from guardian.protocol import ControlFrame, FrameType
from guardian.routing import (
    DISCOVERY_ASSISTED,
    DISCOVERY_MONITOR,
    DISCOVERY_OFF,
    DiscoveryEngine,
    DynamicRouteStore,
    Route,
    RouteTable,
    decode_discovery_metric,
    encode_discovery_metric,
)
from guardian.session import GraphRadioBus, Orchestrator, SessionState


def _engines(bus, calls, **overrides):
    engines = {}
    for call in calls:
        endpoint = bus.endpoint(call)
        engine = DiscoveryEngine(
            call,
            endpoint.send,
            mode=DISCOVERY_ASSISTED,
            forward=True,
            relay_enabled=call not in {"S1", "S2", "S3", "S4", "S5", "S6"},
            jitter_min=0,
            jitter_max=0,
            query_timeout=2,
            settle_time=0,
            **overrides,
        )
        endpoint.on_frame = lambda frame, engine=engine, endpoint=endpoint: engine.receive(
            frame, snr=endpoint.last_frame_snr
        )
        engines[call] = engine
    return engines


def _run(bus, engines, *, until=20.0, step=0.25):
    now = 0.0
    while now <= until:
        for engine in engines.values():
            engine.tick(now)
        bus.pump()
        now += step


def _sample_bus(**kwargs):
    return GraphRadioBus(
        {
            ("S6", "N1"),
            ("S5", "N1"),
            ("N1", "N2"),
            ("N2", "N3"),
            ("N3", "S1"),
            ("N3", "S2"),
            ("N2", "S3"),
            ("N2", "S4"),
        },
        **kwargs,
    )


def test_discovery_metric_roundtrip_is_bounded() -> None:
    assert decode_discovery_metric(encode_discovery_metric(4, 3)) == (4, 3)
    assert decode_discovery_metric(encode_discovery_metric(99, -1)) == (15, 0)


def test_dynamic_routes_expire_and_need_explicit_approval() -> None:
    routes = DynamicRouteStore(lifetime=10)
    route = routes.learn("S1", "N1", 4, 2, 77, 5)
    assert routes.best("S1", 6, approved_only=True) is None
    assert routes.approve("S1", 6) is route
    assert routes.best("S1", 6, approved_only=True) is route
    assert routes.best("S1", 15) is None


def test_s6_discovers_s1_across_four_hops_and_each_relay_learns_forward_path() -> None:
    bus = _sample_bus()
    calls = ["S6", "N1", "N2", "N3", "S1", "S2", "S3", "S4", "S5"]
    engines = _engines(bus, calls, max_ttl=4)
    engines["S6"].tick(0)
    pending = engines["S6"].start("S1", query_id=6001)
    assert pending is not None

    _run(bus, engines)

    source = engines["S6"].routes.best("S1", 10)
    assert source is not None
    assert (source.next_hop, source.hops) == ("N1", 4)
    assert (engines["N1"].routes.best("S1", 10).next_hop,
            engines["N1"].routes.best("S1", 10).hops) == ("N2", 3)
    assert (engines["N2"].routes.best("S1", 10).next_hop,
            engines["N2"].routes.best("S1", 10).hops) == ("N3", 2)
    assert (engines["N3"].routes.best("S1", 10).next_hop,
            engines["N3"].routes.best("S1", 10).hops) == ("S1", 1)
    assert engines["S6"].routes.best("S2", 10) is None


def test_expanding_ring_uses_ttl_two_then_four() -> None:
    seen = []
    bus = _sample_bus(monitor=lambda sender, frame: seen.append((sender, frame)))
    engines = _engines(bus, ["S6", "N1", "N2", "N3", "S1"], max_ttl=4)
    engines["S6"].tick(0)
    engines["S6"].start("S1", query_id=6002)
    _run(bus, engines)

    source_queries = [
        frame.ttl
        for sender, frame in seen
        if sender == "S6" and frame.type is FrameType.MULTIHOP_RREQ
    ]
    assert source_queries == [2, 4]


def test_branch_and_loop_converge_without_payload_flood_or_unbounded_duplicates() -> None:
    frames = []
    links = {
        ("S6", "N1"), ("N1", "N2"), ("N2", "N3"), ("N3", "S1"),
        ("N1", "NX"), ("NX", "N3"), ("NX", "N2"),
    }
    bus = GraphRadioBus(links, monitor=lambda sender, frame: frames.append((sender, frame)))
    engines = _engines(bus, ["S6", "N1", "N2", "N3", "NX", "S1"], max_ttl=4)
    engines["S6"].tick(0)
    engines["S6"].start("S1", query_id=6003)
    _run(bus, engines)

    route = engines["S6"].routes.best("S1", 10)
    assert route is not None
    assert route.hops in (3, 4)
    assert all(
        frame.type in (FrameType.MULTIHOP_RREQ, FrameType.MULTIHOP_RREP)
        for _sender, frame in frames
    )
    assert len(frames) < 40


