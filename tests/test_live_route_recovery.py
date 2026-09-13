"""Link adverts route real session handshakes; RREQ is recovery only."""
import pytest

from guardian.config import StationConfig
from guardian.protocol import ControlFrame, FrameType
from guardian.routing import DiscoveryEngine, DISCOVERY_ASSISTED
from guardian.session import GraphRadioBus, Orchestrator, SessionState


def _network(edges, *, drop=None):
    frames, transfers = [], []
    bus = GraphRadioBus(edges, drop=drop, monitor=lambda call, frame: frames.append((call, frame)))
    config = StationConfig()
    stations = {}
    for call in sorted({call for edge in edges for call in edge}):
        stations[call] = Orchestrator(
            call, bus.endpoint(call), relay=True, auto_complete=False,
            discovery_mode=config.discovery_mode,
            discovery_forward=config.discovery_forward,
            discovery_auto_use=config.discovery_auto_use,
            discovery_frame_budget=config.discovery_frame_budget,
            link_advert_enabled=config.link_advert_enabled,
            begin_transfer=lambda msg, call=call: transfers.append((call, msg)),
        )
        stations[call].discovery.jitter_min = stations[call].discovery.jitter_max = 0
    return bus, stations, frames, transfers


def _drive(network, start, stop):
    bus, stations, _, transfers = network
    for step in range(int(start * 4), int(stop * 4)):
        for station in stations.values():
            station.tick(step / 4)
        bus.pump()
        # The control handshake must actually start each leg. Only then does
        # the fake payload backend report reception, as a real modem would.
        while transfers:
            call, msg = transfers.pop(0)
            receiver = stations[msg.next_hop]
            inbound = receiver.sessions[msg.msg_id]
            assert inbound.state is SessionState.RECEIVING
            inbound.body, inbound.payload_bytes = msg.body, msg.payload_bytes
            stations[call]._on_send_done(msg, True)
            receiver.notify_payload_delivered(msg.msg_id)
            bus.pump()


@pytest.mark.parametrize("edges", [
    {("A", "B"), ("B", "C")},
    {("A", "B"), ("B", "D"), ("D", "C")},
])
def test_fresh_adverts_deliver_across_relays_with_all_rrep_dropped(edges):
    network = _network(edges, drop=lambda sender, receiver, frame: frame.type is FrameType.MULTIHOP_RREP)
    _, stations, frames, _ = network
    _drive(network, 0, 120)
    route = stations["A"].discovery.routes.best("C", 120, approved_only=True)
    assert route is not None and route.source == "link-advert"
    frames.clear()
    message = stations["A"].send_message("C", "payload", msg_id=11801, payload_bytes=b"data" * 7680)
    assert message.state is SessionState.ANNOUNCING and message.next_hop == "B"
    _drive(network, 120, 125)
    assert message.state is SessionState.DELIVERED
    assert stations["C"].sessions[message.msg_id].payload_bytes == b"data" * 7680
    assert not any(frame.type in {FrameType.MULTIHOP_RREQ, FrameType.ROUTE_QUERY} for _, frame in frames)


def _live_engine():
    engine = DiscoveryEngine("A", lambda frame: None, mode=DISCOVERY_ASSISTED,
                             auto_use=True, link_advert_enabled=True)
    for owner, peer in [("A", "B"), ("B", "A"), ("B", "C"), ("C", "B")]:
        engine.live_topology.record(owner, peer, 0, penalty=0, advert_id=1, last_sender=owner)
    engine._rebuild_live_routes()
    return engine


def test_failure_degrades_live_route_without_revoking_or_refreshing_evidence():
    engine = _live_engine()
    route = engine.routes.best("C", 0, approved_only=True)
    original_metric, expires = route.metric, route.expires_at
    assert expires == 1800  # The shipped lifetime is 30 minutes.
    engine.routes.mark_failure("C", "B")
    engine.configure(auto_use=True)
    assert engine.routes.best("C", 1, approved_only=True) is route
    assert route.metric > original_metric and route.failures == 1
    engine.tick(900)
    engine._rebuild_live_routes()
    route = engine.routes.best("C", 900, approved_only=True)
    assert route.failures == 1 and route.expires_at == expires
    engine.routes.mark_success("C", "B", 1000)
    assert route.failures == 0 and route.expires_at == expires
    assert engine.routes.best("C", 1799, approved_only=True) is route
    assert engine.routes.best("C", 1800, approved_only=True) is None


