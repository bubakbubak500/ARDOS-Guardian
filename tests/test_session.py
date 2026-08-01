import pytest

from guardian.protocol import ControlFrame, Flags, FrameType
from guardian.routing import is_locator
from guardian.session import LoopbackBus, Message, Orchestrator, SessionState
from guardian.routing import Route, RouteTable
from guardian.session.orchestrator import (
    CONFIRM_TIMEOUT,
    TRANSFER_TIMEOUT,
    parse_working_channel_token,
    session_transfer_timeout_for,
    working_channel_token,
)


def _drain(bus: LoopbackBus, *stations: Orchestrator, now: float = 1.0) -> None:
    for _ in range(20):
        delivered = bus.pump()
        for station in stations:
            station.tick(now)
        if delivered == 0 and bus.idle:
            return
        now += 0.25
    raise AssertionError("loopback bus did not become idle")


def test_direct_session_reaches_delivered_without_changing_payload_contract() -> None:
    bus = LoopbackBus()
    sender = Orchestrator("OK7PS", bus.endpoint("sender"), auto_route=False)
    receiver = Orchestrator(
        "OK1AAA",
        bus.endpoint("receiver"),
        auto_complete=True,
        auto_route=False,
    )

    message = sender.send_message(
        "OK1AAA", "hello", msg_id=100, next_hop="OK1AAA"
    )
    _drain(bus, sender, receiver)

    assert message.state is SessionState.DELIVERED
    assert receiver.sessions[100].state is SessionState.DELIVERED
    assert sender.learned_paths["OK1AAA"] == "OK1AAA"


def test_default_session_emits_no_working_channel_negotiation() -> None:
    bus = LoopbackBus()
    sender = Orchestrator("OK7PS", bus.endpoint("sender"), auto_route=False)
    receiver = Orchestrator(
        "OK1AAA", bus.endpoint("receiver"), auto_complete=True, auto_route=False
    )
    sent = _spy(sender)

    message = sender.send_message(
        "OK1AAA", "hello", msg_id=101, next_hop="OK1AAA"
    )
    _drain(bus, sender, receiver)

    assert message.state is SessionState.DELIVERED
    assert not [
        frame
        for frame in sent
        if frame.type in (FrameType.WORKING_OFFER, FrameType.WORKING_ACK)
    ]


def test_opt_in_peers_agree_working_channel_before_starting_vara() -> None:
    channel = (145_550_000, "FM")
    token = working_channel_token(*channel)
    bus = LoopbackBus()
    sender = Orchestrator("OK7PS", bus.endpoint("sender"), auto_route=False)
    receiver = Orchestrator(
        "OK1AAA", bus.endpoint("receiver"), auto_complete=True, auto_route=False
    )
    sender.working_channel_offer = lambda peer: channel if peer == "OK1AAA" else None
    receiver.working_channel_accept = (
        lambda peer, offered: channel
        if peer == "OK7PS" and offered == token
        else None
    )
    sent = _spy(sender)

    message = sender.send_message(
        "OK1AAA", "hello", msg_id=102, next_hop="OK1AAA"
    )
    _drain(bus, sender, receiver)

    assert message.state is SessionState.DELIVERED
    assert (message.working_frequency_hz, message.working_mode) == channel
    assert message.working_token == token
    assert receiver.sessions[102].working_token == token
    kinds = [frame.type for frame in sent]
    assert kinds.index(FrameType.WORKING_OFFER) < kinds.index(FrameType.START_VARA)


def test_working_channel_token_reads_back_as_the_channel_it_encoded() -> None:
    # A station that follows a proposal instead of matching it has nothing but
    # the token to learn where the proposer wants to work.
    for channel in (
        (145_350_000, "FM"),
        (145_300_000, "NFM"),
        (7_053_000, "USB"),
        (0, "PKTFM"),
    ):
        assert parse_working_channel_token(working_channel_token(*channel)) == channel
    for bad in ("", "F", "1", "145350000Z", "14?350F"):
        with pytest.raises(ValueError):
            parse_working_channel_token(bad)


