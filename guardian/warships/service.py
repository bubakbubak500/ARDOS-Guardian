"""Lowest-priority radio integration; no mailbox, routing or payload session."""
import time
from queue import SimpleQueue, Empty

from .model import Game
from ..protocol.warships import decode, Op, Event
from ..protocol.frames import FrameType


class WarshipsService:
    def __init__(self, operations):
        self.operations = operations
        self.direct = {}
        self.completed = SimpleQueue()
        self.game = self._new_game()
        self.archived = []
        self.busy_replies = {}
        self.net = None
        self.last_tick = time.monotonic()
        self.available = False

    def _new_game(self):
        game = Game(self.operations.config.callsign, None, time.monotonic)
        game.send = lambda event, done: self._send(event, done, game)
        return game

    def archive_game(self):
        if self.game.state not in {"WON", "LOST", "CANCELLED", "DECLINED"} or self.game.pending:
            raise ValueError("Nejdřív počkej na potvrzení ukončení.")
        self.archived.append((time.monotonic(), self.game))
        self.game = self._new_game()

    def bind(self):
        net = self.operations.net
        if self.net is not net:
            if self.net is not None:
                self.direct.clear()
                if self.game.state != "LOBBY":
                    self.game.pause("Řídicí kanál byl změněn nebo odpojen.")
            self.net = net
            net.on_warships_frame = self.receive
            net.on_direct_beacon = self.beacon
        if self.game.state == "LOBBY":
            self.game.callsign = self.operations.config.callsign

    def beacon(self, frame, frequency):
        # BEACON is originated locally and never relayed by Guardian.
        if frequency and frame.source and len(frame.source) <= 9:
            self.direct[frame.source] = (time.monotonic(), frequency,
                                         getattr(self.net.transport, "last_frame_snr", None))

    def neighbors(self):
        now = time.monotonic()
        frequency = self.operations.current_frequency()
        self.direct = {call: value for call, value in self.direct.items() if now - value[0] <= 300}
        return {call: value for call, value in self.direct.items() if frequency and value[1] == frequency}

    def invite(self, peer):
        if peer not in self.neighbors():
            raise ValueError("Soused není přímo slyšen na aktuálním kanálu.")
        self.game.callsign = self.operations.config.callsign
        self.game.invite(peer, self.operations.current_frequency())

    def receive(self, frame):
        try:
            event = decode(frame)
        except ValueError:
            return
        channel = self.operations.current_frequency()
        if not channel or frame.destination != self.game.callsign:
            return
        for _, old in [*self.archived, *self.busy_replies.values()]:
            if (frame.source, frame.message_id) == (old.peer, old.session):
                old.receive(frame.source, frame.message_id, event, channel)
                return
        # An incoming direct-only invitation is itself evidence of the peer;
        # outgoing invitations still require a fresh, independently heard beacon.
        if event.op is Op.INVITE:
            recent_decline = any(old.peer == frame.source and old.state == "DECLINED"
                                 and time.monotonic() - old.finished < 60
                                 for _, old in self.archived)
            if recent_decline:
                key = (frame.source, frame.message_id)
                if key not in self.busy_replies and len(self.busy_replies) < 32:
                    reply = self._new_game()
                    reply.peer, reply.session, reply.channel = frame.source, frame.message_id, channel
                    reply.state = "DECLINED"
                    reply.queue(Event(Op.DECLINE))
                    self.busy_replies[key] = (time.monotonic(), reply)
                return
            if self.game.state != "LOBBY" and (frame.source, frame.message_id) != (self.game.peer, self.game.session):
                if not (self.game.state == "INVITE_SENT" and frame.source == self.game.peer):
                    key = (frame.source, frame.message_id)
                    if key not in self.busy_replies and len(self.busy_replies) < 32:
                        reply = self._new_game()
                        reply.peer, reply.session, reply.channel = frame.source, frame.message_id, channel
                        reply.state = "DECLINED"
                        reply.queue(Event(Op.BUSY))
                        self.busy_replies[key] = (time.monotonic(), reply)
                    return
        self.game.receive(frame.source, frame.message_id, event, channel)

    def _priority_clear(self, game=None):
        o = self.operations
        return (o.audio_transport is not None and o._net_idle()
                and not any(frame is None or frame.type is not FrameType.WARSHIPS
                            for frame in list(getattr(o.audio_transport, "_pending_frames", {}).values()))
                and not o.payload_handoff_pending()
                and not o.workers.is_active("alert-sweep")
                and not o.workers.is_active("radio-control")
                and not o.net.alerts_pending()
                and not bool(getattr(getattr(o.vara, "state", None), "ptt", False))
                and (game is None or (o.current_frequency() == game.channel and game.state != "PAUSED")))

    def _send(self, event, done, game=None):
        game = game or self.game
        transport = self.operations.audio_transport
        try:
            frame = event.frame(game.callsign, game.peer, game.session)
            if transport is None:
                done(False)
                return
            transport.send(frame, on_complete=lambda ok: self.completed.put((done, ok)),
                           allowed=lambda: self._priority_clear(game))
        except (ValueError, TypeError, RuntimeError):
            done(False)

    def resume(self):
        if self.game.peer not in self.neighbors() or self.operations.current_frequency() != self.game.channel:
            raise ValueError("Vrať se na původní kanál a počkej na přímý maják soupeře.")
        self.game.resume()

    def tick(self):
        self.bind()
        now = time.monotonic()
        while True:
            try:
                done, ok = self.completed.get_nowait()
            except Empty:
                break
            done(ok)
        game = self.game
        if game.state != "LOBBY" and self.operations.current_frequency() != game.channel:
            game.pause("Změna kanálu. Pro pokračování se vrať na původní kanál.")
        transport = self.operations.audio_transport
        # A previous failed TX is not a busy channel. wait_tx_idle() also
        # reports failures, which would otherwise prevent a manual retry forever.
        self.available = bool(self._priority_clear()
                              and not getattr(transport, "_pending_tx", 0)
                              and not getattr(transport, "channel_busy", lambda: True)())
        if not self.available:
            for pending in game.pending.values():
                pending.due += max(0, now - self.last_tick)
        game.timeout = max(12.0, self.operations.net.ack_timeout * 2)
        game.tick(self.available)
        self.archived = [(at, old) for at, old in self.archived if now - at < 600]
        self.busy_replies = {key: value for key, value in self.busy_replies.items() if now - value[0] < 90}
        if self.available and not any(p.queued for p in game.pending.values()):
            for _, old in [*self.archived, *self.busy_replies.values()]:
                if old.pending and old.channel == self.operations.current_frequency():
                    old.tick(True)
                    break
        self.last_tick = now
