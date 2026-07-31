"""Raster map tiles: where they come from, and how they stay on disk.

The source is ČÚZK's WMTS, which the Survey Office publishes "zdarma a bez
registrace" and whose data has been CC BY 4.0 since November 2023. That is
the reason this exists at all: OpenStreetMap's own tile servers forbid the
prefetching an offline map needs, so pointing Guardian at them would have
been a licence breach dressed as a feature.

Tiles are fetched only for what is on screen -- no bulk download of regions
the operator never looks at -- and every one that arrives is kept in a
SQLite file under the station's data directory. A station that opened the
map at home therefore still has that ground in the field, with no network,
which is the case Guardian exists for.
"""

from __future__ import annotations

import math
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from ..config import config_dir

TILE_PIXELS = 256


@dataclass(frozen=True)
class TileSource:
    key: str                 # cache table/file name
    label: str
    url: str                 # format with {z} {x} {y}
    attribution: str
    max_zoom: int = 16

    def tile_url(self, zoom: int, x: int, y: int) -> str:
        return self.url.format(z=zoom, x=x, y=y)


# Základní topografické mapy ČR, Google scale series (EPSG:3857), so the
# tiles line up with the standard slippy-map grid the canvas draws in.
CUZK_ZTM = TileSource(
    key="cuzk-ztm",
    label="ČÚZK — Základní topografické mapy ČR",
    url=(
        "https://ags.cuzk.gov.cz/arcgis1/rest/services/ZTM_WM/MapServer"
        "/tile/{z}/{y}/{x}"
    ),
    attribution="© ČÚZK (CC BY 4.0)",
    max_zoom=16,
)

SOURCES: tuple[TileSource, ...] = (CUZK_ZTM,)


def tile_for(latitude: float, longitude: float, zoom: int) -> tuple[int, int]:
    """Slippy-map tile containing a position."""
    count = 2 ** zoom
    x = int((longitude + 180.0) / 360.0 * count)
    latitude = min(max(latitude, -85.05112878), 85.05112878)
    y = int(
        (1.0 - math.asinh(math.tan(math.radians(latitude))) / math.pi) / 2.0 * count
    )
    return min(max(x, 0), count - 1), min(max(y, 0), count - 1)


class TileCache:
    """Every tile we have ever displayed, kept for the day the net is gone."""

    def __init__(self, source: TileSource, directory: Path | None = None) -> None:
        self.source = source
        base = directory or (config_dir() / "maps")
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / f"{source.key}.sqlite"
        # Qt calls in from its own thread on reply; SQLite objects are not
        # shareable across threads without this, and the lock keeps writes
        # from interleaving.
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS tiles ("
            "  zoom INTEGER, x INTEGER, y INTEGER, image BLOB,"
            "  PRIMARY KEY (zoom, x, y))"
        )
        self._db.commit()

    def get(self, zoom: int, x: int, y: int) -> bytes | None:
        with self._lock:
            row = self._db.execute(
                "SELECT image FROM tiles WHERE zoom=? AND x=? AND y=?", (zoom, x, y)
            ).fetchone()
        return bytes(row[0]) if row else None

    def put(self, zoom: int, x: int, y: int, image: bytes) -> None:
        if not image:
            return
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO tiles (zoom, x, y, image) VALUES (?,?,?,?)",
                (zoom, x, y, sqlite3.Binary(image)),
            )
            self._db.commit()

    def count(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM tiles").fetchone()[0])

    def megabytes(self) -> float:
        return self.path.stat().st_size / 1_048_576 if self.path.exists() else 0.0

    def close(self) -> None:
        with self._lock:
            self._db.close()
