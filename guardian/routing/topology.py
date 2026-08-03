"""Shared network topology and deterministic per-station route derivation.

A route is local: ``S1 via N1`` is correct at S6 but wrong at N2.  A link is
network-wide evidence.  This module persists the shared links and derives the
ordinary :class:`Route` rows Guardian already knows how to use, so no on-air
frame or session contract changes.
"""

from __future__ import annotations

import heapq
import itertools
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..config import config_dir
from .route_table import Route


DEFAULT_TOPOLOGY_PATH = config_dir() / "topology.json"
DIRECTIONS = ("both", "a_to_b", "b_to_a")


@dataclass(frozen=True)
class Link:
    station_a: str
    station_b: str
    direction: str = "both"
    freq_hz: int = 0
    mode: str = ""
    working_freq_hz: int = 0
    working_mode: str = ""
    cost: float = 1.0
    enabled: bool = True

    def normalised(self) -> "Link":
        direction = (self.direction or "both").strip().lower()
        if direction not in DIRECTIONS:
            direction = "both"
        try:
            cost = float(self.cost)
        except (TypeError, ValueError):
            cost = 1.0
        return Link(
            station_a=(self.station_a or "").strip().upper(),
            station_b=(self.station_b or "").strip().upper(),
            direction=direction,
            freq_hz=max(0, int(self.freq_hz or 0)),
            mode=(self.mode or "").strip().upper(),
            working_freq_hz=max(0, int(self.working_freq_hz or 0)),
            working_mode=(self.working_mode or "").strip().upper(),
            cost=cost if cost > 0 else 1.0,
            enabled=bool(self.enabled),
        )

    @property
    def key(self) -> tuple[str, str, str]:
        link = self.normalised()
        if link.direction == "both" and link.station_b < link.station_a:
            return link.station_b, link.station_a, link.direction
        return link.station_a, link.station_b, link.direction

    def problems(self) -> list[str]:
        link = self.normalised()
        out: list[str] = []
        if not link.station_a or not link.station_b:
            out.append("both stations are required")
        elif link.station_a == link.station_b:
            out.append("a station cannot link to itself")
        return out


class Topology:
    def __init__(self, links: list[Link] | None = None):
        self._links: list[Link] = []
        for link in links or []:
            self.add(link)

    @property
    def links(self) -> list[Link]:
        return list(self._links)

    @property
    def nodes(self) -> set[str]:
        return {
            station
            for link in self._links
            for station in (link.station_a, link.station_b)
            if station
        }

    def add(self, link: Link) -> None:
        link = link.normalised()
        if link.problems():
            raise ValueError("; ".join(link.problems()))
        self._links = [existing for existing in self._links if existing.key != link.key]
        self._links.append(link)

    def remove(self, key: tuple[str, str, str]) -> None:
        self._links = [link for link in self._links if link.key != key]

    def clear(self) -> None:
        self._links.clear()

    def _adjacency(self) -> dict[str, list[tuple[str, Link]]]:
        adjacent: dict[str, list[tuple[str, Link]]] = {}
        for link in self._links:
            if not link.enabled:
                continue
            if link.direction in ("both", "a_to_b"):
                adjacent.setdefault(link.station_a, []).append((link.station_b, link))
            if link.direction in ("both", "b_to_a"):
                adjacent.setdefault(link.station_b, []).append((link.station_a, link))
        for edges in adjacent.values():
            edges.sort(key=lambda edge: (edge[0], edge[1].cost, edge[1].key))
        return adjacent

    def _shortest_path(
        self,
        start: str,
        destination: str,
        *,
        blocked_first_hop: str = "",
    ) -> tuple[tuple[str, ...], Link] | None:
        adjacent = self._adjacency()
        serial = itertools.count()
        queue: list[tuple[float, int, tuple[str, ...], int, str, Link | None]] = [
            (0.0, 0, (start,), next(serial), start, None)
        ]
        best: dict[str, tuple[float, int, tuple[str, ...]]] = {}
        while queue:
            cost, hops, path, _serial, node, first_link = heapq.heappop(queue)
            rank = (cost, hops, path)
            if node in best and best[node] <= rank:
                continue
            best[node] = rank
            if node == destination and first_link is not None:
                return path, first_link
            for neighbour, link in adjacent.get(node, []):
                if neighbour in path:
                    continue
                if node == start and neighbour == blocked_first_hop:
                    continue
                heapq.heappush(
                    queue,
                    (
                        cost + link.cost,
                        hops + 1,
                        path + (neighbour,),
                        next(serial),
                        neighbour,
                        first_link or link,
                    ),
                )
        return None

    def derive_routes(self, own_callsign: str) -> list[Route]:
        """Build effective routes from ``own_callsign`` using cost then hops."""
        own = (own_callsign or "").strip().upper()
        if not own or own not in self.nodes:
            return []
        routes: list[Route] = []
        for destination in sorted(self.nodes - {own}):
            primary = self._shortest_path(own, destination)
            if primary is None:
                continue
            path, first_link = primary
            first_hop = path[1]
            alternate = self._shortest_path(
                own,
                destination,
                blocked_first_hop=first_hop,
            )
            backup = alternate[0][1] if alternate is not None else ""
            routes.append(
                Route(
                    destination=destination,
                    preferred="" if len(path) == 2 else first_hop,
                    backup=backup,
                    freq_hz=first_link.freq_hz,
                    mode=first_link.mode,
                    working_freq_hz=first_link.working_freq_hz,
                    working_mode=first_link.working_mode,
                    source="topology",
                ).normalised()
            )
        return routes

    def warnings(self, own_callsign: str, heard: set[str] | None = None) -> list[str]:
        own = (own_callsign or "").strip().upper()
        if not own:
            return ["station callsign is not configured"]
        if own not in self.nodes:
            return [f"{own} is not present in the topology"]
        routes = self.derive_routes(own)
        reachable = {route.destination for route in routes}
        warnings = [
            f"{node} is not reachable from {own}"
            for node in sorted(self.nodes - reachable - {own})
        ]
        if heard is not None:
            heard = {call.strip().upper() for call in heard}
            next_hops = {
                route.preferred or route.destination
                for route in routes
            }
            warnings.extend(
                f"next hop {hop} has not been heard by {own}"
                for hop in sorted(next_hops - heard)
            )
        return warnings

    def save(self, path: Path | str | None = None) -> Path:
        path = Path(path) if path else DEFAULT_TOPOLOGY_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "links": [asdict(link) for link in self._links]}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Topology":
        path = Path(path) if path else DEFAULT_TOPOLOGY_PATH
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            links = [Link(**item) for item in data.get("links", [])]
            return cls(links)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return cls()