def test_lost_first_rreq_is_recovered_by_expanded_ring() -> None:
    dropped = {"done": False}

    def drop(sender, receiver, frame):
        if (
            not dropped["done"]
            and sender == "S6"
            and receiver == "N1"
            and frame.type is FrameType.MULTIHOP_RREQ
        ):
            dropped["done"] = True
            return True
        return False

    bus = _sample_bus(drop=drop)
    engines = _engines(bus, ["S6", "N1", "N2", "N3", "S1"], max_ttl=4)
    engines["S6"].tick(0)
    engines["S6"].start("S1", query_id=6004)
    _run(bus, engines)
    assert engines["S6"].routes.best("S1", 10) is not None


def test_lost_first_rrep_is_recovered_without_duplicate_routes() -> None:
    dropped = {"done": False}

    def drop(sender, receiver, frame):
        if (
            not dropped["done"]
            and frame.type is FrameType.MULTIHOP_RREP
            and sender == "S1"
            and receiver == "N3"
        ):
            dropped["done"] = True
            return True
        return False

    bus = _sample_bus(drop=drop)
    engines = _engines(bus, ["S6", "N1", "N2", "N3", "S1"], max_ttl=4)
    engines["S6"].tick(0)
    engines["S6"].start("S1", query_id=6005)
    _run(bus, engines)
    routes = engines["S6"].routes.routes(10)
    assert [(route.destination, route.next_hop) for route in routes] == [("S1", "N1")]


def test_two_sources_can_discover_concurrently_without_cache_collision() -> None:
    bus = _sample_bus()
    calls = ["S6", "S5", "N1", "N2", "N3", "S1", "S2"]
    engines = _engines(bus, calls, max_ttl=4, frame_budget=30)
    engines["S6"].tick(0)
    engines["S5"].tick(0)
    engines["S6"].start("S1", query_id=6010)
    engines["S5"].start("S2", query_id=6010)  # same uint32, different origin
    _run(bus, engines)
    assert engines["S6"].routes.best("S1", 10).next_hop == "N1"
    assert engines["S5"].routes.best("S2", 10).next_hop == "N1"


def test_legacy_gap_does_not_turn_into_an_unbounded_or_blind_flood() -> None:
    frames = []
    bus = GraphRadioBus(
        {("S6", "N1"), ("N1", "OLD"), ("OLD", "N3"), ("N3", "S1")},
        monitor=lambda sender, frame: frames.append((sender, frame)),
    )
    # OLD has an endpoint but intentionally no discovery handler, modelling a
    # pre-RREQ/RREP Guardian that ignores unknown frame types.
    bus.endpoint("OLD")
    engines = _engines(bus, ["S6", "N1", "N3", "S1"], max_ttl=4)
    engines["S6"].tick(0)
    engines["S6"].start("S1", query_id=6011)
    _run(bus, engines)
    assert engines["S6"].routes.best("S1", 10) is None
    assert len(frames) < 10
    assert all(frame.type is FrameType.MULTIHOP_RREQ for _sender, frame in frames)


def test_monitor_mode_records_but_never_relays_or_replies() -> None:
    sent = []
    engine = DiscoveryEngine("N1", sent.append, mode=DISCOVERY_MONITOR)
    engine.tick(1)
    handled = engine.receive(
        ControlFrame(
            FrameType.MULTIHOP_RREQ,
            source="S6",
            destination="N1",
            next_hop="S6",
            message_id=9,
            ttl=4,
        )
    )
    engine.tick(10)
    assert handled is True
    assert sent == []
    assert engine.events[0].kind == "heard-rreq"


def test_off_mode_consumes_new_frames_without_recording_or_transmitting() -> None:
    sent = []
    engine = DiscoveryEngine("N1", sent.append, mode=DISCOVERY_OFF)
    engine.tick(1)
    assert engine.receive(
        ControlFrame(
            FrameType.MULTIHOP_RREQ,
            source="S6",
            destination="S1",
            next_hop="S6",
            message_id=19,
            ttl=4,
        )
    )
    assert sent == []
    assert list(engine.events) == []


def test_switching_to_monitor_cancels_pending_and_scheduled_transmit() -> None:
    sent = []
    failed = []
    engine = DiscoveryEngine(
        "N1",
        sent.append,
        mode=DISCOVERY_ASSISTED,
        forward=True,
        relay_enabled=True,
        jitter_min=5,
        jitter_max=5,
        on_failure=lambda query, reason: failed.append((query.destination, reason)),
    )
    engine.tick(1)
    engine.receive(
        ControlFrame(
            FrameType.MULTIHOP_RREQ,
            source="S6",
            destination="S1",
            next_hop="S6",
            message_id=20,
            ttl=4,
        )
    )
    engine.start("S2", query_id=21)
    assert sent and sent[0].type is FrameType.MULTIHOP_RREQ
    sent.clear()

    engine.configure(mode=DISCOVERY_MONITOR)
    engine.tick(10)
    assert sent == []
    assert failed == [("S2", "multi-hop discovery disabled")]


