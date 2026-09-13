import time
from types import SimpleNamespace

import pytest

from guardian.protocol.frames import ControlFrame, FrameType, Flags
from guardian.protocol.warships import Event, Op, decode, ship_cells
from guardian.warships.model import Board, Game


class Duel:
    def __init__(self, drop=None):
        self.now = 100.0
        self.queue = []
        self.frames = []
        self.drop = drop or (lambda source, event: False)
        self.a = Game("A", lambda e, cb: self.send("A", e, cb), lambda: self.now)
        self.b = Game("B", lambda e, cb: self.send("B", e, cb), lambda: self.now)

    def send(self, source, event, callback):
        self.frames.append((source, event))
        if not self.drop(source, event):
            self.queue.append((source, event))
        callback(True)

    def step(self):
        self.now += .25
        self.a.tick()
        self.b.tick()
        queue, self.queue = self.queue, []
        for source, event in queue:
            sender, receiver = (self.a, self.b) if source == "A" else (self.b, self.a)
            receiver.receive(source, sender.session, event, 145500000)

    def until(self, predicate):
        for _ in range(800):
            self.step()
            if predicate():
                return
        raise AssertionError((self.a.state, self.b.state, self.a.pending, self.b.pending))

    def start(self):
        self.a.invite("B", 145500000)
        self.until(lambda: self.b.state == "INVITE_RECEIVED")
        self.b.accept()
        self.until(lambda: self.a.state == "PLACING")
        for game in (self.a, self.b):
            for start, length in [(0, 5), (20, 4), (40, 3), (60, 3), (80, 2)]:
                game.board.place(start, length)
            game.ready()
        self.until(lambda: (self.a.state, self.b.state) == ("MY_TURN", "PEER_TURN"))


@pytest.mark.parametrize("lost", list(Op))
def test_duel_survives_loss_of_each_handshake_and_turn_frame(lost):
    dropped = []
    def drop(source, event):
        if event.op == lost and not dropped:
            dropped.append(event)
            return True
        return False
    duel = Duel(drop)
    duel.start()
    # Miss, hit, sinking and final loss, with alternating turns after hits.
    targets = [99, 0, 1, 2, 3, 4, 20, 21, 22, 23, 40, 41, 42, 60, 61, 62, 80, 81]
    for index, cell in enumerate(targets):
        duel.a.fire(cell)
        if index == len(targets) - 1:
            duel.until(lambda: (duel.a.state, duel.b.state) == ("WON", "LOST"))
        else:
            duel.until(lambda: (duel.a.state, duel.b.state) == ("PEER_TURN", "MY_TURN"))
            duel.b.fire(90 + index if index < 10 else 10 + index - 10)
            duel.until(lambda: (duel.a.state, duel.b.state) == ("MY_TURN", "PEER_TURN"))
    assert len(duel.b.board.hits) == len(targets)
    assert len(duel.a.enemy_ships) == 5
    assert duel.a.turn == duel.b.turn
    assert all(e.a != Op.ACK for _, e in duel.frames if e.op == Op.ACK)


def test_retry_budget_pause_and_resume_same_shot():
    duel = Duel()
    duel.start()
    duel.drop = lambda source, e: e.op == Op.SHOT
    duel.a.fire(99)
    duel.until(lambda: duel.a.state == "PAUSED")
    shots = [e for s, e in duel.frames if e.op == Op.SHOT]
    assert len(shots) == 3 and len(set(shots)) == 1
    assert duel.a.turn == 0 and not duel.a.enemy
    for _ in range(100):
        duel.step()
    assert len([e for s, e in duel.frames if e.op == Op.SHOT]) == 3
    duel.drop = lambda s, e: False
    duel.a.resume()
    duel.until(lambda: duel.b.state == "MY_TURN")
    assert duel.a.enemy[99] == Op.MISS


def test_early_result_does_not_get_overwritten_by_tx_done():
    duel = Duel()
    duel.start()
    callbacks = []
    duel.a.send = lambda e, cb: callbacks.append(cb)
    duel.a.fire(99)
    duel.a.tick()
    duel.a.receive("B", duel.a.session, Event(Op.MISS, 0, 99), 145500000)
    callbacks[0](True)
    assert duel.a.state == "ACK_QUEUED" and duel.a.turn == 1


def test_conflicting_result_and_foreign_session_cannot_change_board():
    duel = Duel()
    duel.start()
    duel.a.fire(99)
    duel.a.receive("B", duel.a.session + 1, Event(Op.HIT, 0, 99), 145500000)
    assert not duel.a.enemy
    duel.a.receive("B", duel.a.session, Event(Op.MISS, 0, 98), 145500000)
    assert duel.a.state == "PAUSED" and not duel.a.enemy


