"""Raster map tiles: where they come from, and how they stay on disk.

The source is ČÚZK's WMTS, which the Survey Office publishes "zdarma a bez
registrace" and whose data has been CC BY 4.0 since November 2023. That is
the reason this exists at all: OpenStreetMap's own tile servers forbid the
prefetching an offline map needs, so pointing Guardian at them would have
been a licence breach dressed as a feature.

Tiles viewed on screen are cached automatically. The operator may also ask
Guardian to save the *currently visible* ČÚZK area at a bounded set of zoom
levels. That deliberate job is previewed, capped and cancellable; Guardian
never crawls an unseen region. Every tile that arrives is kept in a SQLite
file under the station's data directory so the same ground remains available
in the field with no network, which is the case Guardian exists for.
"""

from __future__ import annotations

import math
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from ..config import config_dir

TILE_PIXELS = 256
MAX_CACHE_MEGABYTES = 512.0


@dataclass(frozen=True)
class TileSource:
    key: str                 # cache table/file name
    label: str
    url: str                 # format with {z} {x} {y}
    attribution: str
    max_zoom: int = 16
    # Optional coverage in south, west, north, east order. Bulk offline work is
    # clipped to it instead of asking a regional provider for the whole world.
    bounds: tuple[float, float, float, float] | None = None

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
    bounds=(48.4, 11.8, 51.2, 19.1),
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


def tiles_for_bounds(
    south: float,
    west: float,
    north: float,
    east: float,
    minimum_zoom: int,
    maximum_zoom: int,
    *,
    source: TileSource | None = None,
    limit: int = 750,
) -> list[tuple[int, int, int]]:
    """Plan an inclusive slippy-tile download, clipped and size-limited."""
    if minimum_zoom < 0 or maximum_zoom < minimum_zoom:
        raise ValueError("invalid zoom range")
    if source is not None:
        maximum_zoom = min(maximum_zoom, source.max_zoom)
        if source.bounds is not None:
            bound_south, bound_west, bound_north, bound_east = source.bounds
            south, west = max(south, bound_south), max(west, bound_west)
            north, east = min(north, bound_north), min(east, bound_east)
    south, north = max(-85.05112878, south), min(85.05112878, north)
    west, east = max(-180.0, west), min(180.0, east)
    if south >= north or west >= east:
        return []
    keys: list[tuple[int, int, int]] = []
    for zoom in range(minimum_zoom, maximum_zoom + 1):
        first_x, first_y = tile_for(north, west, zoom)
        last_x, last_y = tile_for(south, east, zoom)
        for x in range(first_x, last_x + 1):
            for y in range(first_y, last_y + 1):
                keys.append((zoom, x, y))
                if len(keys) > limit:
                    raise ValueError(f"tile plan exceeds {limit}")
    return keys


class TileCache:
    """Every tile we have ever displayed, kept for the day the net is gone."""

    def __init__(
        self,
        source: TileSource,
        directory: Path | None = None,
        *,
        max_megabytes: float = MAX_CACHE_MEGABYTES,
    ) -> None:
        self.source = source
        self.max_megabytes = float(max_megabytes)
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

    def contains(self, zoom: int, x: int, y: int) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM tiles WHERE zoom=? AND x=? AND y=?", (zoom, x, y)
            ).fetchone()
        return row is not None

    def put(self, zoom: int, x: int, y: int, image: bytes) -> bool:
        if not image:
            return False
        with self._lock:
            exists = self._db.execute(
                "SELECT 1 FROM tiles WHERE zoom=? AND x=? AND y=?", (zoom, x, y)
            ).fetchone()
            projected = self.megabytes() + len(image) / 1_048_576
            if not exists and projected > self.max_megabytes:
                return False
            self._db.execute(
                "INSERT OR REPLACE INTO tiles (zoom, x, y, image) VALUES (?,?,?,?)",
                (zoom, x, y, sqlite3.Binary(image)),
            )
            self._db.commit()
        return True

    def count(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM tiles").fetchone()[0])

    def megabytes(self) -> float:
        return self.path.stat().st_size / 1_048_576 if self.path.exists() else 0.0

    def close(self) -> None:
        with self._lock:
            self._db.close()
