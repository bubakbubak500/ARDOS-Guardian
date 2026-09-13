"""Qt-free board and bounded, idempotent WS exchange state machine.

All methods run on the owning protocol thread. TX completions are posted back
to that thread by the service, never applied by the audio worker.
"""
from dataclasses import dataclass
import random
import secrets

from ..protocol.warships import Event, Op, RESULTS, ACKABLE, ship_cells


class Board:
    def __init__(self):
        self.ships = []
        self.hits = set()

    def place(self, start, length, vertical=False):
        cells = ship_cells(start, length | (128 if vertical else 0))
        occupied = {cell for ship in self.ships for cell in ship_cells(*ship)}
        if any(abs(a // 10 - b // 10) <= 1 and abs(a % 10 - b % 10) <= 1 for a in cells for b in occupied):
            raise ValueError("Lodě se nesmějí překrývat ani dotýkat.")
        remaining = [5, 4, 3, 3, 2]
        for _, packed in self.ships:
            remaining.remove(packed & 127)
        if length not in remaining:
            raise ValueError("Tato loď už je rozmístěna.")
        self.ships.append((start, length | (128 if vertical else 0)))

    @property
    def valid(self):
        return sorted(p & 127 for _, p in self.ships) == [2, 3, 3, 4, 5]

    def randomize(self):
        for _ in range(1000):
            self.ships.clear()
            for length in [5, 4, 3, 3, 2]:
                options = [(s, v) for s in range(100) for v in (False, True)]
                random.shuffle(options)
                for start, vertical in options:
                    try:
                        self.place(start, length, vertical)
                        break
                    except ValueError:
                        pass
                else:
                    break
            if self.valid:
                return
        raise RuntimeError("Unable to place fleet")

    def shoot(self, cell, turn):
        self.hits.add(cell)
        for ship in self.ships:
            cells = set(ship_cells(*ship))
            if cell in cells:
                if cells <= self.hits:
                    lost = all(set(ship_cells(*s)) <= self.hits for s in self.ships)
                    return Event(Op.LOSS if lost else Op.SUNK, turn, *ship)
                return Event(Op.HIT, turn, cell)
        return Event(Op.MISS, turn, cell)


@dataclass
class Pending:
    event: Event
    attempts: int = 0
    queued: bool = False
    due: float = 0


class Game:
    def __init__(self, callsign, send, clock):
        self.callsign, self.send, self.clock = callsign, send, clock
        self.state = "LOBBY"
        self.peer = ""
        self.session = 0
        self.channel = None
        self.pending = {}
        self.sent = {}
        self.history = {}
        self.responses = {}
        self.board = Board()
        self.enemy = {}
        self.enemy_ships = []
        self.turn = 0
        self.local_ready = self.peer_ready = self.ready_ack = False
        self.initiator = False
        self.message = "Vyber přímého souseda."
        self.changed = 0
        self.paused_state = None
        self.created = 0
        self.finished = 0
        self.timeout = 12.0

    def _reset(self, peer, session, channel, initiator):
        self.__init__(self.callsign, self.send, self.clock)
        self.peer, self.session, self.channel, self.initiator = peer, session, channel, initiator
        self.created = self.clock()

    def invite(self, peer, channel):
        if self.state != "LOBBY":
            raise ValueError("Nejdřív dokonči současnou hru.")
        self._reset(peer, secrets.randbits(32), channel, True)
        self.state = "INVITE_SENT"
        self.queue(Event(Op.INVITE, a=1))

    def accept(self):
        if self.state != "INVITE_RECEIVED":
            return
        self.state = "PLACING"
        self.queue(Event(Op.ACCEPT, a=1))

    def decline(self):
        if self.state == "INVITE_RECEIVED":
            self.state = "DECLINED"
            self.finished = self.clock()
            self.queue(Event(Op.DECLINE))

    def ready(self):
        if self.state != "PLACING" or not self.board.valid:
            raise ValueError("Nejdřív rozmísti všech pět lodí.")
        self.local_ready = True
        self.state = "READY_WAIT"
        self.queue(Event(Op.READY))
        self._start()

    def _start(self):
        if self.local_ready and self.peer_ready and self.ready_ack and self.state in {"PLACING", "READY_WAIT"}:
            self.state = "MY_TURN" if self.initiator else "PEER_TURN"

    def fire(self, cell):
        if self.state != "MY_TURN" or cell in self.enemy or not 0 <= cell < 100:
            raise ValueError("Na toto pole teď nelze vystřelit.")
        self.target = cell
        self.state = "SHOT_QUEUED"
        self.queue(Event(Op.SHOT, self.turn, cell))

    def end(self, op=Op.RESIGN):
        if self.state in {"LOBBY", "WON", "LOST", "CANCELLED", "DECLINED"}:
            return
        self.pending.clear()
        self.state = "ENDING"
        self.queue(Event(op))

    def queue(self, event):
        if event not in self.pending:
            pending = self.sent.get(event)
            if pending is None:
                pending = Pending(event, due=self.clock())
                self.sent[event] = pending
            self.pending[event] = pending

    def pause(self, message):
        if self.state != "PAUSED":
            self.paused_state = self.state
        self.state, self.message = "PAUSED", message

    def resume(self):
        if self.state != "PAUSED":
            return
        self.state = self.paused_state
        for pending in self.sent.values():
            pending.attempts = 0
            pending.due = self.clock()

    def tick(self, available=True):
        now = self.clock()
        if self.state in {"INVITE_SENT", "INVITE_RECEIVED"} and now - self.created >= 90:
            self.pending.clear()
            self.state = "DECLINED"
            self.message = "Bez odpovědi / výzva vypršela."
            self.finished = now
        if not available or self.state == "PAUSED":
            return
        for event, pending in list(self.pending.items()):
            if pending.queued or now < pending.due:
                continue
            if pending.attempts >= 3:
                if event.op is Op.INVITE:
                    self.pending.clear()
                    self.state = "DECLINED"
                    self.message = "Bez odpovědi."
                    self.finished = now
                    return
                self.pause("Spojení přerušeno. Zkusit znovu odešle stejnou událost.")
                return
            pending.queued = True
            pending.attempts += 1
            self.send(event, lambda ok, p=pending: self.tx_done(p, ok))
            return  # at most one frame per scheduling tick

    def tx_done(self, pending, success):
        if self.pending.get(pending.event) is not pending:
            return  # an early RX may already have completed this exchange
        pending.queued = False
        if not success:
            self.pause("Vysílání se nezdařilo.")
            return
        event = pending.event
        if event.op is Op.ACK:
            pending.due = self.clock() + 2.0
            self.pending.pop(event, None)
            if event.a in RESULTS and self.state == "ACK_QUEUED":
                self.state = "WON" if event.a == Op.LOSS else "PEER_TURN"
                if self.state == "WON":
                    self.finished = self.clock()
            return
        pending.due = self.clock() + self.timeout * (1.65 if event.op is Op.SHOT else 1.0) + random.uniform(0.3, 1.5)
        if event.op is Op.SHOT and self.state == "SHOT_QUEUED":
            self.state = "WAIT_RESULT"

    def _ack(self, event):
        self.queue(Event(Op.ACK, event.turn, int(event.op)))

    def _forget(self, op, turn=255):
        for event in list(self.pending):
            if event.op == op and event.turn == turn:
                self.pending.pop(event)

    def receive(self, peer, session, event, channel):
        if event.op is Op.INVITE:
            if self.state == "INVITE_SENT" and peer == self.peer and peer < self.callsign:
                self._reset(peer, session, channel, False)
                self.state = "INVITE_RECEIVED"
            elif self.state == "LOBBY":
                self._reset(peer, session, channel, False)
                self.state = "INVITE_RECEIVED"
            elif peer == self.peer and session == self.session:
                if self.local_ready or self.state in {"PLACING", "READY_WAIT"}:
                    self.queue(Event(Op.ACCEPT, a=1))
                elif self.state == "DECLINED":
                    self.queue(Event(Op.BUSY) if Event(Op.BUSY) in self.sent else Event(Op.DECLINE))
            return
        if (peer, session, channel) != (self.peer, self.session, self.channel) or self.state in {"LOBBY", "PAUSED"}:
            return
        key = (event.op, event.turn, event.a if event.op is Op.ACK else None)
        previous = self.history.get(key)
        if previous is not None:
            if previous != event:
                self.pause("Rozpor v přijatém tahu.")
            elif event.op in ACKABLE:
                self._ack(event)
            elif event.op is Op.SHOT:
                self.queue(self.responses[event.turn])
            elif event.op is Op.ACK:
                self._forget(event.a, event.turn)
            return
        if event.op is Op.ACCEPT and self.state == "INVITE_SENT":
            self._forget(Op.INVITE)
            self.state = "PLACING"
        elif event.op in {Op.DECLINE, Op.BUSY} and self.state == "INVITE_SENT":
            self._forget(Op.INVITE)
            self.state = "DECLINED"
            self.message = "Výzva odmítnuta." if event.op is Op.DECLINE else "Stanice je obsazená."
            self.finished = self.clock()
        elif event.op is Op.READY and self.state in {"PLACING", "READY_WAIT"}:
            self.peer_ready = True
            self._start()
        elif event.op is Op.ACK:
            matching = [p for p in self.pending if p.op == event.a and p.turn == event.turn]
            if not matching:
                return
            self._forget(event.a, event.turn)
            if event.a == Op.READY:
                self.ready_ack = True
                self._start()
            elif event.a in RESULTS and self.state == "WAIT_RESULT_ACK":
                self.turn += 1
                self.state = "LOST" if event.a == Op.LOSS else "MY_TURN"
                if self.state == "LOST":
                    self.finished = self.clock()
            elif event.a in {Op.RESIGN, Op.CANCEL}:
                self.state = "LOST" if event.a == Op.RESIGN else "CANCELLED"
                self.finished = self.clock()
        elif event.op is Op.SHOT:
            if self.state == "READY_WAIT" and not self.initiator and self.local_ready and self.peer_ready:
                self.ready_ack = True
                self._forget(Op.READY)
                self._start()
            # An early next shot also proves delivery of our previous result ACK.
            if self.state == "ACK_QUEUED" and event.turn == self.turn:
                self.state = "PEER_TURN"
            if self.state != "PEER_TURN" or event.turn != self.turn or event.a in self.board.hits:
                return
            result = self.board.shoot(event.a, event.turn)
            self.responses[event.turn] = result
            self.state = "WAIT_RESULT_ACK"
            self.queue(result)
        elif event.op in RESULTS:
            if self.state not in {"SHOT_QUEUED", "WAIT_RESULT"} or event.turn != self.turn:
                return
            if event.op in {Op.MISS, Op.HIT}:
                valid = event.a == self.target
            else:
                cells = set(ship_cells(event.a, event.b))
                valid = self.target in cells and all(c == self.target or self.enemy.get(c) == Op.HIT for c in cells)
                valid = valid and not any(cells & set(ship_cells(*s)) for s in self.enemy_ships)
                lengths = [5, 4, 3, 3, 2]
                for _, packed in self.enemy_ships:
                    lengths.remove(packed & 127)
                valid = valid and (event.b & 127) in lengths
                valid = valid and ((event.op is Op.LOSS) == (len(self.enemy_ships) == 4))
            if not valid:
                self.pause("Výsledek neodpovídá dosavadním výstřelům.")
                return
            self._forget(Op.SHOT, self.turn)
            self.enemy[self.target] = event.op
            if event.op in {Op.SUNK, Op.LOSS}:
                self.enemy_ships.append((event.a, event.b))
            self.turn += 1
            self.state = "ACK_QUEUED"
        elif event.op in {Op.RESIGN, Op.CANCEL} and self.state not in {"WON", "LOST", "CANCELLED", "DECLINED"}:
            self.pending.clear()
            self.state = "WON" if event.op is Op.RESIGN else "CANCELLED"
            self.finished = self.clock()
        else:
            return
        self.history[key] = event
        if event.op in ACKABLE:
            self._ack(event)
        self.changed += 1
