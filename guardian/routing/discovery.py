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

from collections import deque
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
DISCOVERY_MONITOR = "monitor"
DISCOVERY_ASSISTED = "assisted"
DISCOVERY_MODES = (DISCOVERY_OFF, DISCOVERY_MONITOR, DISCOVERY_ASSISTED)

MIN_DISCOVERY_TTL = 2
MAX_DISCOVERY_TTL = 8
DEFAULT_ROUTE_LIFETIME = 1800.0
DEFAULT_QUERY_TIMEOUT = 12.0
DEFAULT_SETTLE_TIME = 2.0
DEFAULT_FRAME_BUDGET = 12
DISCOVERY_MEMORY = 180.0
RREP_REPEAT_GAP = 2.0


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
    approved: bool = False
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
        self._routes: dict[tuple[str, str], DynamicRoute] = {}

    def learn(
        self,
        destination: str,
        next_hop: str,
        hops: int,
        penalty: int,
        query_id: int,
        now: float,
    ) -> DynamicRoute:
        route = DynamicRoute(
            destination.strip().upper(),
            next_hop.strip().upper(),
            max(1, int(hops)),
            max(0, int(penalty)),
            float(now),
            float(now) + self.lifetime,
            int(query_id) & 0xFFFFFFFF,
        )
        key = (route.destination, route.next_hop)
        old = self._routes.get(key)
        if old is not None and old.active(now):
            route.approved = old.approved
            route.last_success = old.last_success
            route.failures = old.failures
        self._routes[key] = route
        return route

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
        return min(candidates, key=lambda route: (route.metric, -route.learned_at, route.next_hop), default=None)

    def approve(self, destination: str, now: float) -> DynamicRoute | None:
        route = self.best(destination, now)
        if route is not None:
            for candidate in self._routes.values():
                if candidate.destination == route.destination:
                    candidate.approved = candidate is route
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
        route = self._routes.get((destination.strip().upper(), next_hop.strip().upper()))
        if route is not None:
            route.last_success = now
            route.failures = 0
            route.expires_at = max(route.expires_at, now + self.lifetime)

    def mark_failure(self, destination: str, next_hop: str) -> None:
        route = self._routes.get((destination.strip().upper(), next_hop.strip().upper()))
        if route is not None:
            route.failures += 1
            route.approved = False


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
        mode: str = DISCOVERY_MONITOR,
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
        on_event: Callable[[DiscoveryEvent], None] | None = None,
        on_result: Callable[[DynamicRoute, PendingQuery], None] | None = None,
        on_failure: Callable[[PendingQuery, str], None] | None = None,
    ) -> None:
        self.callsign = callsign.strip().upper()
        self.send = send
        self.mode = mode if mode in DISCOVERY_MODES else DISCOVERY_MONITOR
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
        self.on_event = on_event
        self.on_result = on_result
        self.on_failure = on_failure
        self.routes = DynamicRouteStore(route_lifetime)
        self.pending: dict[int, PendingQuery] = {}
        self.breadcrumbs: dict[tuple[str, int, str], Breadcrumb] = {}
        self.events: deque[DiscoveryEvent] = deque(maxlen=200)
        self._scheduled: list[_Scheduled] = []
        self._sent: deque[float] = deque()
        self._counter = 0
        self._now = 0.0

    @property
    def can_transmit(self) -> bool:
        return self.mode == DISCOVERY_ASSISTED

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
    ) -> None:
        if callsign is not None:
            self.callsign = callsign.strip().upper()
        if mode is not None:
            previous_mode = self.mode
            self.mode = mode if mode in DISCOVERY_MODES else DISCOVERY_MONITOR
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
        if frame_budget is not None:
            self.frame_budget = max(1, int(frame_budget))
        if allowlist is not None:
            self.allowlist = {item.strip().upper() for item in allowlist if item.strip()}
        if denylist is not None:
            self.denylist = {item.strip().upper() for item in denylist if item.strip()}

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
        if self.mode == DISCOVERY_OFF or not self._peer_allowed(previous):
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
        self.breadcrumbs = {
            key: value
            for key, value in self.breadcrumbs.items()
            if self._now - value.seen_at < DISCOVERY_MEMORY
        }
