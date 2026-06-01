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

from ..protocol import ControlFrame, Flags, FrameType, Priority
from .transport import ControlTransport

# Timeouts (seconds). Generous, since HF bursts are slow.
ACK_TIMEOUT = 8.0
START_TIMEOUT = 12.0
TRANSFER_TIMEOUT = 120.0
BUSY_BACKOFF = 20.0
MAX_ANNOUNCE = 3


class SessionState(Enum):
    IDLE = "idle"
    # initiator
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
    direction: str = "out"         # "out" (I'm relaying/originating) | "in"
    state: SessionState = SessionState.IDLE
    attempts: int = 0
    tried_backup: bool = False
    t_state: float = 0.0           # monotonic time the state was entered
    error: str = ""


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
    ):
        self.callsign = callsign.strip().upper()
        self.transport = transport
        self.transport.on_frame = self._on_frame
        self.routes = routes
        self.on_event = on_event
        self.auto_complete = auto_complete
        self.begin_transfer = begin_transfer
        self.payload = payload  # PayloadBackend | None
        self.busy = False
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
    ) -> Message:
        """Originate (or relay) a message toward final_dest."""
        final_dest = final_dest.strip().upper()
        hop = (next_hop or "").strip().upper()
        if not hop and self.routes is not None:
            hop = self.routes.next_hop(final_dest) or ""
        if not hop:
            hop = final_dest  # last resort: try to reach the destination directly
        msg = Message(
            msg_id=msg_id, source=self.callsign, final_dest=final_dest,
            next_hop=hop, priority=priority, ttl=ttl, flags=flags,
            body=body, direction="out",
        )
        self.sessions[msg_id] = msg
        self._enter(msg, SessionState.ANNOUNCING)
        msg.attempts = 1
        self._send(FrameType.HAVE_MSG, msg)
        self._emit(msg, f"announcing to {hop} (final {final_dest})")
        return msg

    def cancel(self, msg_id: int) -> None:
        msg = self.sessions.get(msg_id)
        if msg and not msg.state.terminal:
            self._send(FrameType.CANCEL, msg)
            self._enter(msg, SessionState.CANCELLED)
            self._emit(msg, "cancelled by operator")

    def notify_payload_delivered(self, msg_id: int, ok: bool = True) -> None:
        """Called (by VARA layer or sim) when an inbound payload finished."""
        msg = self.sessions.get(msg_id)
        if not msg or msg.direction != "in":
            return
        if not ok:
            self._enter(msg, SessionState.FAILED)
            msg.error = "payload CRC failed"
            self._emit(msg, "payload failed")
            return
        self._send(FrameType.RECEIVED, msg)
        if self.callsign == msg.final_dest:
            self._send(FrameType.DELIVERED, msg)
            self._enter(msg, SessionState.DELIVERED)
            self._emit(msg, "payload received — I am the final destination")
        else:
            self._enter(msg, SessionState.RECEIVED_OK)
            self._emit(msg, "payload received — ready to relay onward")

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
        for msg in list(self.sessions.values()):
            if msg.state.terminal:
                continue
            elapsed = now - msg.t_state
            if msg.state is SessionState.ANNOUNCING and elapsed > ACK_TIMEOUT:
                self._announce_timeout(msg)
            elif msg.state is SessionState.WAITING_BUSY and elapsed > BUSY_BACKOFF:
                self._enter(msg, SessionState.ANNOUNCING)
                msg.attempts = 1
                self._send(FrameType.HAVE_MSG, msg)
                self._emit(msg, "retrying after busy")
            elif msg.state is SessionState.STARTING_VARA and elapsed > START_TIMEOUT:
                self._fail(msg, "VARA did not start in time")
            elif msg.state is SessionState.TRANSFERRING and elapsed > TRANSFER_TIMEOUT:
                self._fail(msg, "no RECEIVED before transfer timeout")
            elif msg.state is SessionState.ACKED and elapsed > START_TIMEOUT:
                self._fail(msg, "initiator never sent START_VARA")
            elif msg.state is SessionState.RECEIVING and self.auto_complete:
                # Simulation: pretend the VARA payload has now arrived.
                self.notify_payload_delivered(msg.msg_id, ok=True)

    # ------------------------------------------------------------------ #
    #  Frame handling                                                     #
    # ------------------------------------------------------------------ #
    def _on_frame(self, frame: ControlFrame) -> None:
        handler = {
            FrameType.HAVE_MSG: self._rx_have_msg,
            FrameType.ACK_HAVE: self._rx_ack,
            FrameType.BUSY: self._rx_busy,
            FrameType.START_VARA: self._rx_start,
            FrameType.RECEIVED: self._rx_received,
            FrameType.DELIVERED: self._rx_delivered,
            FrameType.CANCEL: self._rx_cancel,
        }.get(frame.type)
        if handler:
            handler(frame)

    def _rx_have_msg(self, f: ControlFrame) -> None:
        # Am I the requested next hop (or the final dest with no hop set)?
        addressed = f.next_hop == self.callsign or (not f.next_hop and f.final_dest == self.callsign)
        if not addressed:
            return  # someone else's announcement; just ignore (could log "heard")
        existing = self.sessions.get(f.message_id)
        if existing and not existing.state.terminal:
            return  # duplicate announcement, already handling
        msg = Message(
            msg_id=f.message_id, source=f.source, final_dest=f.destination,
            next_hop=self.callsign, priority=f.priority, ttl=f.ttl, flags=f.flags,
            direction="in",
        )
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
            self._enter(msg, SessionState.CONFIRMED)
            self._emit(msg, f"{f.source} confirmed RECEIVED")
            if msg.next_hop == msg.final_dest:
                # Next hop is the final dest; DELIVERED should follow.
                pass

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
        if msg.attempts < MAX_ANNOUNCE:
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