def test_simultaneous_invites_choose_smaller_callsign():
    duel = Duel()
    duel.a.invite("B", 145500000)
    session = duel.a.session
    duel.b.invite("A", 145500000)
    duel.step()
    assert duel.a.state == "INVITE_SENT"
    assert duel.b.state == "INVITE_RECEIVED" and duel.b.session == session


@pytest.mark.parametrize("op", [Op.RESIGN, Op.CANCEL])
def test_terminal_ack_loss_is_recovered(op):
    duel = Duel()
    duel.start()
    dropped = []
    def drop(source, e):
        if e.op == Op.ACK and e.a == op and not dropped:
            dropped.append(e)
            return True
        return False
    duel.drop = drop
    duel.a.end(op)
    duel.until(lambda: not duel.a.pending and not duel.b.pending)
    assert duel.a.state == ("LOST" if op == Op.RESIGN else "CANCELLED")


def test_wire_format_and_rejection():
    frame = Event(Op.SHOT, 0, 62).frame("ABCDEFGHI", "JKLMNOPQR", 123)
    assert frame.next_hop == "W15003E00"
    assert len(frame.encode()) == 44
    assert decode(ControlFrame.decode(frame.encode())) == Event(Op.SHOT, 0, 62)
    for field, value in [("ttl", 2), ("flags", Flags.ACK_REQUIRED), ("next_hop", "W15006400"), ("source", "ABCDEFGHIJ")]:
        bad = Event(Op.SHOT, 0, 62).frame("A", "B", 1)
        setattr(bad, field, value)
        with pytest.raises(ValueError):
            decode(bad)


def test_board_edges_touching_and_random_fleet():
    board = Board()
    board.place(0, 5)
    with pytest.raises(ValueError):
        board.place(15, 4)
    with pytest.raises(ValueError):
        board.place(99, 2)
    for _ in range(20):
        board.randomize()
        assert board.valid
        assert len({c for s in board.ships for c in ship_cells(*s)}) == 17


def test_orchestrator_discards_ws_before_discovery_and_heard():
    from guardian.session.orchestrator import Orchestrator
    from guardian.session.transport import LoopbackBus
    from guardian.routing import RouteTable
    bus = LoopbackBus()
    net = Orchestrator("B", bus.endpoint("B"), RouteTable())
    received = []
    net.on_warships_frame = received.append
    net.discovery.receive = lambda *a, **k: pytest.fail("WS entered discovery")
    net._on_frame(Event(Op.INVITE, a=1).frame("A", "C", 1))
    assert not received and not net.heard.active(time.monotonic())
    net._on_frame(Event(Op.INVITE, a=1).frame("A", "B", 1))
    assert len(received) == 1 and bus.idle


@pytest.mark.parametrize("confirmed", [Op.READY, Op.MISS, Op.HIT, Op.SUNK, Op.LOSS])
def test_each_result_ack_loss(confirmed):
    dropped = []
    def drop(source, e):
        if e.op == Op.ACK and e.a == confirmed and not dropped:
            dropped.append(e)
            return True
        return False
    duel = Duel(drop)
    duel.start()
    targets = [99, 0, 1, 2, 3, 4, 20, 21, 22, 23, 40, 41, 42, 60, 61, 62, 80, 81]
    for index, cell in enumerate(targets):
        duel.a.fire(cell)
        if index == len(targets) - 1:
            duel.until(lambda: (duel.a.state, duel.b.state) == ("WON", "LOST"))
        else:
            duel.until(lambda: (duel.a.state, duel.b.state) == ("PEER_TURN", "MY_TURN"))
            duel.b.fire(90 + index if index < 10 else index)
            duel.until(lambda: (duel.a.state, duel.b.state) == ("MY_TURN", "PEER_TURN"))
    assert dropped


def test_duplicate_flood_is_bounded():
    duel = Duel()
    duel.start()
    duel.a.fire(99)
    duel.until(lambda: duel.b.state == "MY_TURN")
    for _ in range(1000):
        duel.b.receive("A", duel.a.session, Event(Op.SHOT, 0, 99), 145500000)
    assert len(duel.b.pending) <= 1
    assert duel.b.board.hits == {99}
    assert duel.b.turn == 1


