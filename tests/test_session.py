from guardian.protocol import ControlFrame, FrameType
from guardian.session import LoopbackBus, Message, Orchestrator, SessionState
from guardian.routing import Route, RouteTable
from guardian.session.orchestrator import (
    CONFIRM_TIMEOUT,
    TRANSFER_TIMEOUT,
    session_transfer_timeout_for,
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