def test_mismatched_working_channels_cancel_without_starting_vara() -> None:
    bus = LoopbackBus()
    sender = Orchestrator("OK7PS", bus.endpoint("sender"), auto_route=False)
    receiver = Orchestrator("OK1AAA", bus.endpoint("receiver"), auto_route=False)
    sender.working_channel_offer = lambda _peer: (145_550_000, "FM")
    receiver.working_channel_accept = lambda _peer, _token: None
    sent = _spy(sender)

    message = sender.send_message(
        "OK1AAA", "hello", msg_id=103, next_hop="OK1AAA"
    )
    _drain(bus, sender, receiver)

    assert message.state is SessionState.CANCELLED
    assert receiver.sessions[103].state is SessionState.FAILED
    assert FrameType.START_VARA not in [frame.type for frame in sent]


def test_relay_offer_ranking_uses_signal_then_freshness_then_callsign() -> None:
    station = Orchestrator("OK7PS", LoopbackBus().endpoint("a"), auto_route=True)
    message = Message(
        104,
        "OK7PS",
        "OK9ZZZ",
        "",
        offers=["OK1WEAK", "OK2STRONG", "OK3UNKNOWN"],
        offer_quality={
            "OK1WEAK": (2.0, 12.0, 145_500_000),
            "OK2STRONG": (11.5, 10.0, 145_500_000),
            "OK3UNKNOWN": (None, 14.0, 145_500_000),
        },
    )

    assert station._best_offer(message) == "OK2STRONG"

    message.offers = ["OK2BBB", "OK1AAA"]
    message.offer_quality = {
        "OK2BBB": (8.0, 20.0, 145_500_000),
        "OK1AAA": (8.0, 20.0, 145_500_000),
    }
    assert station._best_offer(message) == "OK1AAA"


def test_direct_destination_offer_still_beats_a_stronger_relay() -> None:
    station = Orchestrator("OK7PS", LoopbackBus().endpoint("a"), auto_route=True)
    message = Message(
        105,
        "OK7PS",
        "OK9ZZZ",
        "",
        offers=["OK1RELAY", "OK9ZZZ"],
        offer_quality={
            "OK1RELAY": (18.0, 10.0, 145_500_000),
            "OK9ZZZ": (-2.0, 10.0, 145_500_000),
        },
    )

    assert station._best_offer(message) == "OK9ZZZ"


def test_route_offer_captures_its_own_signal_and_channel_measurement() -> None:
    station = Orchestrator("OK7PS", LoopbackBus().endpoint("a"), auto_route=True)
    station.channel_frequency = lambda: 145_500_000
    station.tick(25.0)
    message = station.send_message("OK9ZZZ", "hello", msg_id=106)
    station.transport.last_frame_snr = 7.25

    station._on_frame(
        ControlFrame(
            FrameType.ROUTE_OFFER,
            source="OK1AAA",
            destination="OK9ZZZ",
            next_hop="OK7PS",
            message_id=106,
        )
    )

    assert message.offer_quality["OK1AAA"] == (7.25, 25.0, 145_500_000)


def test_session_and_payload_agree_on_the_envelope_floor() -> None:
    # The session layer duplicates the payload floor to avoid depending on a
    # backend; if they drift, transfer deadlines stop matching reality.
    from guardian.payload.vara_p2p import MIN_WIRE_SIZE
    from guardian.session.orchestrator import _PAYLOAD_MIN_WIRE_SIZE

    assert _PAYLOAD_MIN_WIRE_SIZE == MIN_WIRE_SIZE


