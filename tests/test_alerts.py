from guardian.protocol import (
    ALERTS,
    MAX_CONTROL_FRAME_BYTES,
    ControlFrame,
    FrameType,
    Priority,
    alert_kind,
    decode_alert,
    encode_alert,
    max_note_length,
)
from guardian.session import LoopbackBus, Orchestrator
from guardian.session.orchestrator import (
    ALERT_REPEATS,
    ALERT_REPEAT_GAP,
    ALERT_RELAY_MAX,
    ALERT_TTL,
    alert_priority,
)


def _alert_frame(source: str, note: str, code: int = 0x01) -> ControlFrame:
    return ControlFrame(
        type=FrameType.ALERT,
        source=source,
        destination=encode_alert(code, note, source),
        next_hop="",
        message_id=0xDEADBEEF,
        priority=Priority.EMERGENCY,
        ttl=ALERT_TTL,
    )


def test_full_note_still_fits_the_unchanged_control_frame() -> None:
    # The frame format is fixed; the budget is what has to give. Check the
    # longest callsign a note is sized against and a longer one besides.
    for callsign in ("OK7PS", "OK2IPW", "DL1ABCD"):
        note = "X" * max_note_length(callsign)
        frame = _alert_frame(callsign, note)
        assert len(frame.encode()) <= MAX_CONTROL_FRAME_BYTES
        assert decode_alert(frame.destination) == (0x01, note)


def test_note_is_truncated_and_forced_to_ascii_rather_than_refused() -> None:
    room = max_note_length("OK7PS")
    payload = encode_alert(0x03, "Ř" + "A" * (room + 10), "OK7PS")
    code, note = decode_alert(payload)
    assert code == 0x03
    assert len(note) == room
    assert note.isascii()
    assert len(_alert_frame("OK7PS", note, code).encode()) <= MAX_CONTROL_FRAME_BYTES


def test_alert_codes_are_unique_and_carry_a_priority() -> None:
    codes = [kind.code for kind in ALERTS]
    assert len(codes) == len(set(codes))
    assert alert_priority(0x01) is Priority.EMERGENCY
    assert alert_priority(0x11) is Priority.PRIORITY
    # A code this build has never heard of must still be displayable.
    assert alert_kind(0x77) is None
    assert alert_priority(0x77) is Priority.ROUTINE


def _net(*callsigns: str):
    bus = LoopbackBus()
    stations = []
    seen: list[tuple[str, int, str, int, bool]] = []
    for call in callsigns:
        station = Orchestrator(call, bus.endpoint(call.lower()), auto_route=False)

        def record(frame, mine, owner=station):
            code, note = decode_alert(frame.destination)
            seen.append((owner.callsign, code, note, frame.ttl, mine))

        station.on_alert = record
        stations.append(station)
    return bus, stations, seen


def _run(bus, stations, seconds: float = 40.0, step: float = 0.5) -> None:
    now = 0.0
    while now <= seconds:
        for station in stations:
            station.tick(now)
        bus.pump()
        now += step


def test_alert_reaches_every_station_exactly_once_and_loses_a_hop() -> None:
    bus, (a, b, c), seen = _net("OK7PS", "OK2IPW", "OK1AAA")
    a.tick(0.0)
    a.send_alert(0x01, "POZAR SKLAD B")
    _run(bus, (a, b, c))

    assert [row for row in seen if row[0] == "OK7PS"] == [
        ("OK7PS", 0x01, "POZAR SKLAD B", ALERT_TTL, True)
    ]
    for call in ("OK2IPW", "OK1AAA"):
        heard = [row for row in seen if row[0] == call]
        # Shown once, no matter how many repeats and relays carried it.
        assert len(heard) == 1
        assert heard[0][1:] == (0x01, "POZAR SKLAD B", ALERT_TTL, False)


def test_relay_keeps_the_originator_and_decrements_ttl() -> None:
    sent: list[ControlFrame] = []
    bus = LoopbackBus()
    listener = Orchestrator("OK2IPW", bus.endpoint("b"), auto_route=False)
    listener.transport.send = sent.append          # type: ignore[method-assign]
    listener.tick(0.0)

    listener._on_frame(_alert_frame("OK7PS", "QSY 7100"))
    listener.tick(ALERT_RELAY_MAX + 1.0)

    assert len(sent) == 1
    relayed = sent[0]
    assert relayed.source == "OK7PS"               # not the relaying station
    assert relayed.next_hop == "OK2IPW"            # who passed it on
    assert relayed.ttl == ALERT_TTL - 1
    assert relayed.message_id == 0xDEADBEEF        # same id, so it dedups
    assert decode_alert(relayed.destination) == (0x01, "QSY 7100")


