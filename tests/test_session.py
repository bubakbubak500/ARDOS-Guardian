from guardian.session import LoopbackBus, Orchestrator, SessionState
from guardian.routing import Route, RouteTable


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