def test_received_from_the_final_destination_closes_the_session() -> None:
    # A lost DELIVERED frame used to leave the session in CONFIRMED forever,
    # so the shell kept showing "active transfers: 1" long after the message
    # had been delivered and filed.
    bus = LoopbackBus()
    sender = Orchestrator("OK7PS", bus.endpoint("sender"), auto_route=False)
    message = sender.send_message(
        "OK2IPW", "hello", msg_id=200, next_hop="OK2IPW"
    )
    message.state = SessionState.TRANSFERRING

    sender._rx_received(
        ControlFrame(
            type=FrameType.RECEIVED,
            source="OK2IPW",
            destination="OK2IPW",
            next_hop="OK2IPW",
            message_id=200,
        )
    )

    assert message.state is SessionState.DELIVERED
    assert message.state.terminal


def test_a_relay_confirmation_stops_counting_as_an_active_transfer() -> None:
    bus = LoopbackBus()
    sender = Orchestrator("OK7PS", bus.endpoint("sender"), auto_route=False)
    message = sender.send_message(
        "OK1AAA", "hello", msg_id=201, next_hop="OK2IPW"
    )
    message.state = SessionState.TRANSFERRING

    sender._rx_received(
        ControlFrame(
            type=FrameType.RECEIVED,
            source="OK2IPW",
            destination="OK1AAA",
            next_hop="OK2IPW",
            message_id=201,
        )
    )

    # Relayed: the next hop is not the final destination, so the end-to-end
    # receipt is still genuinely outstanding.
    assert message.state is SessionState.CONFIRMED
    sender.tick(message.t_state + CONFIRM_TIMEOUT - 1)
    assert message.state is SessionState.CONFIRMED

    sender.tick(message.t_state + CONFIRM_TIMEOUT + 1)
    assert message.state is SessionState.DELIVERED


def test_no_route_with_auto_route_disabled_attempts_destination_directly() -> None:
    bus = LoopbackBus()
    sender = Orchestrator("OK7PS", bus.endpoint("sender"), auto_route=False)

    message = sender.send_message("OK9ZZZ", "hello", msg_id=101)

    assert message.next_hop == "OK9ZZZ"
    assert message.state is SessionState.ANNOUNCING


def test_configured_empty_hop_is_direct_even_with_discovery_enabled() -> None:
    bus = LoopbackBus()
    routes = RouteTable([Route("OK1AAA", "")])
    sender = Orchestrator(
        "OK7PS",
        bus.endpoint("sender"),
        routes=routes,
        auto_route=True,
    )

    message = sender.send_message("OK1AAA", "hello", msg_id=102)

    assert message.next_hop == "OK1AAA"
    assert message.state is SessionState.ANNOUNCING


def test_inbound_payload_failure_sends_cancel_to_initiator() -> None:
    frames = []
    bus = LoopbackBus()
    receiver = Orchestrator("OK1AAA", bus.endpoint("receiver"))
    observer = bus.endpoint("observer")
    observer.on_frame = frames.append
    message = Message(
        103,
        "OK7PS",
        "OK1AAA",
        "OK1AAA",
        direction="in",
        state=SessionState.RECEIVING,
    )
    receiver.sessions[message.msg_id] = message

    receiver.notify_payload_delivered(message.msg_id, ok=False)
    bus.pump()

    assert message.state is SessionState.FAILED
    assert message.error == "payload CRC failed"
    assert [frame.type for frame in frames] == [FrameType.CANCEL]


def test_session_timeout_stays_above_scaled_payload_timeout() -> None:
    small = Message(104, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"x")
    large = Message(
        105,
        "OK7PS",
        "OK1AAA",
        "OK1AAA",
        payload_bytes=b"x" * 10_000,
    )

    assert session_transfer_timeout_for(small) == TRANSFER_TIMEOUT
    assert session_transfer_timeout_for(large) > TRANSFER_TIMEOUT


def test_a_heard_station_is_filed_with_its_signal_and_channel() -> None:
    # Both columns were dead in the Network workspace: nothing ever passed an
    # SNR to the registry, and the channel was not recorded at all.
    bus = LoopbackBus()
    station = Orchestrator("OK7PS", bus.endpoint("a"), auto_route=False)
    station.transport.last_frame_snr = 14.5
    station.channel_frequency = lambda: 145_237_500
    station.tick(100.0)

    station._on_frame(
        ControlFrame(
            type=FrameType.BEACON,
            source="OK2IPW",
            destination="",
            next_hop="",
            message_id=7,
        )
    )

    heard = station.heard.get("OK2IPW")
    assert (heard.last_snr, heard.last_freq_hz) == (14.5, 145_237_500)


