"""Configurable route table.

Routing is *configurable first, smart later* (per the ARDOS design). The
operator declares, for each destination or group, a preferred next hop and a
backup:

    Destination/group   Preferred next hop   Backup
    OK1CCC              OK1DDD               OK1EEE
    OSTRAVA-GROUP       OK1BBB               OK1FFF
    REGION-NORTH        OK1GGG               ANY

Later phases can layer heard-stations / signal-quality / busy-status logic on
top, but the manual table is always the fallback.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..config import config_dir

DEFAULT_ROUTES_PATH = config_dir() / "routes.json"

ANY = "ANY"


@dataclass
class Route:
    destination: str          # callsign or group name
    preferred: str            # next-hop callsign
    backup: str = ""          # backup next-hop ("" or "ANY")
    freq_hz: int = 0          # existing control/direct-QSY frequency
    mode: str = ""            # mode at the existing control frequency
    # Optional payload-only channel.  It is ignored unless the station enables
    # separate working channels, so old route files retain their exact meaning.
    working_freq_hz: int = 0
    working_mode: str = ""

    def normalised(self) -> "Route":
        return Route(
            destination=self.destination.strip().upper(),
            preferred=self.preferred.strip().upper(),
            backup=self.backup.strip().upper(),
            freq_hz=int(self.freq_hz or 0),
            mode=(self.mode or "").strip().upper(),
            working_freq_hz=int(self.working_freq_hz or 0),
            working_mode=(self.working_mode or "").strip().upper(),
        )


class RouteTable:
    def __init__(self, routes: list[Route] | None = None):
        self._routes: list[Route] = [r.normalised() for r in (routes or [])]

    def __iter__(self):
        return iter(self._routes)

    def __len__(self):
        return len(self._routes)

    @property
    def routes(self) -> list[Route]:
        return list(self._routes)

    def add(self, route: Route) -> None:
        route = route.normalised()
        # Replace an existing route for the same destination.
        self._routes = [r for r in self._routes if r.destination != route.destination]
        self._routes.append(route)

    def remove(self, destination: str) -> None:
        dest = destination.strip().upper()
        self._routes = [r for r in self._routes if r.destination != dest]

    def freq_for(self, callsign: str) -> tuple[int, str] | None:
        """Return (freq_hz, mode) configured for a station, if any (for QSY)."""
        call = callsign.strip().upper()
        for r in self._routes:
            if r.destination == call and r.freq_hz:
                return r.freq_hz, r.mode
        return None

    def working_for(self, callsign: str) -> tuple[int, str] | None:
        """Return the opt-in payload channel configured for a direct peer."""
        call = callsign.strip().upper()
        for route in self._routes:
            if route.destination == call and route.working_freq_hz:
                return route.working_freq_hz, route.working_mode
        return None

    def frequencies(self) -> list[tuple[int, str]]:
        """Every distinct control/net frequency the table knows, in table order.

        The operator enters these per destination, but they are also the only
        record Guardian has of *where the net lives*: an alert sweep repeats a
        broadcast on each of them so it reaches the stations that are not
        listening where we happen to be tuned.
        """
        seen: set[int] = set()
        out: list[tuple[int, str]] = []
        for r in self._routes:
            if r.freq_hz and r.freq_hz not in seen:
                seen.add(r.freq_hz)
                out.append((r.freq_hz, r.mode))
        return out

    def lookup(self, destination: str) -> Route | None:
        """Find the configured route for a destination/group."""
        dest = destination.strip().upper()
        for r in self._routes:
            if r.destination == dest:
                return r
        return None

    def next_hop(self, destination: str, *, use_backup: bool = False) -> str | None:
        """Resolve the next hop for a destination.

        Returns the preferred hop, or the backup when use_backup is set. A
        backup of "ANY" means "broadcast a ROUTE_QUERY and let anyone answer".
        """
        route = self.lookup(destination)
        if route is None:
            return None
        hop = route.backup if use_backup else route.preferred
        return hop or None

    # --- persistence -----------------------------------------------------
    @classmethod
    def load(cls, path: Path | str | None = None) -> "RouteTable":
        path = Path(path) if path else DEFAULT_ROUTES_PATH
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        routes = [Route(**r) for r in data.get("routes", []) if "destination" in r]
        return cls(routes)

    def save(self, path: Path | str | None = None) -> Path:
        path = Path(path) if path else DEFAULT_ROUTES_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"routes": [asdict(r) for r in self._routes]}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