def test_all_announce_retries_exhausted_try_live_alternative_before_rreq():
    reject_b = [False]
    network = _network(
        {("A", "B"), ("B", "C"), ("A", "E"), ("E", "C")},
        drop=lambda sender, receiver, frame: reject_b[0] and sender == "B"
        and receiver == "A" and frame.type is FrameType.ACK_HAVE,
    )
    _, stations, frames, _ = network
    _drive(network, 0, 120)
    reject_b[0] = True
    frames.clear()
    sender = stations["A"]
    sender.ack_timeout = 1
    b_events = []
    stations["B"].on_event = lambda msg, event: b_events.append(event)
    message = sender.send_message("C", "alternative", msg_id=11802)
    assert message.next_hop == "B"
    _drive(network, 120, 135)
    assert message.state is SessionState.DELIVERED and message.next_hop == "E"
    # B still hears A's broadcasts. START_VARA addressed to E must not start
    # a second modem receiver at the abandoned first hop.
    assert not any(event.startswith("receiving payload") for event in b_events)
    assert "B" in message.failed_hops
    assert not any(frame.type is FrameType.MULTIHOP_RREQ for _, frame in frames)
    failed = [r for r in sender.discovery.routes.routes(135) if r.destination == "C" and r.next_hop == "B"]
    assert failed and failed[0].approved and failed[0].failures == 1


def test_exhausted_path_can_recover_with_rreq_but_next_message_reuses_live_route():
    network = _network({("A", "B"), ("B", "C")})
    _, stations, frames, _ = network
    _drive(network, 0, 120)
    sender = stations["A"]
    msg = sender.send_message("C", "first", msg_id=11803)
    for _ in range(3):
        sender._announce_timeout(msg)
    assert msg.state is SessionState.MULTIHOP_DISCOVERY
    assert sender.discovery.routes.best("C", 120, approved_only=True).failures == 1
    # A waiting recovery must not immediately reselect the failed first hop.
    sender._resume_automatic_routes()
    assert msg.state is SessionState.MULTIHOP_DISCOVERY
    frames.clear()
    again = sender.send_message("C", "second", msg_id=11804)
    assert again.state is SessionState.ANNOUNCING and again.next_hop == "B"
    assert not any(frame.type is FrameType.MULTIHOP_RREQ for _, frame in frames)


def test_payload_failure_keeps_live_route_available_but_degraded():
    network = _network({("A", "B"), ("B", "C")})
    _, stations, _, _ = network
    _drive(network, 0, 120)
    sender = stations["A"]
    message = sender.send_message("C", "first", msg_id=11805)
    sender._enter(message, SessionState.TRANSFERRING)
    sender._on_send_done(message, False)
    route = sender.discovery.routes.best("C", 120, approved_only=True)
    assert message.state is SessionState.FAILED
    assert route is not None and route.failures == 1


def test_one_lost_ack_retries_the_known_hop_without_discovery():
    lost = []
    def drop(sender, receiver, frame):
        if sender == "B" and receiver == "A" and frame.type is FrameType.ACK_HAVE and not lost:
            lost.append(frame)
            return True
        return False
    network = _network({("A", "B"), ("B", "C")}, drop=drop)
    _, stations, frames, _ = network
    _drive(network, 0, 120)
    frames.clear()
    stations["A"].ack_timeout = 1
    message = stations["A"].send_message("C", "retry", msg_id=11806)
    _drive(network, 120, 130)
    assert lost and message.state is SessionState.DELIVERED
    assert not any(frame.type is FrameType.MULTIHOP_RREQ for _, frame in frames)
    assert stations["A"].discovery.routes.best("C", 130).failures == 0


def test_expired_evidence_requires_discovery_but_fresh_evidence_does_not():
    sender = Orchestrator("A", GraphRadioBus(set()).endpoint("A"),
                          discovery_mode=DISCOVERY_ASSISTED, discovery_auto_use=True)
    sender.discovery = _live_engine()
    sender.tick(1799)
    fresh = sender.send_message("C", "fresh", msg_id=11807)
    assert fresh.state is SessionState.ANNOUNCING
    sender.tick(1800)
    expired = sender.send_message("C", "expired", msg_id=11808)
    assert expired.state is SessionState.MULTIHOP_DISCOVERY