def test_an_unmeasurable_channel_leaves_the_last_known_values_alone() -> None:
    # A simulated transport reports no SNR and a rig with no CAT no frequency.
    # Overwriting yesterday's reading with None would be worse than silence.
    bus = LoopbackBus()
    station = Orchestrator("OK7PS", bus.endpoint("a"), auto_route=False)
    station.heard.record("OK2IPW", 90.0, snr=9.0, freq_hz=7_100_000)
    station.channel_frequency = lambda: (_ for _ in ()).throw(OSError("no CAT"))
    station.tick(100.0)

    station._on_frame(
        ControlFrame(
            type=FrameType.BEACON,
            source="OK2IPW",
            destination="",
            next_hop="",
            message_id=8,
        )
    )

    heard = station.heard.get("OK2IPW")
    assert (heard.last_snr, heard.last_freq_hz) == (9.0, 7_100_000)
    assert heard.last_heard == 100.0


def _spy(station: Orchestrator) -> list[ControlFrame]:
    sent: list[ControlFrame] = []
    original = station.transport.send

    def record(frame):
        sent.append(frame)
        return original(frame)

    station.transport.send = record        # type: ignore[method-assign]
    return sent


def test_a_blind_hop_gets_one_query_and_two_announces_not_four_frames() -> None:
    # Discovery already put an unanswered ROUTE_QUERY on the air; three blind
    # HAVE_MSG on top of it is one transmission too many for a shared channel.
    bus = LoopbackBus()
    station = Orchestrator("OK7PS", bus.endpoint("a"), auto_route=True)
    sent = _spy(station)
    station.tick(0.0)
    message = station.send_message("OK9ZZZ", "hello", msg_id=300)

    now = 0.0
    while not message.state.terminal and now < 300.0:
        now += 1.0
        station.tick(now)
        bus.pump()

    assert message.state is SessionState.FAILED
    queries = [f for f in sent if f.type is FrameType.ROUTE_QUERY]
    announces = [f for f in sent if f.type is FrameType.HAVE_MSG]
    assert len(queries) == 1
    assert len(announces) == 2, "blind: initial announce plus a single retry"


def test_a_vouched_hop_keeps_all_three_announces() -> None:
    # The tighter budget is only for guesses; a configured route still gets
    # the full retry schedule.
    bus = LoopbackBus()
    routes = RouteTable([Route("OK9ZZZ", "OK2IPW")])
    station = Orchestrator("OK7PS", bus.endpoint("a"), routes=routes)
    sent = _spy(station)
    station.tick(0.0)
    message = station.send_message("OK9ZZZ", "hello", msg_id=301)

    now = 0.0
    while not message.state.terminal and now < 300.0:
        now += 1.0
        station.tick(now)
        bus.pump()

    announces = [f for f in sent if f.type is FrameType.HAVE_MSG]
    assert len(announces) == 3


def test_a_repeated_announce_is_answered_again_instead_of_ignored() -> None:
    # The initiator re-announces because it did not hear the ACK. Silence
    # from the responder stranded both sides: every retry burned against a
    # peer that had answered once into a lost frame.
    bus = LoopbackBus()
    responder = Orchestrator("OK2IPW", bus.endpoint("b"), auto_route=False)
    sent = _spy(responder)
    responder.tick(0.0)

    announce = ControlFrame(
        type=FrameType.HAVE_MSG, source="OK7PS", destination="OK2IPW",
        next_hop="OK2IPW", message_id=400,
    )
    responder._on_frame(announce)
    assert [f.type for f in sent] == [FrameType.ACK_HAVE]

    responder._on_frame(announce)          # retry: our ACK was lost on RF
    assert [f.type for f in sent] == [FrameType.ACK_HAVE, FrameType.ACK_HAVE]

    # A duplicate while already receiving must not restart anything.
    responder.sessions[400].state = SessionState.RECEIVING
    responder._on_frame(announce)
    assert len(sent) == 2


