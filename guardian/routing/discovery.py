"""Bounded multi-hop route discovery and volatile learned routes.

The discovery plane deliberately stays separate from the operator's manual
routes and the imported topology.  Its evidence expires, is discarded on a
restart and cannot silently overwrite an operator decision.

Wire use for the two discovery-only frame types keeps protocol version 1:

* RREQ: source=current transmitter, destination=target, next_hop=origin
* RREP: source=current transmitter, destination=origin, next_hop=previous hop

The message id identifies the query.  For discovery frames only, the flags
byte carries a compact metric: high nibble is hop count, low nibble is the
accumulated link-quality penalty.  TTL remains a real relay budget.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import time
import zlib
from typing import Callable

from ..protocol import (
    MAX_CONTROL_FRAME_BYTES,
    ControlFrame,
    Flags,
    FrameType,
    Priority,
)

DISCOVERY_OFF = "off"
DISCOVERY_ASSISTED = "assisted"
DISCOVERY_MODES = (DISCOVERY_OFF, DISCOVERY_ASSISTED)

# 0.6.58 and earlier also offered a receive-only "monitor" mode. A station in it
# recorded breadcrumbs it could never answer with an RREP, never returned a
# route to its own operator and never appeared to anybody else, so the operator
# was left looking at empty tables with nothing explaining why. There is no
# useful third position between "do not take part" and "take part": a profile
# that still selects it is read as assisted.
_RETIRED_MODES = {"monitor": DISCOVERY_ASSISTED}


def normalize_discovery_mode(mode: str | None) -> str:
    """Read any stored or supplied value as one of the two supported modes.

    Anything unrecognised becomes ``off``: a typo must never be the reason a
    station starts keying the transmitter.
    """
    value = str(mode or "").strip().lower()
    value = _RETIRED_MODES.get(value, value)
    return value if value in DISCOVERY_MODES else DISCOVERY_OFF

MIN_DISCOVERY_TTL = 2
MAX_DISCOVERY_TTL = 8
DEFAULT_ROUTE_LIFETIME = 1800.0
DEFAULT_QUERY_TIMEOUT = 12.0
DEFAULT_SETTLE_TIME = 2.0
DEFAULT_FRAME_BUDGET = 12
DISCOVERY_MEMORY = 180.0
RREP_REPEAT_GAP = 2.0
DEFAULT_LINK_ADVERT_INTERVAL = 900.0
MIN_LINK_ADVERT_INTERVAL = 60.0
LINK_ADVERT_MEMORY = 3600.0


def encode_discovery_metric(hops: int, penalty: int) -> Flags:
    """Pack a bounded (hop count, quality penalty) metric into one byte."""
    return Flags((min(15, max(0, int(hops))) << 4) | min(15, max(0, int(penalty))))


def decode_discovery_metric(flags: Flags | int) -> tuple[int, int]:
    value = int(flags) & 0xFF
    return (value >> 4) & 0x0F, value & 0x0F


def snr_penalty(snr: float | None) -> int:
    """Small, deliberately coarse cost; missing S/N is neutral, not fatal."""
    if snr is None:
        return 1
    if snr >= 10:
        return 0
    if snr >= 0:
        return 1
    if snr >= -8:
        return 2
    return 3


@dataclass
class DynamicRoute:
    destination: str
    next_hop: str
    hops: int
    penalty: int
    learned_at: float
    expires_at: float
    query_id: int
    source: str = "rreq"
    approved: bool = False
    auto_approved: bool = False
    last_success: float | None = None
    failures: int = 0

    def active(self, now: float) -> bool:
        return now < self.expires_at

    @property
    def metric(self) -> int:
        return self.hops * 16 + self.penalty + self.failures * 32


class DynamicRouteStore:
    """Runtime-only routes learned from proven RREP return paths."""

    def __init__(self, lifetime: float = DEFAULT_ROUTE_LIFETIME) -> None:
        self.lifetime = max(1.0, float(lifetime))
        self._routes: dict[tuple[str, str, str], DynamicRoute] = {}

    def learn(
        self,
        destination: str,
        next_hop: str,
        hops: int,
        penalty: int,
        query_id: int,
        now: float,
        *,
        source: str = "rreq",
        approved: bool = False,
    ) -> DynamicRoute:
        route = DynamicRoute(
            destination.strip().upper(),
            next_hop.strip().upper(),
            max(1, int(hops)),
            max(0, int(penalty)),
            float(now),
            float(now) + self.lifetime,
            int(query_id) & 0xFFFFFFFF,
            source.strip().lower() or "rreq",
            bool(approved),
            bool(approved),
        )
        key = (route.source, route.destination, route.next_hop)
        old = self._routes.get(key)
        if old is not None and old.active(now):
            route.approved = old.approved
            route.auto_approved = old.auto_approved
            route.last_success = old.last_success
            route.failures = old.failures
        self._routes[key] = route
        return route

    def replace_source(
        self,
        source: str,
        routes: list[tuple],
        now: float,
        *,
        approved: bool = False,
    ) -> None:
        """Atomically replace one volatile route source, preserving approvals."""
        route_source = source.strip().lower()
        old = {
            key: route
            for key, route in self._routes.items()
            if route.source == route_source
        }
        self._routes = {
            key: route
            for key, route in self._routes.items()
            if route.source != route_source
        }
        for route_data in routes:
            destination, next_hop, hops, penalty, query_id = route_data[:5]
            evidence_expires = route_data[5] if len(route_data) > 5 else None
            key = (route_source, destination.strip().upper(), next_hop.strip().upper())
            previous = old.get(key)
            preserve_approval = previous is not None and previous.active(now)
            was_approved = previous.approved if preserve_approval else False
            learned = self.learn(
                destination,
                next_hop,
                hops,
                penalty,
                query_id,
                now,
                source=route_source,
                approved=approved or was_approved,
            )
            if evidence_expires is not None:
                learned.expires_at = min(learned.expires_at, float(evidence_expires))
            current = self._routes.get(key)
            if current is not None and preserve_approval:
                if previous.approved and not previous.auto_approved:
                    current.approved = True
                    current.auto_approved = False
                elif not approved:
                    current.auto_approved = previous.auto_approved

    def routes(self, now: float, *, include_expired: bool = False) -> list[DynamicRoute]:
        values = list(self._routes.values())
        if not include_expired:
            values = [route for route in values if route.active(now)]
        return sorted(values, key=lambda route: (route.destination, route.metric, route.next_hop))

    def best(
        self,
        destination: str,
        now: float,
        *,
        approved_only: bool = False,
    ) -> DynamicRoute | None:
        dest = destination.strip().upper()
        candidates = [
            route
            for route in self._routes.values()
            if route.destination == dest
            and route.active(now)
            and (route.approved or not approved_only)
        ]
        return min(
            candidates,
            key=lambda route: (route.metric, -route.learned_at, route.next_hop),
            default=None,
        )

    def approve(
        self, destination: str, now: float, *, automatic: bool = False
    ) -> DynamicRoute | None:
        route = self.best(destination, now)
        if route is not None:
            for candidate in self._routes.values():
                if candidate.destination == route.destination:
                    candidate.approved = candidate is route
                    candidate.auto_approved = candidate is route and automatic
        return route

    def clear(self) -> None:
        self._routes.clear()

    def prune(self, now: float) -> None:
        # Keep a short read-only tombstone for the operator view while making
        # `best()` reject it immediately at expiry.
        retention = max(300.0, min(self.lifetime, 3600.0))
        self._routes = {
            key: route
            for key, route in self._routes.items()
            if now < route.expires_at + retention
        }

    def mark_success(self, destination: str, next_hop: str, now: float) -> None:
        destination = destination.strip().upper()
        next_hop = next_hop.strip().upper()
        for route in self._routes.values():
            if route.destination != destination or route.next_hop != next_hop:
                continue
            route.last_success = now
            route.failures = 0
            if route.source != "link-advert":
                route.expires_at = max(route.expires_at, now + self.lifetime)

    def mark_failure(self, destination: str, next_hop: str) -> None:
        destination = destination.strip().upper()
        next_hop = next_hop.strip().upper()
        for route in self._routes.values():
            if route.destination != destination or route.next_hop != next_hop:
                continue
            route.failures += 1
            route.approved = False
            route.auto_approved = False


@dataclass
class ObservedLink:
    """Directed evidence that ``owner`` recently heard ``neighbor``."""

    owner: str
    neighbor: str
    learned_at: float
    expires_at: float
    penalty: int
    advert_id: int
    last_sender: str

    def active(self, now: float) -> bool:
        return now < self.expires_at


class LiveTopologyStore:
    """Runtime-only LINK_ADVERT evidence and reciprocal route derivation."""

    def __init__(self, lifetime: float = DEFAULT_ROUTE_LIFETIME) -> None:
        self.lifetime = max(1.0, float(lifetime))
        self._links: dict[tuple[str, str], ObservedLink] = {}

    def record(
        self,
        owner: str,
        neighbor: str,
        now: float,
        *,
        penalty: int,
        advert_id: int,
        last_sender: str,
    ) -> ObservedLink | None:
        owner = owner.strip().upper()
        neighbor = neighbor.strip().upper()
        if not owner or not neighbor or owner == neighbor:
            return None
        link = ObservedLink(
            owner,
            neighbor,
            float(now),
            float(now) + self.lifetime,
            min(15, max(0, int(penalty))),
            int(advert_id) & 0xFFFFFFFF,
            last_sender.strip().upper(),
        )
        self._links[(owner, neighbor)] = link
        return link

    def links(self, now: float, *, include_expired: bool = False) -> list[ObservedLink]:
        values = list(self._links.values())
        if not include_expired:
            values = [link for link in values if link.active(now)]
        return sorted(values, key=lambda link: (link.owner, link.neighbor))

    def reciprocal(self, link: ObservedLink, now: float) -> bool:
        reverse = self._links.get((link.neighbor, link.owner))
        return bool(link.active(now) and reverse is not None and reverse.active(now))

    def derive_routes(
        self, callsign: str, now: float
    ) -> list[tuple[str, str, int, int, int, float]]:
        """Dijkstra over links confirmed independently from both directions."""
        origin = callsign.strip().upper()
        graph: dict[str, list[tuple[str, int, int, float]]] = defaultdict(list)
        for link in self.links(now):
            reverse = self._links.get((link.neighbor, link.owner))
            if reverse is None or not reverse.active(now):
                continue
            cost = 16 + max(link.penalty, reverse.penalty)
            advert_id = max(link.advert_id, reverse.advert_id)
            expires_at = min(link.expires_at, reverse.expires_at)
            graph[link.owner].append(
                (link.neighbor, cost, advert_id, expires_at)
            )

        # metric, hops, node, first hop, penalty, advert id, evidence expiry
        queue: list[tuple[int, int, str, str, int, int, float]] = [
            (0, 0, origin, "", 0, 0, float("inf"))
        ]
        best: dict[str, tuple[int, int, str, int, int, float]] = {}
        while queue:
            queue.sort(reverse=True)
            metric, hops, node, first_hop, penalty, advert_id, expiry = queue.pop()
            if node in best and best[node][0] <= metric:
                continue
            best[node] = (metric, hops, first_hop, penalty, advert_id, expiry)
            for neighbor, edge_cost, edge_id, edge_expiry in graph.get(node, []):
                if neighbor == origin:
                    continue
                next_hop = first_hop or neighbor
                edge_penalty = max(0, edge_cost - 16)
                queue.append(
                    (
                        metric + edge_cost,
                        hops + 1,
                        neighbor,
                        next_hop,
                        min(15, penalty + edge_penalty),
                        max(advert_id, edge_id),
                        min(expiry, edge_expiry),
                    )
                )
        return [
            (destination, data[2], data[1], data[3], data[4], data[5])
            for destination, data in best.items()
            if destination != origin and data[2]
        ]

    def clear(self) -> None:
        self._links.clear()

    def prune(self, now: float) -> bool:
        before = len(self._links)
        self._links = {
            key: link
            for key, link in self._links.items()
            if now < link.expires_at + max(300.0, min(self.lifetime, 3600.0))
        }
        return len(self._links) != before


@dataclass
class DiscoveryEvent:
    timestamp: float
    kind: str
    source: str
    destination: str
    detail: str


@dataclass
class PendingQuery:
    query_id: int
    destination: str
    started_at: float
    round_ttl: int
    max_ttl: int
    deadline: float
    context: str = "manual"
    best_route: DynamicRoute | None = None
    settle_at: float | None = None


@dataclass
class Breadcrumb:
    origin: str
    destination: str
    query_id: int
    previous_hop: str
    hops_from_origin: int
    penalty_from_origin: int
    remaining_ttl: int
    seen_at: float

    @property
    def rank(self) -> tuple[int, int, str]:
        return self.hops_from_origin, self.penalty_from_origin, self.previous_hop


@dataclass
class _Scheduled:
    due: float
    kind: str
    key: tuple[str, int, str]
    frame: ControlFrame


class DiscoveryEngine:
    """State machine for bounded RREQ flooding and directed RREP return."""

    def __init__(
        self,
        callsign: str,
        send: Callable[[ControlFrame], None],
        *,
        mode: str = DISCOVERY_OFF,
        forward: bool = False,
        relay_enabled: bool = False,
        max_ttl: int = 4,
        route_lifetime: float = DEFAULT_ROUTE_LIFETIME,
        frame_budget: int = DEFAULT_FRAME_BUDGET,
        query_timeout: float = DEFAULT_QUERY_TIMEOUT,
        settle_time: float = DEFAULT_SETTLE_TIME,
        jitter_min: float = 0.4,
        jitter_max: float = 1.4,
        allowlist: set[str] | None = None,
        denylist: set[str] | None = None,
        auto_use: bool = False,
        link_advert_enabled: bool = False,
        link_advert_interval: float = DEFAULT_LINK_ADVERT_INTERVAL,
        on_event: Callable[[DiscoveryEvent], None] | None = None,
        on_result: Callable[[DynamicRoute, PendingQuery], None] | None = None,
        on_failure: Callable[[PendingQuery, str], None] | None = None,
    ) -> None:
        self.callsign = callsign.strip().upper()
        self.send = send
        self.mode = normalize_discovery_mode(mode)
        self.forward = bool(forward)
        self.relay_enabled = bool(relay_enabled)
        self.max_ttl = min(MAX_DISCOVERY_TTL, max(MIN_DISCOVERY_TTL, int(max_ttl)))
        self.frame_budget = max(1, int(frame_budget))
        self.query_timeout = max(1.0, float(query_timeout))
        self.settle_time = max(0.0, float(settle_time))
        self.jitter_min = max(0.0, float(jitter_min))
        self.jitter_max = max(self.jitter_min, float(jitter_max))
        self.allowlist = {item.strip().upper() for item in (allowlist or set()) if item.strip()}
        self.denylist = {item.strip().upper() for item in (denylist or set()) if item.strip()}
        self.auto_use = bool(auto_use)
        self.link_advert_enabled = bool(link_advert_enabled)
        self.link_advert_interval = max(
            MIN_LINK_ADVERT_INTERVAL, float(link_advert_interval)
        )
        self.on_event = on_event
        self.on_result = on_result
        self.on_failure = on_failure
        self.routes = DynamicRouteStore(route_lifetime)
        self.live_topology = LiveTopologyStore(route_lifetime)
        self.pending: dict[int, PendingQuery] = {}
        self.breadcrumbs: dict[tuple[str, int, str], Breadcrumb] = {}
        self.events: deque[DiscoveryEvent] = deque(maxlen=200)
        self._scheduled: list[_Scheduled] = []
        self._sent: deque[float] = deque()
        self._counter = 0
        self._now = 0.0
        self._advert_seen: dict[tuple[str, int, str], float] = {}
        self._last_advert_at: float | None = None
        self._last_advert_neighbors: frozenset[str] | None = None

    @property
    def can_transmit(self) -> bool:
        return self.mode == DISCOVERY_ASSISTED

    @property
    def automatic_use_active(self) -> bool:
        return self.auto_use and self.can_transmit

    def configure(
        self,
        *,
        callsign: str | None = None,
        mode: str | None = None,
        forward: bool | None = None,
        relay_enabled: bool | None = None,
        max_ttl: int | None = None,
        route_lifetime: float | None = None,
        frame_budget: int | None = None,
        allowlist: set[str] | None = None,
        denylist: set[str] | None = None,
        auto_use: bool | None = None,
        link_advert_enabled: bool | None = None,
        link_advert_interval: float | None = None,
    ) -> None:
        if callsign is not None:
            self.callsign = callsign.strip().upper()
        if mode is not None:
            previous_mode = self.mode
            self.mode = normalize_discovery_mode(mode)
            if previous_mode == DISCOVERY_ASSISTED and not self.can_transmit:
                cancelled = list(self.pending.values())
                self.pending.clear()
                self._scheduled.clear()
                for query in cancelled:
                    self._event(
                        "cancelled",
                        self.callsign,
                        query.destination,
                        "transmit mode disabled",
                    )
                    if self.on_failure is not None:
                        self.on_failure(query, "multi-hop discovery disabled")
        if forward is not None:
            self.forward = bool(forward)
            if not self.forward:
                self._scheduled = [
                    item for item in self._scheduled if item.kind != "RREQ"
                ]
        if relay_enabled is not None:
            self.relay_enabled = bool(relay_enabled)
        if max_ttl is not None:
            self.max_ttl = min(MAX_DISCOVERY_TTL, max(MIN_DISCOVERY_TTL, int(max_ttl)))
        if route_lifetime is not None:
            self.routes.lifetime = max(1.0, float(route_lifetime))
            self.live_topology.lifetime = max(1.0, float(route_lifetime))
        if frame_budget is not None:
            self.frame_budget = max(1, int(frame_budget))
        if allowlist is not None:
            self.allowlist = {item.strip().upper() for item in allowlist if item.strip()}
        if denylist is not None:
            self.denylist = {item.strip().upper() for item in denylist if item.strip()}
        if auto_use is not None:
            self.auto_use = bool(auto_use)
        if link_advert_enabled is not None:
            previous_link_advert = self.link_advert_enabled
            self.link_advert_enabled = bool(link_advert_enabled)
            if not self.link_advert_enabled:
                self._scheduled = [
                    item for item in self._scheduled if item.kind != "LINK-ADVERT"
                ]
                if previous_link_advert:
                    self.live_topology.clear()
                    self.routes.replace_source("link-advert", [], self._now)
                    self._advert_seen.clear()
            elif not previous_link_advert:
                self._last_advert_at = None
                self._last_advert_neighbors = None
        if link_advert_interval is not None:
            self.link_advert_interval = max(
                MIN_LINK_ADVERT_INTERVAL, float(link_advert_interval)
            )
        self._sync_auto_approvals()

    def _sync_auto_approvals(self) -> None:
        active = self.automatic_use_active
        for route in self.routes.routes(self._now, include_expired=True):
            if active and route.active(self._now) and not route.failures:
                if not route.approved:
                    route.approved = True
                    route.auto_approved = True
            elif route.auto_approved:
                route.approved = False
                route.auto_approved = False

    def start(
        self,
        destination: str,
        *,
        query_id: int | None = None,
        context: str = "manual",
        priority: Priority = Priority.ROUTINE,
    ) -> PendingQuery | None:
        dest = destination.strip().upper()
        if not dest or dest == self.callsign or not self.can_transmit:
            return None
        query_id = self._next_id() if query_id is None else int(query_id) & 0xFFFFFFFF
        existing = self.pending.get(query_id)
        if existing is not None:
            return existing
        first_ttl = min(MIN_DISCOVERY_TTL, self.max_ttl)
        pending = PendingQuery(
            query_id,
            dest,
            self._now,
            first_ttl,
            self.max_ttl,
            self._now + self.query_timeout + RREP_REPEAT_GAP,
            context,
        )
        self.pending[query_id] = pending
        self._send_query(pending, priority)
        return pending

    def approve(self, destination: str) -> DynamicRoute | None:
        route = self.routes.approve(destination, self._now)
        if route is not None:
            self._event("approved", self.callsign, route.destination, f"via {route.next_hop}")
        return route

    def clear_routes(self) -> None:
        self.routes.clear()
        self._event("cleared", self.callsign, "", "dynamic routes cleared")

    def clear_live_topology(self) -> None:
        self.live_topology.clear()
        self.routes.replace_source("link-advert", [], self._now)
        self._advert_seen.clear()
        self._last_advert_at = None
        self._last_advert_neighbors = None
        self._event("links-cleared", self.callsign, "", "live topology cleared")

    def advertise_neighbors(
        self,
        neighbors: list[tuple[str, float | None]],
        *,
        force: bool = False,
    ) -> int:
        """Advertise fresh direct observations; return transmitted frame count."""
        if not (self.link_advert_enabled and self.can_transmit):
            return 0
        clean: dict[str, float | None] = {}
        for callsign, snr in neighbors:
            peer = callsign.strip().upper()
            if peer and peer != self.callsign and self._peer_allowed(peer):
                clean[peer] = snr
        neighbor_set = frozenset(clean)
        if (
            not force
            and self._last_advert_at is not None
            and neighbor_set == self._last_advert_neighbors
            and self._now - self._last_advert_at < self.link_advert_interval
        ):
            return 0
        advert_id = self._next_id()
        sent = 0
        if not clean:
            # A one-hop presence advert bootstraps an entirely quiet network.
            # The orchestrator records its physical sender in HeardStations;
            # an empty neighbour is never inserted into the live graph or
            # flooded beyond the receiver.
            presence = ControlFrame(
                type=FrameType.LINK_ADVERT,
                source=self.callsign,
                destination=self.callsign,
                message_id=advert_id,
                ttl=1,
            )
            if self._transmit(presence, "LINK-PRESENCE"):
                sent = 1
            self._last_advert_at = self._now
            self._last_advert_neighbors = neighbor_set
            self._event("advertised-presence", self.callsign, "", "one hop")
            return sent
        for peer in sorted(clean):
            penalty = snr_penalty(clean[peer])
            self.live_topology.record(
                self.callsign,
                peer,
                self._now,
                penalty=penalty,
                advert_id=advert_id,
                last_sender=self.callsign,
            )
            key = (self.callsign, advert_id, peer)
            self._advert_seen[key] = self._now
            frame = ControlFrame(
                type=FrameType.LINK_ADVERT,
                source=self.callsign,
                destination=self.callsign,
                next_hop=peer,
                message_id=advert_id,
                ttl=self.max_ttl,
                flags=encode_discovery_metric(0, penalty),
            )
            if self._transmit(frame, "LINK-ADVERT"):
                sent += 1
        self._last_advert_at = self._now
        self._last_advert_neighbors = neighbor_set
        self._rebuild_live_routes()
        self._event(
            "advertised-links",
            self.callsign,
            "",
            f"{sent}/{len(clean)} neighbours",
        )
        return sent

    def receive(self, frame: ControlFrame, *, snr: float | None = None) -> bool:
        if frame.type is FrameType.MULTIHOP_RREQ:
            if self.mode == DISCOVERY_OFF:
                return True
            self._receive_rreq(frame, snr)
            return True
        if frame.type is FrameType.MULTIHOP_RREP:
            if self.mode == DISCOVERY_OFF:
                return True
            self._receive_rrep(frame)
            return True
        if frame.type is FrameType.LINK_ADVERT:
            if not self.link_advert_enabled or self.mode == DISCOVERY_OFF:
                return True
            self._receive_link_advert(frame)
            return True
        return False

    def tick(self, now: float) -> None:
        self._now = float(now)
        self._prune()
        due = [item for item in self._scheduled if item.due <= self._now]
        self._scheduled = [item for item in self._scheduled if item.due > self._now]
        for item in sorted(due, key=lambda value: (value.due, value.kind, value.key)):
            self._transmit(item.frame, item.kind)

        for query_id, pending in list(self.pending.items()):
            if pending.best_route is not None and pending.settle_at is not None:
                if self._now >= pending.settle_at:
                    self.pending.pop(query_id, None)
                    if self.automatic_use_active:
                        self.routes.approve(
                            pending.destination, self._now, automatic=True
                        )
                    self._event(
                        "found",
                        pending.best_route.next_hop,
                        pending.destination,
                        f"{pending.best_route.hops} hops, penalty {pending.best_route.penalty}",
                    )
                    if self.on_result is not None:
                        self.on_result(pending.best_route, pending)
                continue
            if self._now < pending.deadline:
                continue
            if pending.round_ttl < pending.max_ttl:
                pending.round_ttl = min(pending.max_ttl, pending.round_ttl + 2)
                pending.deadline = self._now + self.query_timeout + RREP_REPEAT_GAP
                self._event("expanded", self.callsign, pending.destination, f"TTL {pending.round_ttl}")
                self._send_query(pending, Priority.ROUTINE)
            else:
                self.pending.pop(query_id, None)
                self._event("failed", self.callsign, pending.destination, "no RREP")
                if self.on_failure is not None:
                    self.on_failure(pending, "no multi-hop route reply")

    def _receive_link_advert(self, frame: ControlFrame) -> None:
        owner = frame.destination.strip().upper()
        neighbor = frame.next_hop.strip().upper()
        sender = frame.source.strip().upper()
        if not owner or not sender or owner == self.callsign or not self._peer_allowed(sender):
            return
        if not neighbor:
            self._event("heard-presence", owner, "", f"from {sender}")
            return
        if owner == neighbor:
            return
        key = (owner, frame.message_id, neighbor)
        if key in self._advert_seen:
            return
        self._advert_seen[key] = self._now
        _hops, penalty = decode_discovery_metric(frame.flags)
        self.live_topology.record(
            owner,
            neighbor,
            self._now,
            penalty=penalty,
            advert_id=frame.message_id,
            last_sender=sender,
        )
        self._rebuild_live_routes()
        self._event("heard-link", owner, neighbor, f"via {sender}, TTL {frame.ttl}")
        if not (
            self.can_transmit
            and self.forward
            and self.relay_enabled
            and frame.ttl > 1
        ):
            return
        onward = ControlFrame(
            type=FrameType.LINK_ADVERT,
            source=self.callsign,
            destination=owner,
            next_hop=neighbor,
            message_id=frame.message_id,
            priority=frame.priority,
            ttl=frame.ttl - 1,
            flags=frame.flags,
        )
        self._schedule("LINK-ADVERT", key, onward)

    def _rebuild_live_routes(self) -> None:
        routes = self.live_topology.derive_routes(self.callsign, self._now)
        self.routes.replace_source(
            "link-advert",
            routes,
            self._now,
            approved=self.automatic_use_active,
        )

    def _send_query(self, pending: PendingQuery, priority: Priority) -> None:
        frame = ControlFrame(
            type=FrameType.MULTIHOP_RREQ,
            source=self.callsign,
            destination=pending.destination,
            next_hop=self.callsign,
            message_id=pending.query_id,
            priority=priority,
            ttl=pending.round_ttl,
            flags=encode_discovery_metric(0, 0),
        )
        self._transmit(frame, "RREQ")
        self._event("query", self.callsign, pending.destination, f"TTL {pending.round_ttl}")

    def _receive_rreq(self, frame: ControlFrame, snr: float | None) -> None:
        origin = frame.next_hop.strip().upper()
        target = frame.destination.strip().upper()
        previous = frame.source.strip().upper()
        if not origin or not target or not previous or origin == self.callsign:
            return
        self._event("heard-rreq", previous, target, f"origin {origin}, TTL {frame.ttl}")
        if not self._peer_allowed(previous):
            return
        hops, penalty = decode_discovery_metric(frame.flags)
        candidate = Breadcrumb(
            origin,
            target,
            frame.message_id,
            previous,
            min(15, hops + 1),
            min(15, penalty + snr_penalty(snr)),
            int(frame.ttl),
            self._now,
        )
        key = (origin, frame.message_id, target)
        old = self.breadcrumbs.get(key)
        better = old is None or candidate.rank < old.rank
        expanded = old is not None and candidate.remaining_ttl > old.remaining_ttl
        if not better and not expanded:
            return
        if better:
            self.breadcrumbs[key] = candidate
        else:
            old.remaining_ttl = candidate.remaining_ttl
            old.seen_at = self._now
            candidate = old
        if not self.can_transmit:
            return
        metric = encode_discovery_metric(
            candidate.hops_from_origin, candidate.penalty_from_origin
        )
        if target == self.callsign:
            reply = ControlFrame(
                type=FrameType.MULTIHOP_RREP,
                source=self.callsign,
                destination=origin,
                next_hop=candidate.previous_hop,
                message_id=frame.message_id,
                priority=frame.priority,
                ttl=max(1, candidate.hops_from_origin),
                flags=metric,
            )
            self._schedule("RREP", key, reply)
            # A reply is directed and cheap compared with repeating the whole
            # flood. One bounded duplicate lets a single lost RREP recover
            # without inventing an acknowledgement protocol for control bursts.
            self._scheduled.append(
                _Scheduled(
                    self._now + self._jitter(key, "RREP") + RREP_REPEAT_GAP,
                    "RREP-RETRY",
                    key,
                    reply,
                )
            )
            return
        if not (self.forward and self.relay_enabled) or frame.ttl <= 1:
            return
        onward = ControlFrame(
            type=FrameType.MULTIHOP_RREQ,
            source=self.callsign,
            destination=target,
            next_hop=origin,
            message_id=frame.message_id,
            priority=frame.priority,
            ttl=frame.ttl - 1,
            flags=metric,
        )
        self._schedule("RREQ", key, onward)

    def _receive_rrep(self, frame: ControlFrame) -> None:
        if frame.destination.strip().upper() != self.callsign and frame.next_hop.strip().upper() != self.callsign:
            return
        if frame.next_hop.strip().upper() != self.callsign:
            return
        origin = frame.destination.strip().upper()
        if not self._peer_allowed(frame.source):
            return
        total_hops, total_penalty = decode_discovery_metric(frame.flags)
        pending = self.pending.get(frame.message_id)
        if origin == self.callsign and pending is not None:
            route = self.routes.learn(
                pending.destination,
                frame.source,
                total_hops,
                total_penalty,
                frame.message_id,
                self._now,
            )
            if pending.best_route is None or route.metric < pending.best_route.metric:
                pending.best_route = route
            pending.settle_at = self._now + self.settle_time
            self._event("heard-rrep", frame.source, pending.destination, f"{total_hops} hops")
            return
        matches = [
            breadcrumb
            for key, breadcrumb in self.breadcrumbs.items()
            if key[0] == origin and key[1] == frame.message_id
        ]
        if not matches:
            return
        breadcrumb = min(matches, key=lambda item: item.rank)
        remaining_hops = max(1, total_hops - breadcrumb.hops_from_origin)
        remaining_penalty = max(0, total_penalty - breadcrumb.penalty_from_origin)
        self.routes.learn(
            breadcrumb.destination,
            frame.source,
            remaining_hops,
            remaining_penalty,
            frame.message_id,
            self._now,
        )
        self._event("relay-rrep", frame.source, breadcrumb.destination, f"to {breadcrumb.previous_hop}")
        if not (self.can_transmit and self.forward and self.relay_enabled) or frame.ttl <= 1:
            return
        onward = ControlFrame(
            type=FrameType.MULTIHOP_RREP,
            source=self.callsign,
            destination=origin,
            next_hop=breadcrumb.previous_hop,
            message_id=frame.message_id,
            priority=frame.priority,
            ttl=frame.ttl - 1,
            flags=frame.flags,
        )
        self._transmit(onward, "RREP")

    def _schedule(
        self,
        kind: str,
        key: tuple[str, int, str],
        frame: ControlFrame,
    ) -> None:
        self._scheduled = [
            item
            for item in self._scheduled
            if not (
                item.key == key
                and (
                    item.kind == kind
                    or (kind == "RREP" and item.kind.startswith("RREP"))
                )
            )
        ]
        self._scheduled.append(
            _Scheduled(self._now + self._jitter(key, kind), kind, key, frame)
        )

    def _transmit(self, frame: ControlFrame, kind: str) -> bool:
        if not self.can_transmit:
            self._event("suppressed", self.callsign, frame.destination, f"{kind} in {self.mode} mode")
            return False
        if len(frame.encode()) > MAX_CONTROL_FRAME_BYTES:
            self._event(
                "oversize",
                self.callsign,
                frame.destination,
                f"{kind} exceeds {MAX_CONTROL_FRAME_BYTES} bytes",
            )
            return False
        cutoff = self._now - 60.0
        while self._sent and self._sent[0] <= cutoff:
            self._sent.popleft()
        if len(self._sent) >= self.frame_budget:
            self._event("limited", self.callsign, frame.destination, f"{kind} airtime budget")
            return False
        self.send(frame)
        self._sent.append(self._now)
        return True

    def _peer_allowed(self, callsign: str) -> bool:
        peer = callsign.strip().upper()
        if peer in self.denylist:
            return False
        return not self.allowlist or peer in self.allowlist

    def _jitter(self, key: tuple[str, int, str], kind: str) -> float:
        if self.jitter_max <= self.jitter_min:
            return self.jitter_min
        seed = f"{key[0]}:{key[1]}:{key[2]}:{self.callsign}:{kind}".encode("ascii", "replace")
        fraction = (zlib.crc32(seed) & 0xFFFFFFFF) / 0xFFFFFFFF
        return self.jitter_min + fraction * (self.jitter_max - self.jitter_min)

    def _next_id(self) -> int:
        self._counter = (self._counter + 1) & 0xFFFF
        station = zlib.crc32(self.callsign.encode("ascii", "replace")) & 0xFFFF
        return ((station << 16) | self._counter) & 0xFFFFFFFF

    def _event(self, kind: str, source: str, destination: str, detail: str) -> None:
        event = DiscoveryEvent(self._now, kind, source, destination, detail)
        self.events.appendleft(event)
        if self.on_event is not None:
            self.on_event(event)

    def _prune(self) -> None:
        self.routes.prune(self._now)
        links_changed = self.live_topology.prune(self._now)
        self._advert_seen = {
            key: seen
            for key, seen in self._advert_seen.items()
            if self._now - seen < LINK_ADVERT_MEMORY
        }
        if links_changed:
            self._rebuild_live_routes()
        self.breadcrumbs = {
            key: value
            for key, value in self.breadcrumbs.items()
            if self._now - value.seen_at < DISCOVERY_MEMORY
        }