def test_relay_does_not_send_payload_back_to_previous_hop():
    from guardian.session.orchestrator import Message
    relay = Orchestrator("B", GraphRadioBus(set()).endpoint("B"), relay=True)
    relay.discovery.routes.learn("C", "A", 2, 0, 1, 0, source="link-advert", approved=True)
    relay.discovery.routes.learn("C", "D", 3, 0, 2, 0, source="link-advert", approved=True)
    inbound = Message(msg_id=11809, source="A", final_dest="C", next_hop="B", direction="in")
    relay._maybe_relay(inbound)
    outbound = relay.sessions[inbound.msg_id]
    assert outbound.next_hop == "D" and outbound.previous_hop == "A"


def test_late_control_from_abandoned_hop_cannot_affect_alternative():
    sender = Orchestrator("A", GraphRadioBus(set()).endpoint("A"))
    msg = sender.send_message("C", "alternative", msg_id=11810, next_hop="E")
    for kind in (FrameType.BUSY, FrameType.CANCEL, FrameType.ACK_HAVE):
        sender._on_frame(ControlFrame(kind, source="B", destination="C", next_hop="A", message_id=msg.msg_id))
        assert msg.state is SessionState.ANNOUNCING
    sender._enter(msg, SessionState.TRANSFERRING)
    sender._on_frame(ControlFrame(FrameType.RECEIVED, source="B", destination="C", next_hop="A", message_id=msg.msg_id))
    assert msg.state is SessionState.TRANSFERRING


def test_live_route_cancels_an_rreq_waiting_in_audio_queue():
    from guardian.modem.audio import AudioControlTransport
    transport = AudioControlTransport()
    transport._tx_suspended = True
    sender = Orchestrator("A", transport, discovery_mode=DISCOVERY_ASSISTED,
                          discovery_auto_use=True)
    msg = sender.send_message("C", "queued", msg_id=11811)
    query_id = msg.discovery_query_id
    assert any(frame.type is FrameType.MULTIHOP_RREQ for kind, frame in transport._deferred_tx)
    sender.discovery.routes.learn("C", "B", 2, 0, 9, 0, source="link-advert", approved=True)
    sender._resume_automatic_routes()
    assert msg.state is SessionState.ANNOUNCING and msg.next_hop == "B"
    assert query_id not in sender.discovery.pending
    assert [frame.type for kind, frame in transport._deferred_tx] == [FrameType.HAVE_MSG]


def test_lost_custody_receipt_is_repeated_while_b_is_transferring_to_c():
    lost = []
    def drop(sender, receiver, frame):
        if sender == "B" and receiver == "A" and frame.type is FrameType.RECEIVED and not lost:
            lost.append(frame)
            return True
        return False
    network = _network({("A", "B"), ("B", "C")}, drop=drop)
    bus, stations, frames, transfers = network
    _drive(network, 0, 120)
    sender, relay, receiver = stations["A"], stations["B"], stations["C"]
    msg = sender.send_message("C", "custody", msg_id=11812)
    bus.pump()
    call, outbound = transfers.pop(0)
    assert call == "A"
    sender._on_send_done(outbound, True)
    relay.notify_payload_delivered(msg.msg_id)
    bus.pump()
    assert lost and msg.state is SessionState.TRANSFERRING
    relay_leg = relay.sessions[msg.msg_id]
    assert relay_leg.state is SessionState.TRANSFERRING
    # C's payload completion is delayed. A asks B to repeat its lost receipt.
    sender.tick(120 + max(sender.ack_timeout, sender.control_exchange_timeout) + 1)
    bus.pump()
    assert msg.state is SessionState.CONFIRMED
    assert relay.sessions[msg.msg_id] is relay_leg
    assert relay_leg.state is SessionState.TRANSFERRING
    assert len(transfers) == 1 and transfers[0][0] == "B"
    relay._on_send_done(relay_leg, True)
    receiver.notify_payload_delivered(msg.msg_id)
    bus.pump()
    assert msg.state is SessionState.DELIVERED