def test_slow_keying_is_negotiated_to_the_larger_request_on_both_sides() -> None:
    from guardian.protocol import decode_ptt_delay

    bus = LoopbackBus()
    sender = Orchestrator("OK7PS", bus.endpoint("a"), auto_route=False)
    receiver = Orchestrator(
        "OK2IPW", bus.endpoint("b"), auto_complete=True, auto_route=False
    )
    sender.ptt_delay_request = lambda: 300
    receiver.ptt_delay_request = lambda: 500

    message = sender.send_message("OK2IPW", "hi", msg_id=500, next_hop="OK2IPW")
    _drain(bus, sender, receiver)

    assert message.state is SessionState.DELIVERED
    assert message.ptt_delay_ms == 500, "initiator adopts the peer's larger ask"
    assert receiver.sessions[500].ptt_delay_ms == 500


def test_stations_without_the_setting_keep_todays_timing() -> None:
    bus = LoopbackBus()
    sender = Orchestrator("OK7PS", bus.endpoint("a"), auto_route=False)
    receiver = Orchestrator(
        "OK2IPW", bus.endpoint("b"), auto_complete=True, auto_route=False
    )

    message = sender.send_message("OK2IPW", "hi", msg_id=501, next_hop="OK2IPW")
    _drain(bus, sender, receiver)

    assert message.ptt_delay_ms == 0
    assert receiver.sessions[501].ptt_delay_ms == 0


def test_a_relay_negotiates_its_own_keying_not_the_previous_hops() -> None:
    # The gap agreed between A and B belongs to those two radios; when B
    # relays onward to C the next leg starts from B's own request.
    from guardian.protocol import decode_ptt_delay, encode_ptt_delay

    bus = LoopbackBus()
    relay = Orchestrator("OK2IPW", bus.endpoint("b"), auto_route=False, relay=True)
    sent = _spy(relay)
    relay.ptt_delay_request = lambda: 200
    relay.tick(0.0)
    relay.heard.record("OK1AAA", 0.0)      # so the relay resolves the hop

    inbound = ControlFrame(
        type=FrameType.HAVE_MSG, source="OK7PS", destination="OK1AAA",
        next_hop="OK2IPW", message_id=600,
        flags=encode_ptt_delay(Flags.NONE, 700),   # A asked for a huge gap
    )
    relay._on_frame(inbound)
    assert relay.sessions[600].ptt_delay_ms == 700

    relay.sessions[600].payload_bytes = b"x" * 32
    relay.notify_payload_delivered(600, ok=True)

    onward = [f for f in sent if f.type is FrameType.HAVE_MSG]
    assert len(onward) == 1
    assert decode_ptt_delay(onward[0].flags) == 200, "own ask, not A's 700"
    assert relay.sessions[600].ptt_delay_ms == 200


def test_the_slow_keying_field_survives_the_wire_and_old_builds() -> None:
    # The request rides in spare flag bits of the existing byte: encode,
    # transmit, decode -- and a build that never heard of it must still parse
    # the frame and echo the bits back unchanged.
    from guardian.protocol import (
        MAX_PTT_DELAY_MS,
        decode_ptt_delay,
        encode_ptt_delay,
    )

    flags = encode_ptt_delay(Flags.COMPRESSED, 300)
    assert decode_ptt_delay(flags) == 300
    assert flags & Flags.COMPRESSED, "existing flag bits are untouched"

    frame = ControlFrame(
        type=FrameType.HAVE_MSG, source="OK7PS", destination="OK1AAA",
        next_hop="OK2IPW", message_id=7, flags=flags,
    )
    decoded = ControlFrame.decode(frame.encode())
    assert decode_ptt_delay(decoded.flags) == 300
    assert decoded.flags & Flags.COMPRESSED

    # Overwrite semantics: a relay replaces the previous value, never ors it.
    assert decode_ptt_delay(encode_ptt_delay(flags, 100)) == 100
    assert decode_ptt_delay(encode_ptt_delay(flags, 0)) == 0
    # Rounding and cap.
    assert decode_ptt_delay(encode_ptt_delay(Flags.NONE, 250)) == 200
    assert decode_ptt_delay(encode_ptt_delay(Flags.NONE, 5_000)) == MAX_PTT_DELAY_MS