def test_denylist_and_frame_budget_prevent_untrusted_or_excess_transmit() -> None:
    sent = []
    engine = DiscoveryEngine(
        "N1",
        sent.append,
        mode=DISCOVERY_ASSISTED,
        forward=True,
        relay_enabled=True,
        denylist={"BAD"},
        frame_budget=1,
        jitter_min=0,
        jitter_max=0,
    )
    engine.tick(1)
    for source, message_id in (("BAD", 1), ("S6", 2), ("S5", 3)):
        engine.receive(
            ControlFrame(
                FrameType.MULTIHOP_RREQ,
                source=source,
                destination="S1",
                next_hop=source,
                message_id=message_id,
                ttl=4,
            )
        )
    engine.tick(2)
    assert len(sent) == 1
    assert any(event.kind == "limited" for event in engine.events)


def test_oversize_discovery_identity_is_refused_before_transport() -> None:
    sent = []
    engine = DiscoveryEngine(
        "ABCDEFGHIJKLMNOP",
        sent.append,
        mode=DISCOVERY_ASSISTED,
    )
    engine.tick(1)
    assert engine.start("QRSTUVWXYZABCDEF", query_id=4) is not None
    assert sent == []
    assert any(event.kind == "oversize" for event in engine.events)


def test_approved_live_route_precedes_topology_but_never_manual_override() -> None:
    topology = RouteTable([Route("S1", "N9", source="topology")])
    station = Orchestrator("S6", GraphRadioBus(set()).endpoint("S6"), routes=topology)
    station.tick(10)
    station.discovery.routes.learn("S1", "N1", 4, 1, 88, 10)
    station.discovery.approve("S1")
    assert station._resolve_next_hop("S1") == ("N1", "assisted discovery")

    topology.add(Route("S1", "MANUAL", source="manual"))
    assert station._resolve_next_hop("S1") == ("MANUAL", "manual route")


def test_assisted_discovery_then_existing_payload_pipeline_delivers_s6_to_s1() -> None:
    bus = _sample_bus()
    stations = {}
    for call in ("S6", "N1", "N2", "N3", "S1"):
        station = Orchestrator(
            call,
            bus.endpoint(call),
            auto_route=False,
            auto_complete=True,
            relay=call in {"N1", "N2", "N3"},
            discovery_mode=DISCOVERY_ASSISTED,
            discovery_forward=call in {"N1", "N2", "N3"},
            discovery_ttl=4,
        )
        station.discovery.query_timeout = 2
        station.discovery.settle_time = 0
        station.discovery.jitter_min = 0
        station.discovery.jitter_max = 0
        stations[call] = station

    message = stations["S6"].send_message("S1", "hello", msg_id=7001, ttl=8)
    approved = False
    now = 0.0
    while now <= 40:
        for station in stations.values():
            station.tick(now)
        bus.pump()
        if message.state is SessionState.WAITING_ROUTE_APPROVAL and not approved:
            route = stations["S6"].approve_discovered_route("S1")
            assert route is not None
            assert route.next_hop == "N1"
            approved = True
        if message.state is SessionState.DELIVERED:
            break
        now += 0.25

    assert approved is True
    assert message.state is SessionState.DELIVERED
    assert stations["S6"].discovery.routes.best(
        "S1", now, approved_only=True
    ) is not None
    assert stations["N1"].learned_paths["S1"] == "N2"
    assert stations["N2"].learned_paths["S1"] == "N3"
    assert stations["N3"].learned_paths["S1"] == "S1"


def test_assisted_route_failure_does_not_blindly_announce_directly() -> None:
    sent = []
    engine = Orchestrator(
        "S6",
        GraphRadioBus(set()).endpoint("S6"),
        auto_route=False,
        discovery_mode=DISCOVERY_ASSISTED,
        discovery_ttl=2,
    )
    engine.transport.send = sent.append
    engine.discovery.send = sent.append
    engine.discovery.query_timeout = 1
    engine.discovery.jitter_min = 0
    engine.discovery.jitter_max = 0
    message = engine.send_message("S1", "hello", msg_id=7002)
    for now in (0, 1.1, 2.2, 3.2):
        engine.tick(now)
    assert message.state is SessionState.FAILED
    assert [frame.type for frame in sent] == [FrameType.MULTIHOP_RREQ]
