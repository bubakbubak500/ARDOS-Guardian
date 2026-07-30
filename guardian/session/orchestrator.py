"""The VARA handshake state-machine.

Choreographs a message hop between two stations using control frames, then
hands the actual payload to VARA. One Orchestrator runs per station; it plays
both roles depending on the frames it hears:

  initiator (I have a message for the next hop)
  ----------------------------------------------
    -> HAVE_MSG  (announce)            state: ANNOUNCING
    <- ACK_HAVE                        state: STARTING_VARA
    -> START_VARA                      state: TRANSFERRING   (VARA connects, payload flows)
    <- RECEIVED                        state: CONFIRMED
    (<- DELIVERED if next hop == final dest)  state: DELIVERED

  responder (a HAVE_MSG names me as next hop)
  -------------------------------------------
    <- HAVE_MSG                        state: HEARD
    -> ACK_HAVE  (or BUSY)             state: ACKED
    <- START_VARA                      state: RECEIVING      (VARA receives payload)
    payload OK -> RECEIVED             state: RECEIVED_OK
    (if I am final dest -> DELIVERED)  state: DELIVERED

Timeouts and retransmits are driven by `tick(now)`, which the owner calls
periodically (the UI poll loop does this). The payload phase itself is
abstracted: in simulation it auto-completes; with real VARA the data layer
calls `notify_payload_delivered()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from ..protocol import (
    ControlFrame,
    Flags,
    FrameType,
    Priority,
    alert_kind,
    crc16,
    decode_ptt_delay,
    encode_alert,
    encode_ptt_delay,
)
from ..routing import HeardStations
from .transport import ControlTransport

DISCOVERY_TIMEOUT = 8.0

# Timeouts (seconds). Generous, since HF bursts are slow.
ACK_TIMEOUT = 8.0
START_TIMEOUT = 12.0
TRANSFER_TIMEOUT = 180.0
BUSY_BACKOFF = 20.0
# A relayed message sits in CONFIRMED until an end-to-end DELIVERED comes back
# from the far end. That frame can be lost on RF, and CONFIRMED is not terminal,
# so without this bound the session would stay "active" forever.
CONFIRM_TIMEOUT = 120.0
# Alerts are broadcast with no acknowledgement, so repetition is the only
# reliability available. Relays are jittered because every station in earshot
# hears the same alert at the same instant and would otherwise key together.
ALERT_TTL = 3
ALERT_REPEATS = 3
ALERT_REPEAT_GAP = 10.0
ALERT_RELAY_MIN = 1.0
ALERT_RELAY_MAX = 5.0
ALERT_MEMORY = 3600.0
MAX_ANNOUNCE = 3
_PAYLOAD_TRANSFER_TIMEOUT = 120.0
_PAYLOAD_WIRE_OVERHEAD = 14
# Mirrors payload.vara_p2p.MIN_WIRE_SIZE; kept as a local constant so the
# session layer does not depend on a payload backend.  test_session asserts the
# two stay equal.
_PAYLOAD_MIN_WIRE_SIZE = 256
_SLOW_LINK_BPS = 300.0
_TRANSFER_MARGIN = 3.0
_SESSION_MARGIN = 60.0


def alert_priority(code: int) -> Priority:
    """Priority carried by an alert code; unknown codes stay routine."""
    kind = alert_kind(code)
    return kind.priority if kind else Priority.ROUTINE


def session_transfer_timeout_for(msg: "Message") -> float:
    """Keep the control-session deadline beyond the payload-layer deadline."""
    data_size = (
        len(msg.payload_bytes)
        if msg.payload_bytes is not None
        else len(msg.body.encode("utf-8"))
    )
    wire_size = max(
        _PAYLOAD_MIN_WIRE_SIZE,
        _PAYLOAD_WIRE_OVERHEAD + data_size,
    )
    payload_timeout = max(
        _PAYLOAD_TRANSFER_TIMEOUT,
        wire_size * 8 / _SLOW_LINK_BPS * _TRANSFER_MARGIN,
    )
    return max(TRANSFER_TIMEOUT, payload_timeout + _SESSION_MARGIN)


class SessionState(Enum):
    IDLE = "idle"
    # initiator
    ROUTE_DISCOVERY = "discovery"  # no route known; ROUTE_QUERY out, gathering offers
    ANNOUNCING = "announcing"      # sent HAVE_MSG, waiting for ACK
    WAITING_BUSY = "waiting"       # peer was busy, will retry
    STARTING_VARA = "starting"     # got ACK, starting VARA session
    TRANSFERRING = "transferring"  # payload in flight over VARA
    CONFIRMED = "confirmed"        # next hop confirmed RECEIVED
    # responder
    HEARD = "heard"                # heard a HAVE_MSG addressed to me
    ACKED = "acked"                # acked, waiting for START_VARA
    RECEIVING = "receiving"        # receiving payload over VARA
    RECEIVED_OK = "received"       # payload received, sent RECEIVED
    # terminal (both)
    DELIVERED = "delivered"        # reached final destination
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in (SessionState.DELIVERED, SessionState.FAILED, SessionState.CANCELLED)


@dataclass
class Message:
    msg_id: int
    source: str
    final_dest: str
    next_hop: str
    priority: Priority = Priority.ROUTINE
    ttl: int = 5
    flags: Flags = Flags.NONE
    body: str = ""
    payload_bytes: bytes | None = None   # transferable bundle (mail), if any
    direction: str = "out"         # "out" (I'm relaying/originating) | "in"
    state: SessionState = SessionState.IDLE
    attempts: int = 0
    tried_backup: bool = False
    t_state: float = 0.0           # monotonic time the state was entered
    error: str = ""
    offers: list = field(default_factory=list)   # ROUTE_OFFER sources during discovery
    relayed: bool = False          # this station has already relayed this msg
    # The hop is a blind guess: discovery ran and nobody offered, so we are
    # trying the destination directly. Blind announces get one repeat fewer —
    # the channel already carried a query nobody answered.
    blind: bool = False
    # Slow-keying hold-off (ms) negotiated for the VARA payload phase: the
    # larger of what we and the peer asked for in HAVE_MSG/ACK_HAVE.
    ptt_delay_ms: int = 0


class Orchestrator:
    """Per-station control choreographer.

    Parameters
    ----------
    callsign : this station's callsign (frames addressed here are "mine")
    transport: how control frames go out/come in
    routes   : optional RouteTable for next-hop / backup resolution
    on_event : optional callback(message, event:str) for UI/logging
    auto_complete : simulation aid — when True the responder pretends the VARA
                    payload arrived as soon as it gets START_VARA. Real stations
                    leave this False and call notify_payload_delivered().
    begin_transfer : optional callback(message) run when the initiator should
                     push the payload over VARA. Default no-op (sim).
    """

    def __init__(
        self,
        callsign: str,
        transport: ControlTransport,
        routes=None,
        on_event: Callable[[Message, str], None] | None = None,
        auto_complete: bool = False,
        begin_transfer: Callable[[Message], None] | None = None,
        payload=None,
        heard=None,
        auto_route: bool = True,
        relay: bool = False,
        clock: Callable[[], float] | None = None,
    ):
        self.callsign = callsign.strip().upper()
        self.transport = transport
        self.transport.on_frame = self._on_frame
        self.routes = routes
        self.on_event = on_event
        self.auto_complete = auto_complete
        self.begin_transfer = begin_transfer
        self.payload = payload  # PayloadBackend | None
        self.heard = heard if heard is not None else HeardStations()
        self.auto_route = auto_route
        # Scaled to the control modem once a real channel is up: a 34-byte
        # HAVE_MSG is 0.9 s on AFSK but 5.2 s on MFSK-16, so a fixed 8 s ACK
        # budget cannot survive one exchange on HF.
        self.ack_timeout = ACK_TIMEOUT
        self.start_timeout = START_TIMEOUT
        self.relay = relay
        self._clock = clock
        self.learned_paths: dict[str, str] = {}   # final_dest -> next_hop that worked
        self.busy = False
        # Where the radio is tuned, so a heard station can be filed against the
        # channel it was actually heard on. Set by the owner; the session layer
        # never talks to a rig itself.
        self.channel_frequency: Callable[[], int | None] | None = None
        # This station's slow-keying request (ms) for the VARA payload phase,
        # asked at call time so a mode change needs no rebuild. The owner sets
        # it only when it applies (VARA FM, operator configured a delay).
        self.ptt_delay_request: Callable[[], int] | None = None
        # Broadcast alerts: what we have already seen (so a flood converges)
        # and what is waiting to go out (repeats and jittered relays).
        self.on_alert: Callable[[ControlFrame, bool], None] | None = None
        self._seen_alerts: dict[int, float] = {}
        self._alert_queue: list[tuple[float, ControlFrame]] = []
        self._alert_counter = 0
        self.sessions: dict[int, Message] = {}
        self._now = 0.0  # last tick time, used when reacting to frames

    # ------------------------------------------------------------------ #
    #  Public API                                                         #
    # ------------------------------------------------------------------ #
    def send_message(
        self,
        final_dest: str,
        body: str,
        msg_id: int,
        priority: Priority = Priority.ROUTINE,
        next_hop: str | None = None,
        ttl: int = 5,
        flags: Flags = Flags.NONE,
        payload_bytes: bytes | None = None,
    ) -> Message:
        """Originate (or relay) a message toward final_dest."""
        final_dest = final_dest.strip().upper()
        own_delay = self._own_ptt_delay()
        msg = Message(
            msg_id=msg_id, source=self.callsign, final_dest=final_dest,
            next_hop="", priority=priority, ttl=ttl,
            flags=encode_ptt_delay(flags, own_delay),
            body=body, payload_bytes=payload_bytes, direction="out",
        )
        msg.ptt_delay_ms = own_delay
        self.sessions[msg_id] = msg

        hop, how = self._resolve_next_hop(final_dest, explicit=next_hop)
        if hop:
            msg.next_hop = hop
            self._begin_announce(msg)
            self._emit(msg, f"announcing to {hop} (final {final_dest}, via {how})")
        elif self.auto_route:
            # No route known — discover one with a ROUTE_QUERY broadcast.
            self._enter(msg, SessionState.ROUTE_DISCOVERY)
            self._send(FrameType.ROUTE_QUERY, msg)
            self._emit(msg, f"no route to {final_dest} — querying the net")
        else:
            msg.next_hop = final_dest  # last resort: try the destination directly
            self._begin_announce(msg)
            self._emit(msg, f"announcing directly to {final_dest}")
        return msg

    def _own_ptt_delay(self) -> int:
        """What this station asks the peer to slow down by, in ms."""
        if self.ptt_delay_request is None:
            return 0
        try:
            return max(0, int(self.ptt_delay_request()))
        except Exception:       # noqa: BLE001 - a config fault must not stop mail
            return 0

    def _resolve_next_hop(self, final_dest: str, explicit: str | None = None) -> tuple[str | None, str]:
        """Pick a next hop: explicit > manual route > learned > directly heard."""
        hop = (explicit or "").strip().upper()
        if hop:
            return hop, "manual"
        if self.routes is not None:
            route = self.routes.lookup(final_dest)
            if route is not None:
                if route.preferred:
                    return route.preferred, "route"
                return final_dest, "direct route"
        if final_dest in self.learned_paths:
            return self.learned_paths[final_dest], "learned"
        if self.heard.is_heard(final_dest, self._now):
            return final_dest, "heard"
        return None, "none"

    def _begin_announce(self, msg: Message) -> None:
        self._enter(msg, SessionState.ANNOUNCING)
        msg.attempts = 1
        self._send(FrameType.HAVE_MSG, msg)

    def beacon(self) -> None:
        """Transmit a presence beacon so neighbours hear (and can deliver to) us."""
        self.transport.send(ControlFrame(type=FrameType.BEACON, source=self.callsign))

    def cancel(self, msg_id: int) -> None:
        msg = self.sessions.get(msg_id)
        if msg and not msg.state.terminal:
            self._send(FrameType.CANCEL, msg)
            self._enter(msg, SessionState.CANCELLED)
            self._emit(msg, "cancelled by operator")

    # ------------------------------------------------------------------ #
    #  Bench testing — drive the payload (VARA) phase directly, bypassing #
    #  the control-burst handshake. Lets you verify the VARA round-trip   #
    #  on real radios before the on-air control modem is proven.          #
    # ------------------------------------------------------------------ #
    def force_send(self, final_dest, next_hop, msg_id, body="",
                   payload_bytes=None, priority: Priority = Priority.ROUTINE,
                   payload=None) -> "Message":
        """[TEST] Jump straight to TRANSFERRING and push a payload over VARA,
        as if a next hop had ACKed. No HAVE_MSG/ACK_HAVE exchange happens —
        use this to test the VARA P2P / Winlink layer in isolation."""
        backend = payload if payload is not None else self.payload
        next_hop = next_hop.strip().upper()
        msg = Message(
            msg_id=msg_id, source=self.callsign,
            final_dest=(final_dest or next_hop).strip().upper(),
            next_hop=next_hop, priority=priority,
            body=body, payload_bytes=payload_bytes, direction="out",
        )
        self.sessions[msg_id] = msg
        self._enter(msg, SessionState.TRANSFERRING)
        self._emit(msg, f"[BENCH] force-send to {next_hop} over VARA (control net bypassed)")
        if backend is not None:
            backend.start_send(msg, lambda ok, m=msg: self._on_send_done(m, ok))
        return msg

    def force_receive(self, source, final_dest, msg_id,
                      priority: Priority = Priority.ROUTINE, payload=None) -> "Message":
        """[TEST] Jump straight to RECEIVING and LISTEN on VARA for one payload,
        as if a START_VARA had arrived. No HAVE_MSG/ACK_HAVE exchange happens."""
        backend = payload if payload is not None else self.payload
        msg = Message(
            msg_id=msg_id, source=(source or "BENCH").strip().upper(),
            final_dest=(final_dest or self.callsign).strip().upper(),
            next_hop=self.callsign, priority=priority, direction="in",
        )
        self.sessions[msg_id] = msg
        self._enter(msg, SessionState.RECEIVING)
        self._emit(msg, "[BENCH] force-receive — VARA LISTEN (control net bypassed)")
        if backend is not None:
            backend.start_receive(msg, lambda ok, m=msg: self.notify_payload_delivered(m.msg_id, ok))
        return msg

    def notify_payload_delivered(self, msg_id: int, ok: bool = True) -> None:
        """Called (by VARA layer or sim) when an inbound payload finished."""
        msg = self.sessions.get(msg_id)
        if not msg or msg.direction != "in":
            return
        if not ok:
            self._send(FrameType.CANCEL, msg)
            self._enter(msg, SessionState.FAILED)
            msg.error = "payload CRC failed"
            self._emit(msg, "payload failed — sent CANCEL")
            return
        self._send(FrameType.RECEIVED, msg)
        if self.callsign == msg.final_dest:
            self._send(FrameType.DELIVERED, msg)
            self._enter(msg, SessionState.DELIVERED)
            self._emit(msg, "payload received — I am the final destination")
        else:
            self._enter(msg, SessionState.RECEIVED_OK)
            self._emit(msg, "payload received — ready to relay onward")
            self._maybe_relay(msg)

    def _maybe_relay(self, inbound: Message) -> None:
        """Mesh: forward a received message toward its final destination."""
        if not self.relay or inbound.relayed:
            return
        if inbound.ttl <= 1:
            self._emit(inbound, "TTL expired — not relaying")
            return
        inbound.relayed = True
        own_delay = self._own_ptt_delay()
        relay = Message(
            msg_id=inbound.msg_id, source=self.callsign,
            final_dest=inbound.final_dest, next_hop="",
            priority=inbound.priority, ttl=inbound.ttl - 1,
            # The delay negotiated on the previous hop belongs to that pair of
            # radios; the next leg starts over from our own request.
            flags=encode_ptt_delay(inbound.flags, own_delay),
            body=inbound.body,
            payload_bytes=inbound.payload_bytes, direction="out",
        )
        relay.ptt_delay_ms = own_delay
        self.sessions[inbound.msg_id] = relay  # the outbound leg takes over
        hop, how = self._resolve_next_hop(relay.final_dest)
        if hop:
            relay.next_hop = hop
            self._begin_announce(relay)
            self._emit(relay, f"relaying to {hop} (final {relay.final_dest}, ttl {relay.ttl})")
        elif self.auto_route:
            self._enter(relay, SessionState.ROUTE_DISCOVERY)
            self._send(FrameType.ROUTE_QUERY, relay)
            self._emit(relay, f"relaying — querying route to {relay.final_dest}")
        else:
            self._fail(relay, "relay: no route to destination")

    def _on_send_done(self, msg: Message, ok: bool) -> None:
        """Initiator payload-send result. End-to-end confirm still via RECEIVED."""
        if ok:
            self._emit(msg, "payload sent — awaiting RECEIVED")
            return
        # Only abort if we're still mid-transfer; a peer RECEIVED may have
        # already advanced us to CONFIRMED/DELIVERED.
        if msg.state is SessionState.TRANSFERRING:
            self._send(FrameType.CANCEL, msg)
            self._fail(msg, "payload send failed")

    def tick(self, now: float) -> None:
        """Drive timeouts/retransmits. Call periodically."""
        self._now = now
        self._tick_alerts(now)
        for msg in list(self.sessions.values()):
            if msg.state.terminal:
                continue
            elapsed = now - msg.t_state
            if msg.state is SessionState.ANNOUNCING and elapsed > self.ack_timeout:
                self._announce_timeout(msg)
            elif msg.state is SessionState.WAITING_BUSY and elapsed > BUSY_BACKOFF:
                self._enter(msg, SessionState.ANNOUNCING)
                msg.attempts = 1
                self._send(FrameType.HAVE_MSG, msg)
                self._emit(msg, "retrying after busy")
            elif msg.state is SessionState.STARTING_VARA and elapsed > self.start_timeout:
                self._fail(msg, "VARA did not start in time")
            elif (
                msg.state is SessionState.TRANSFERRING
                and elapsed > session_transfer_timeout_for(msg)
            ):
                self._fail(msg, "no RECEIVED before transfer timeout")
            elif msg.state is SessionState.ACKED and elapsed > self.start_timeout:
                self._fail(msg, "initiator never sent START_VARA")
            elif msg.state is SessionState.RECEIVING and self.auto_complete:
                # Simulation: pretend the VARA payload has now arrived.
                self.notify_payload_delivered(msg.msg_id, ok=True)
            elif msg.state is SessionState.ROUTE_DISCOVERY and elapsed > DISCOVERY_TIMEOUT:
                self._discovery_timeout(msg)
            elif msg.state is SessionState.CONFIRMED and elapsed > CONFIRM_TIMEOUT:
                # The next hop has the message; only the end-to-end receipt is
                # missing. Close the session rather than count it as an active
                # transfer for the rest of the run.
                self._enter(msg, SessionState.DELIVERED)
                self._emit(
                    msg,
                    "next hop holds the message; no end-to-end DELIVERED "
                    "arrived before the confirmation timeout",
                )

    def _discovery_timeout(self, msg: Message) -> None:
        if msg.offers:
            hop = self._best_offer(msg)
            msg.next_hop = hop
            self._begin_announce(msg)
            self._emit(msg, f"discovered route via {hop} (offers: {','.join(msg.offers)})")
        else:
            # Nobody offered — try the destination directly as a last resort.
            # The query already went unanswered, so this is a blind guess and
            # gets one announce fewer than a hop somebody vouched for.
            msg.next_hop = msg.final_dest
            msg.blind = True
            self._begin_announce(msg)
            self._emit(msg, f"no offers — trying {msg.final_dest} directly")

    def _best_offer(self, msg: Message) -> str:
        # Prefer the destination itself (direct reach); else the most recently
        # heard offerer.
        if msg.final_dest in msg.offers:
            return msg.final_dest
        ranked = sorted(
            msg.offers,
            key=lambda c: (self.heard.get(c).last_heard if self.heard.get(c) else 0),
            reverse=True,
        )
        return ranked[0]

    # ------------------------------------------------------------------ #
    #  Frame handling                                                     #
    # ------------------------------------------------------------------ #
    def _on_frame(self, frame: ControlFrame) -> None:
        # Every frame we hear means its sender is reachable right now.
        if frame.source and frame.source != self.callsign:
            self.heard.record(
                frame.source,
                self._now,
                snr=getattr(self.transport, "last_frame_snr", None),
                freq_hz=self._current_frequency(),
                frame=frame.type.name,
            )
        self._dispatch(frame)

    def _current_frequency(self) -> int | None:
        """The tuned frequency, or None when nobody is telling us / CAT is out."""
        if self.channel_frequency is None:
            return None
        try:
            return self.channel_frequency()
        except Exception:       # noqa: BLE001 - a stale rig must not drop frames
            return None

    def _dispatch(self, frame: ControlFrame) -> None:
        handler = {
            FrameType.HAVE_MSG: self._rx_have_msg,
            FrameType.ACK_HAVE: self._rx_ack,
            FrameType.BUSY: self._rx_busy,
            FrameType.ROUTE_QUERY: self._rx_route_query,
            FrameType.ROUTE_OFFER: self._rx_route_offer,
            FrameType.START_VARA: self._rx_start,
            FrameType.RECEIVED: self._rx_received,
            FrameType.DELIVERED: self._rx_delivered,
            FrameType.CANCEL: self._rx_cancel,
            FrameType.ALERT: self._rx_alert,
        }.get(frame.type)
        if handler:
            handler(frame)

    # ------------------------------------------------------------------ #
    #  Alerts (net-wide broadcast, flooded)                               #
    # ------------------------------------------------------------------ #
    def send_alert(self, code: int, note: str = "", ttl: int = ALERT_TTL) -> ControlFrame:
        """Broadcast an alert to everyone on the channel and flood it onward.

        Nobody acknowledges an alert, so repetition is the only reliability
        there is: the frame is queued ALERT_REPEATS times and `tick` spaces the
        copies out. Our own id goes straight into the seen set so a neighbour's
        relay of our own alert never comes back at us.
        """
        payload = encode_alert(code, note, self.callsign)
        frame = ControlFrame(
            type=FrameType.ALERT,
            source=self.callsign,
            destination=payload,
            next_hop="",
            message_id=self._next_alert_id(),
            priority=alert_priority(code),
            ttl=max(1, int(ttl)),
        )
        self._seen_alerts[frame.message_id] = self._now
        self._alert_queue.extend(
            (self._now + n * ALERT_REPEAT_GAP, frame) for n in range(ALERT_REPEATS)
        )
        if self.on_alert is not None:
            self._safe_alert(frame, mine=True)
        return frame

    def retransmit_alert(self, frame: ControlFrame) -> None:
        """Put an alert we already originated on the air again, unchanged.

        The frequency sweep uses this: keeping the *same* message id on every
        channel is the whole point, so a station that hears the alert on two of
        them still shows it once and still relays it once.
        """
        self._seen_alerts[frame.message_id] = self._now
        self.transport.send(frame)

    def alerts_pending(self) -> int:
        """Alert copies still queued for the air (own repeats plus relays)."""
        return len(self._alert_queue)

    def _next_alert_id(self) -> int:
        self._alert_counter = (self._alert_counter + 1) & 0xFFFF
        return (crc16(self.callsign.encode("ascii", "replace")) << 16 |
                self._alert_counter) & 0xFFFFFFFF

    def _rx_alert(self, f: ControlFrame) -> None:
        """Show a heard alert once, then pass it on."""
        if f.source == self.callsign or f.message_id in self._seen_alerts:
            return                      # ours, or already flooded by someone
        self._seen_alerts[f.message_id] = self._now
        self._safe_alert(f, mine=False)
        if f.ttl > 1:
            onward = ControlFrame(
                type=FrameType.ALERT,
                source=f.source,        # always the originator, never the relay
                destination=f.destination,
                next_hop=self.callsign,  # who passed it on, for diagnostics
                message_id=f.message_id,
                priority=f.priority,
                ttl=f.ttl - 1,
            )
            # Everyone in earshot heard the same alert at the same moment, so
            # relaying immediately would collide. Spread the repeats out.
            delay = ALERT_RELAY_MIN + self._jitter() * (
                ALERT_RELAY_MAX - ALERT_RELAY_MIN
            )
            self._alert_queue.append((self._now + delay, onward))

    def _jitter(self) -> float:
        """Deterministic 0..1 spread, distinct per station, no RNG state."""
        self._alert_counter = (self._alert_counter + 1) & 0xFFFF
        seed = crc16(f"{self.callsign}{self._alert_counter}".encode("ascii", "replace"))
        return seed / 0xFFFF

    def _safe_alert(self, frame: ControlFrame, *, mine: bool) -> None:
        if self.on_alert is None:
            return
        try:
            self.on_alert(frame, mine)
        except Exception:       # noqa: BLE001 - a UI fault must not stop the net
            pass

    def _tick_alerts(self, now: float) -> None:
        # Forget alerts long enough after the last copy that a late relay is
        # still recognised, but not so long that a repeat exercise is ignored.
        if self._seen_alerts:
            self._seen_alerts = {
                msg_id: seen for msg_id, seen in self._seen_alerts.items()
                if now - seen < ALERT_MEMORY
            }
        due = [item for item in self._alert_queue if item[0] <= now]
        if not due:
            return
        self._alert_queue = [item for item in self._alert_queue if item[0] > now]
        for _when, frame in due:
            self.transport.send(frame)

    def _rx_route_query(self, f: ControlFrame) -> None:
        """Someone is asking who can reach f.destination. Offer if we can."""
        if f.source == self.callsign:
            return
        dest = f.destination
        can = (
            dest == self.callsign
            or (self.routes is not None and self.routes.lookup(dest) is not None)
            or dest in self.learned_paths
            or self.heard.is_heard(dest, self._now)
        )
        if can:
            offer = ControlFrame(
                type=FrameType.ROUTE_OFFER, source=self.callsign,
                destination=dest, next_hop=f.source, message_id=f.message_id,
                priority=f.priority, ttl=f.ttl,
            )
            self.transport.send(offer)
            # Remember that we can reach dest (for our own heard table view).
            self.heard.record(f.source, self._now, frame="ROUTE_QUERY")

    def _rx_route_offer(self, f: ControlFrame) -> None:
        """A station offers to relay toward f.destination for our query."""
        msg = self.sessions.get(f.message_id)
        if msg and msg.direction == "out" and msg.state is SessionState.ROUTE_DISCOVERY:
            if f.source not in msg.offers:
                msg.offers.append(f.source)
                self._emit(msg, f"route offer from {f.source}")
        # Note that this station can reach the destination.
        self.heard.record(f.source, self._now, frame="ROUTE_OFFER", reaches=f.destination)

    def _rx_have_msg(self, f: ControlFrame) -> None:
        # Am I the requested next hop (or the final dest with no hop set)?
        addressed = f.next_hop == self.callsign or (not f.next_hop and f.final_dest == self.callsign)
        if not addressed:
            return  # someone else's announcement; just ignore (could log "heard")
        existing = self.sessions.get(f.message_id)
        if existing and not existing.state.terminal:
            # A repeated announcement means the initiator did not hear our
            # answer. Staying silent used to strand both sides: they burn
            # every re-announce against a peer that acked once into a lost
            # frame and then said nothing more.
            if existing.direction == "in" and existing.state is SessionState.ACKED:
                self._send(FrameType.ACK_HAVE, existing)
                self._emit(existing, f"re-acked repeated HAVE_MSG from {f.source}")
            return
        # Slow-keying negotiation: the payload phase runs at the larger of the
        # two requests, and our ACK carries the result back so both stations
        # key with the same hold-off.
        negotiated = max(decode_ptt_delay(f.flags), self._own_ptt_delay())
        msg = Message(
            msg_id=f.message_id, source=f.source, final_dest=f.destination,
            next_hop=self.callsign, priority=f.priority, ttl=f.ttl,
            flags=encode_ptt_delay(f.flags, negotiated),
            direction="in",
        )
        msg.ptt_delay_ms = negotiated
        self.sessions[f.message_id] = msg
        self._enter(msg, SessionState.HEARD)
        if self.busy:
            self._send(FrameType.BUSY, msg)
            self._fail(msg, "declined — station busy")
            return
        self._send(FrameType.ACK_HAVE, msg)
        self._enter(msg, SessionState.ACKED)
        self._emit(msg, f"acked HAVE_MSG from {f.source}")

    def _rx_ack(self, f: ControlFrame) -> None:
        msg = self._mine(f, "out")
        if msg and msg.state is SessionState.ANNOUNCING and f.source == msg.next_hop:
            # The responder answered with the negotiated hold-off (the larger
            # of the two requests); adopt it for our own keying too.
            msg.ptt_delay_ms = max(msg.ptt_delay_ms, decode_ptt_delay(f.flags))
            self._enter(msg, SessionState.STARTING_VARA)
            self._send(FrameType.START_VARA, msg)
            self._emit(msg, f"{f.source} ready — starting VARA")
            self._enter(msg, SessionState.TRANSFERRING)
            if self.begin_transfer:
                self.begin_transfer(msg)
            if self.payload is not None:
                self.payload.start_send(msg, lambda ok, m=msg: self._on_send_done(m, ok))

    def _rx_busy(self, f: ControlFrame) -> None:
        msg = self._mine(f, "out")
        if msg and msg.state in (SessionState.ANNOUNCING, SessionState.WAITING_BUSY):
            self._enter(msg, SessionState.WAITING_BUSY)
            self._emit(msg, f"{f.source} busy — backing off")

    def _rx_start(self, f: ControlFrame) -> None:
        msg = self._mine(f, "in")
        if msg and msg.state is SessionState.ACKED:
            self._enter(msg, SessionState.RECEIVING)
            self._emit(msg, "receiving payload over VARA")
            if self.payload is not None:
                self.payload.start_receive(
                    msg, lambda ok, m=msg: self.notify_payload_delivered(m.msg_id, ok))

    def _rx_received(self, f: ControlFrame) -> None:
        msg = self._mine(f, "out")
        if msg and msg.state is SessionState.TRANSFERRING:
            # Learn that this next hop reaches this destination (for next time).
            if msg.next_hop:
                self.learned_paths[msg.final_dest] = msg.next_hop
            if msg.next_hop == msg.final_dest:
                # The station confirming receipt *is* the final destination, so
                # RECEIVED already proves end-to-end delivery. Waiting for a
                # separate DELIVERED frame only risks losing it on RF and
                # leaving the session open for good.
                self._enter(msg, SessionState.DELIVERED)
                self._emit(msg, f"{f.source} confirmed RECEIVED (final destination)")
                return
            self._enter(msg, SessionState.CONFIRMED)
            self._emit(msg, f"{f.source} confirmed RECEIVED")

    def _rx_delivered(self, f: ControlFrame) -> None:
        msg = self.sessions.get(f.message_id)
        if msg and not msg.state.terminal:
            self._enter(msg, SessionState.DELIVERED)
            self._emit(msg, "end-to-end DELIVERED confirmation received")

    def _rx_cancel(self, f: ControlFrame) -> None:
        msg = self.sessions.get(f.message_id)
        if msg and not msg.state.terminal:
            self._enter(msg, SessionState.CANCELLED)
            self._emit(msg, f"cancelled by {f.source}")

    # ------------------------------------------------------------------ #
    #  Helpers                                                            #
    # ------------------------------------------------------------------ #
    def _announce_timeout(self, msg: Message) -> None:
        # A blind hop (discovery ran, nobody answered) is announced twice in
        # total, not three times: one ROUTE_QUERY plus two HAVE_MSG is already
        # three unanswered transmissions on a shared channel.
        limit = MAX_ANNOUNCE - 1 if msg.blind else MAX_ANNOUNCE
        if msg.attempts < limit:
            msg.attempts += 1
            msg.t_state = self._now
            self._send(FrameType.HAVE_MSG, msg)
            self._emit(msg, f"no ACK — re-announcing (attempt {msg.attempts})")
            return
        # Try the configured backup hop once.
        if not msg.tried_backup and self.routes is not None:
            backup = self.routes.next_hop(msg.final_dest, use_backup=True)
            if backup and backup != msg.next_hop and backup != "ANY":
                msg.next_hop = backup
                msg.tried_backup = True
                msg.attempts = 1
                msg.t_state = self._now
                self._send(FrameType.HAVE_MSG, msg)
                self._emit(msg, f"no ACK — trying backup hop {backup}")
                return
        self._fail(msg, "no ACK from next hop")

    def _mine(self, f: ControlFrame, direction: str) -> Message | None:
        msg = self.sessions.get(f.message_id)
        if msg and msg.direction == direction:
            return msg
        return None

    def _enter(self, msg: Message, state: SessionState) -> None:
        msg.state = state
        msg.t_state = self._now

    def _fail(self, msg: Message, reason: str) -> None:
        msg.error = reason
        self._enter(msg, SessionState.FAILED)
        self._emit(msg, f"failed: {reason}")

    def _send(self, ftype: FrameType, msg: Message) -> None:
        frame = ControlFrame(
            type=ftype, source=self.callsign, destination=msg.final_dest,
            next_hop=msg.next_hop, message_id=msg.msg_id,
            priority=msg.priority, ttl=msg.ttl, flags=msg.flags,
        )
        self.transport.send(frame)

    def _emit(self, msg: Message, event: str) -> None:
        if self.on_event:
            self.on_event(msg, event)