def test_a_beacon_carries_the_locator_and_only_a_beacon_sets_one() -> None:
    # The position rides in the address field, which every other frame type
    # uses for a real callsign -- reading one of those as a locator would put
    # a station on the map somewhere it has never been.
    from guardian.protocol import MAX_CONTROL_FRAME_BYTES

    bus = LoopbackBus()
    station = Orchestrator("OK7PS", bus.endpoint("a"), auto_route=False)
    sent = _spy(station)
    station.position = lambda: "JO70FB28MC"
    station.tick(0.0)

    station.beacon()

    assert len(sent) == 1
    assert sent[0].type is FrameType.BEACON
    assert sent[0].destination == "JO70FB28MC"
    assert len(sent[0].encode()) <= MAX_CONTROL_FRAME_BYTES

    # A peer's beacon puts them on the map.
    listener = Orchestrator("OK2IPW", bus.endpoint("b"), auto_route=False)
    listener.tick(0.0)
    listener._on_frame(sent[0])
    assert listener.heard.get("OK7PS").grid == "JO70FB28MC"

    # The hazard is a destination that *parses* as a locator: a group named
    # JN89HE is a perfectly ordinary thing to route to, and reading it as a
    # position would drop that station onto the map in Brno.
    assert is_locator("JN89HE")
    listener._on_frame(
        ControlFrame(
            type=FrameType.HAVE_MSG, source="OK9ZZZ", destination="JN89HE",
            next_hop="OK2IPW", message_id=1,
        )
    )
    assert listener.heard.get("OK9ZZZ").grid == ""


def test_a_station_with_no_position_beacons_exactly_as_before() -> None:
    bus = LoopbackBus()
    station = Orchestrator("OK7PS", bus.endpoint("a"), auto_route=False)
    sent = _spy(station)
    station.tick(0.0)

    station.beacon()                              # no position callback at all
    station.position = lambda: ""                 # or one that declines
    station.beacon()
    station.position = lambda: "not a locator"    # or nonsense
    station.beacon()
    station.position = lambda: (_ for _ in ()).throw(OSError("bad config"))
    station.beacon()

    assert len(sent) == 4
    assert all(frame.destination == "" for frame in sent)
    assert all(len(frame.encode()) == 22 for frame in sent)


def test_the_locator_fits_beside_even_the_longest_callsign() -> None:
    # An odd truncation would name a different square, so the room is always
    # rounded down to a whole locator pair.
    from guardian.protocol import MAX_CONTROL_FRAME_BYTES
    from guardian.session.orchestrator import beacon_locator_room

    # 16 is the longest callsign Settings accepts, and ten characters fit
    # beside it with room to spare -- the 25-character case is the guard
    # itself, kept because an oversize frame would mis-size the RX window
    # and every session timeout derived from it.
    assert beacon_locator_room("A" * 25) == 6
    for callsign in ("OK7PS", "OK1ABC/P", "VK9XYZ/MM", "A" * 16, "A" * 25):
        bus = LoopbackBus()
        station = Orchestrator(callsign, bus.endpoint("x"), auto_route=False)
        sent = _spy(station)
        station.position = lambda: "JO70FB28MC"
        station.tick(0.0)
        station.beacon()

        room = beacon_locator_room(callsign)
        assert room % 2 == 0, callsign
        assert len(sent[0].encode()) <= MAX_CONTROL_FRAME_BYTES, callsign
        assert sent[0].destination == "JO70FB28MC"[:room], callsign