def test_last_hop_is_not_relayed_again() -> None:
    sent: list[ControlFrame] = []
    bus = LoopbackBus()
    station = Orchestrator("OK1AAA", bus.endpoint("c"), auto_route=False)
    station.transport.send = sent.append           # type: ignore[method-assign]
    station.tick(0.0)

    expiring = _alert_frame("OK7PS", "end of line")
    expiring.ttl = 1
    station._on_frame(expiring)
    station.tick(ALERT_RELAY_MAX + 1.0)

    assert sent == []


def test_source_repeats_the_same_frame_spread_over_time() -> None:
    sent: list[ControlFrame] = []
    bus = LoopbackBus()
    station = Orchestrator("OK7PS", bus.endpoint("a"), auto_route=False)
    station.transport.send = sent.append           # type: ignore[method-assign]
    station.tick(0.0)
    station.send_alert(0x30, "OBEC HORNI")

    station.tick(0.0)
    assert len(sent) == 1, "first copy goes out immediately"
    station.tick(ALERT_REPEAT_GAP / 2)
    assert len(sent) == 1, "repeats are spaced, not burst"
    station.tick(ALERT_REPEATS * ALERT_REPEAT_GAP)
    assert len(sent) == ALERT_REPEATS
    assert {frame.message_id for frame in sent} == {sent[0].message_id}


def test_relays_from_different_stations_do_not_key_together() -> None:
    delays = set()
    for call in ("OK7PS", "OK2IPW", "OK1AAA", "OM3XYZ"):
        bus = LoopbackBus()
        station = Orchestrator(call, bus.endpoint(call.lower()), auto_route=False)
        queued: list[tuple[float, ControlFrame]] = station._alert_queue
        station.tick(0.0)
        station._on_frame(_alert_frame("OK5ZZZ", "flood"))
        delays.add(round(queued[0][0], 3))
    assert len(delays) == 4


def test_a_failing_display_callback_does_not_stop_the_flood() -> None:
    sent: list[ControlFrame] = []
    bus = LoopbackBus()
    station = Orchestrator("OK2IPW", bus.endpoint("b"), auto_route=False)
    station.transport.send = sent.append           # type: ignore[method-assign]

    def explode(_frame, _mine):
        raise RuntimeError("UI is gone")

    station.on_alert = explode
    station.tick(0.0)
    station._on_frame(_alert_frame("OK7PS", "still relays"))
    station.tick(ALERT_RELAY_MAX + 1.0)

    assert len(sent) == 1


def test_the_same_alert_on_another_frequency_keeps_its_identity() -> None:
    # The frequency sweep re-sends one frame on each channel. Reusing the id is
    # the point: a station in earshot of two of them shows the alert once and
    # relays it once, exactly as if it had heard a repeat.
    sent: list[ControlFrame] = []
    bus = LoopbackBus()
    station = Orchestrator("OK7PS", bus.endpoint("a"), auto_route=False)
    station.transport.send = sent.append           # type: ignore[method-assign]
    station.tick(0.0)
    frame = station.send_alert(0x01, "POZAR SKLAD B")
    station.tick(ALERT_REPEATS * ALERT_REPEAT_GAP)
    home_copies = len(sent)

    station.retransmit_alert(frame)

    assert len(sent) == home_copies + 1
    assert sent[-1] is frame
    assert {copy.message_id for copy in sent} == {frame.message_id}
    # A neighbour's relay of the swept copy still must not come back at us.
    relayed = ControlFrame(
        type=FrameType.ALERT,
        source="OK7PS",
        destination=frame.destination,
        next_hop="OK2IPW",
        message_id=frame.message_id,
        priority=frame.priority,
        ttl=frame.ttl - 1,
    )
    station._on_frame(relayed)
    station.tick(ALERT_RELAY_MAX + 1.0)
    assert len(sent) == home_copies + 1


def test_pending_alert_count_is_what_the_sweep_waits_for() -> None:
    # The sweep must not tune away while copies are still queued for the air.
    bus = LoopbackBus()
    station = Orchestrator("OK7PS", bus.endpoint("a"), auto_route=False)
    station.tick(0.0)
    station.send_alert(0x02, "ZRANENI")

    assert station.alerts_pending() == ALERT_REPEATS
    station.tick(ALERT_REPEATS * ALERT_REPEAT_GAP)
    assert station.alerts_pending() == 0