def test_service_requires_beacon_on_same_known_channel_and_pauses_on_qsy():
    from guardian.warships.service import WarshipsService
    frequency = [145500000]
    transport = SimpleNamespace(last_frame_snr=12)
    net = SimpleNamespace(transport=transport, ack_timeout=8)
    operations = SimpleNamespace(config=SimpleNamespace(callsign="A"), net=net,
        current_frequency=lambda: frequency[0], _beacon_block_reason=lambda: "RX only",
        audio_transport=None)
    service = WarshipsService(operations)
    service.bind()
    with pytest.raises(ValueError):
        service.invite("B")
    service.beacon(ControlFrame(FrameType.BEACON, "B"), frequency[0])
    service.invite("B")
    service.tick()
    assert service.game.state == "INVITE_SENT"
    assert all(p.attempts == 0 for p in service.game.pending.values())
    frequency[0] += 25000
    service.tick()
    assert service.game.state == "PAUSED" and not service.neighbors()
    with pytest.raises(ValueError):
        service.resume()


@pytest.mark.parametrize("mode", ["ok", "fail", "blocked", "stopped", "suspended"])
def test_physical_tx_completion_is_per_frame_and_after_ptt_release(mode):
    import threading
    import numpy as np
    from guardian.modem.audio import AudioControlTransport
    events = []
    def play(*a, **k):
        events.append("play")
        if mode == "fail":
            raise RuntimeError("device failed")
    transport = AudioControlTransport(ptt=lambda on: events.append(on))
    transport._sd = SimpleNamespace(play=play, wait=lambda: None)
    transport.modem.modulate = lambda payload: np.zeros(10)
    transport.tx_lead_seconds = transport.tx_tail_seconds = transport.tx_guard_seconds = 0
    transport._stopped = mode == "stopped"
    transport._tx_suspended = mode == "suspended"
    completed = threading.Event()
    def done(ok):
        events.append(("done", ok))
        completed.set()
    transport.send(Event(Op.SHOT, 0, 99).frame("A", "B", 1),
                   on_complete=done, allowed=lambda: mode != "blocked")
    assert completed.wait(3)
    assert events[-1] == ("done", mode == "ok")
    if mode in {"ok", "fail"}:
        assert events.index(False) < len(events) - 1
    else:
        assert True not in events


def test_services_on_three_station_graph_never_relay():
    from guardian.warships.service import WarshipsService
    from guardian.session.orchestrator import Orchestrator
    from guardian.session.transport import GraphRadioBus
    from guardian.routing import RouteTable
    bus = GraphRadioBus([("A", "B"), ("B", "C")])
    services = {}
    for call in "ABC":
        endpoint = bus.endpoint(call)
        original = endpoint.send
        def send(frame, on_complete=None, allowed=None, original=original):
            assert allowed is None or allowed()
            original(frame)
            if on_complete:
                on_complete(True)
        endpoint.send = send
        endpoint.channel_busy = lambda: False
        net = Orchestrator(call, endpoint, RouteTable())
        net.channel_frequency = lambda: 145500000
        ops = SimpleNamespace(config=SimpleNamespace(callsign=call), net=net,
            current_frequency=lambda: 145500000, audio_transport=endpoint,
            _net_idle=lambda: True, payload_handoff_pending=lambda: False,
            workers=SimpleNamespace(is_active=lambda name: False), vara=None)
        service = WarshipsService(ops)
        service.bind()
        services[call] = service
        net.beacon()
    bus.pump()
    a, b, c = (services[call] for call in "ABC")
    assert set(a.neighbors()) == {"B"}
    with pytest.raises(ValueError):
        a.invite("C")
    a.invite("B")
    for _ in range(4):
        for service in services.values():
            service.tick()
        bus.pump()
    assert b.game.state == "INVITE_RECEIVED" and c.game.state == "LOBBY"
    b.game.accept()
    for _ in range(6):
        for service in services.values():
            service.tick()
        bus.pump()
    assert a.game.state == b.game.state == "PLACING"
    for game in (a.game, b.game):
        game.board.randomize()
        game.ready()
    for _ in range(6):
        for service in services.values():
            service.tick()
        bus.pump()
    assert a.game.state == "MY_TURN" and b.game.state == "PEER_TURN"
    a.game.fire(99)
    for _ in range(6):
        for service in services.values():
            service.tick()
        bus.pump()
    assert b.game.state == "MY_TURN" and a.game.state == "PEER_TURN"
    assert c.game.state == "LOBBY" and not c.game.history


def test_busy_invitation_duplicates_keep_same_response():
    game = Game("B", lambda *a: None, lambda: 100)
    game.peer, game.session, game.channel = "A", 1, 145500000
    game.state = "DECLINED"
    game.queue(Event(Op.BUSY))
    game.receive("A", 1, Event(Op.INVITE, a=1), 145500000)
    assert set(game.pending) == {Event(Op.BUSY)}
