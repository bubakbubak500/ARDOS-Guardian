from guardian.config import StationConfig
from guardian.protocol import ControlFrame, FrameType
from guardian.routing import (
    DISCOVERY_ASSISTED,
    DISCOVERY_MODES,
    DISCOVERY_OFF,
    DiscoveryEngine,
    DynamicRouteStore,
    Route,
    RouteTable,
    decode_discovery_metric,
    encode_discovery_metric,
    normalize_discovery_mode,
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


def test_only_off_and_assisted_remain_and_a_monitor_profile_becomes_assisted() -> None:
    assert DISCOVERY_MODES == (DISCOVERY_OFF, DISCOVERY_ASSISTED)
    # The retired receive-only position could neither answer a query nor hand a
    # route to its own operator, so an upgraded station takes part instead.
    assert normalize_discovery_mode("monitor") == DISCOVERY_ASSISTED
    assert normalize_discovery_mode("ASSISTED ") == DISCOVERY_ASSISTED
    # A typo must never be the reason a station starts transmitting.
    for value in ("", None, "assisted-ish", "monitor only"):
        assert normalize_discovery_mode(value) == DISCOVERY_OFF
    assert DiscoveryEngine("N1", lambda _frame: None).mode == DISCOVERY_OFF
    engine = DiscoveryEngine("N1", lambda _frame: None, mode="monitor")
    assert engine.mode == DISCOVERY_ASSISTED
    assert engine.can_transmit is True


def test_assisted_station_answers_a_query_addressed_to_itself() -> None:
    sent = []
    engine = DiscoveryEngine(
        "N1",
        sent.append,
        mode=DISCOVERY_ASSISTED,
        jitter_min=0,
        jitter_max=0,
    )
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
    assert engine.events[-1].kind == "heard-rreq"
    assert [frame.type for frame in sent] == [
        FrameType.MULTIHOP_RREP,
        FrameType.MULTIHOP_RREP,   # one bounded duplicate against a lost reply
    ]
    assert sent[0].destination == "S6"


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


def test_switching_off_cancels_pending_and_scheduled_transmit() -> None:
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

    engine.configure(mode=DISCOVERY_OFF)
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


def test_experimental_auto_use_delivers_without_operator_approval() -> None:
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
            discovery_auto_use=True,
        )
        station.discovery.query_timeout = 2
        station.discovery.settle_time = 0
        station.discovery.jitter_min = 0
        station.discovery.jitter_max = 0
        stations[call] = station

    message = stations["S6"].send_message("S1", "automatic", msg_id=7010, ttl=8)
    now = 0.0
    while now <= 40 and message.state is not SessionState.DELIVERED:
        for station in stations.values():
            station.tick(now)
        bus.pump()
        now += 0.25

    assert message.state is SessionState.DELIVERED
    route = stations["S6"].discovery.routes.best(
        "S1", now, approved_only=True
    )
    assert route is not None
    assert (route.next_hop, route.source) == ("N1", "rreq")


def test_link_advert_flag_off_consumes_frame_without_learning_or_relaying() -> None:
    sent = []
    engine = DiscoveryEngine(
        "N1",
        sent.append,
        mode=DISCOVERY_ASSISTED,
        forward=True,
        relay_enabled=True,
        link_advert_enabled=False,
    )
    engine.tick(1)
    assert engine.receive(
        ControlFrame(
            FrameType.LINK_ADVERT,
            source="S6",
            destination="S6",
            next_hop="N1",
            message_id=44,
            ttl=4,
        )
    )
    engine.tick(5)
    assert engine.live_topology.links(5) == []
    assert sent == []


def test_link_advert_presence_bootstraps_two_previously_quiet_stations() -> None:
    frames = []
    bus = GraphRadioBus(
        {("S6", "N1")},
        monitor=lambda sender, frame: frames.append((sender, frame)),
    )
    stations = {
        call: Orchestrator(
            call,
            bus.endpoint(call),
            discovery_mode=DISCOVERY_ASSISTED,
            discovery_auto_use=True,
            link_advert_enabled=True,
        )
        for call in ("S6", "N1")
    }
    for now in (0.0, 0.1, 0.2):
        for station in stations.values():
            station.tick(now)
        bus.pump()

    route = stations["S6"].discovery.routes.best(
        "N1", 0.2, approved_only=True
    )
    assert route is not None
    assert (route.next_hop, route.hops, route.source) == (
        "N1",
        1,
        "link-advert",
    )
    assert len(frames) == 4  # two one-hop presence + two changed-neighbour adverts


def test_quiet_three_node_network_self_detects_and_regenerates_multihop_route() -> None:
    frames = []
    bus = GraphRadioBus(
        {("S6", "N1"), ("N1", "N2")},
        monitor=lambda sender, frame: frames.append((sender, frame)),
    )
    stations = {}
    for call in ("S6", "N1", "N2"):
        station = Orchestrator(
            call,
            bus.endpoint(call),
            relay=call == "N1",
            discovery_mode=DISCOVERY_ASSISTED,
            discovery_forward=call == "N1",
            discovery_auto_use=True,
            link_advert_enabled=True,
            discovery_frame_budget=40,
        )
        station.discovery.jitter_min = 0
        station.discovery.jitter_max = 0
        stations[call] = station

    now = 0.0
    while now <= 3.0:
        for station in stations.values():
            station.tick(now)
        bus.pump()
        now += 0.1

    route = stations["S6"].discovery.routes.best(
        "N2", now, approved_only=True
    )
    assert route is not None
    assert (route.next_hop, route.hops) == ("N1", 2)
    assert len(frames) < 30


def test_lost_link_advert_is_recovered_on_next_bounded_interval() -> None:
    dropped = {"done": False}

    def drop(sender, receiver, frame):
        if (
            not dropped["done"]
            and sender == "N1"
            and receiver == "S6"
            and frame.type is FrameType.LINK_ADVERT
            and frame.next_hop
        ):
            dropped["done"] = True
            return True
        return False

    bus = GraphRadioBus({("S6", "N1")}, drop=drop)
    stations = {
        call: Orchestrator(
            call,
            bus.endpoint(call),
            discovery_mode=DISCOVERY_ASSISTED,
            discovery_auto_use=True,
            link_advert_enabled=True,
            link_advert_interval=60,
        )
        for call in ("S6", "N1")
    }
    for now in (0.0, 0.1, 0.2):
        for station in stations.values():
            station.tick(now)
        bus.pump()
    assert stations["S6"].discovery.routes.best("N1", 0.2) is None

    for now in (60.1, 60.2, 60.3):
        for station in stations.values():
            station.tick(now)
        bus.pump()
    assert dropped["done"] is True
    assert stations["S6"].discovery.routes.best(
        "N1", 60.3, approved_only=True
    ) is not None


def test_legacy_node_creates_safe_finite_gap_in_link_advert_topology() -> None:
    frames = []
    bus = GraphRadioBus(
        {("S6", "N1"), ("N1", "OLD"), ("OLD", "N2")},
        monitor=lambda sender, frame: frames.append((sender, frame)),
    )
    bus.endpoint("OLD")  # pre-0.6.58 node: receives but has no type-16 handler
    stations = {}
    for call in ("S6", "N1", "N2"):
        station = Orchestrator(
            call,
            bus.endpoint(call),
            relay=call == "N1",
            discovery_mode=DISCOVERY_ASSISTED,
            discovery_forward=call == "N1",
            discovery_auto_use=True,
            link_advert_enabled=True,
        )
        station.discovery.jitter_min = 0
        station.discovery.jitter_max = 0
        stations[call] = station
    for now in (0.0, 0.1, 0.2, 0.3, 1.0):
        for station in stations.values():
            station.tick(now)
        bus.pump()

    assert stations["S6"].discovery.routes.best("N2", 1.0) is None
    assert all(frame.type is FrameType.LINK_ADVERT for _sender, frame in frames)
    assert len(frames) < 15


def test_disabling_link_advert_removes_only_its_volatile_state() -> None:
    engine = DiscoveryEngine(
        "S6",
        lambda _frame: None,
        mode=DISCOVERY_ASSISTED,
        link_advert_enabled=True,
        auto_use=True,
    )
    engine.tick(1)
    engine.advertise_neighbors([("N1", 10.0)], force=True)
    engine.receive(
        ControlFrame(
            FrameType.LINK_ADVERT,
            source="N1",
            destination="N1",
            next_hop="S6",
            message_id=441,
            ttl=2,
        )
    )
    rreq = engine.routes.learn("S2", "N2", 2, 0, 442, 1)
    assert engine.routes.best("N1", 1) is not None

    engine.configure(link_advert_enabled=False)
    assert engine.live_topology.links(1) == []
    assert engine.routes.best("N1", 1) is None
    assert engine.routes.best("S2", 1) is rreq


def test_one_way_link_advert_is_visible_but_never_routable() -> None:
    engine = DiscoveryEngine(
        "S6",
        lambda _frame: None,
        mode=DISCOVERY_ASSISTED,
        link_advert_enabled=True,
    )
    engine.tick(1)
    engine.receive(
        ControlFrame(
            FrameType.LINK_ADVERT,
            source="N1",
            destination="N1",
            next_hop="S6",
            message_id=45,
            ttl=2,
        )
    )
    links = engine.live_topology.links(1)
    assert [(link.owner, link.neighbor) for link in links] == [("N1", "S6")]
    assert engine.live_topology.reciprocal(links[0], 1) is False
    assert engine.routes.best("N1", 1) is None


def test_link_adverts_regenerate_full_reciprocal_topology_with_bounded_flood() -> None:
    frames = []
    bus = _sample_bus(monitor=lambda sender, frame: frames.append((sender, frame)))
    calls = ["S6", "N1", "N2", "N3", "S1", "S2", "S3", "S4", "S5"]
    engines = _engines(
        bus,
        calls,
        max_ttl=4,
        frame_budget=120,
        link_advert_enabled=True,
        auto_use=False,
    )
    neighbours = {
        "S6": ["N1"],
        "S5": ["N1"],
        "N1": ["S6", "S5", "N2"],
        "N2": ["N1", "N3", "S3", "S4"],
        "N3": ["N2", "S1", "S2"],
        "S1": ["N3"],
        "S2": ["N3"],
        "S3": ["N2"],
        "S4": ["N2"],
    }
    for engine in engines.values():
        engine.tick(0)
    for call, peers in neighbours.items():
        engines[call].advertise_neighbors([(peer, 5.0) for peer in peers], force=True)

    _run(bus, engines, until=10)

    route = engines["S6"].routes.best("S1", 10)
    assert route is not None
    assert (route.next_hop, route.hops, route.source, route.approved) == (
        "N1",
        4,
        "link-advert",
        False,
    )
    assert engines["S6"].routes.best("S1", 10, approved_only=True) is None
    assert all(frame.type is FrameType.LINK_ADVERT for _sender, frame in frames)
    # Flooding is finite despite every physical link hearing each broadcast.
    assert len(frames) < 250


def test_link_advert_routes_auto_approve_only_when_auto_use_flag_is_on() -> None:
    engine = DiscoveryEngine(
        "S6",
        lambda _frame: None,
        mode=DISCOVERY_ASSISTED,
        link_advert_enabled=True,
        auto_use=True,
    )
    engine.tick(1)
    engine.advertise_neighbors([("N1", 10.0)], force=True)
    engine.receive(
        ControlFrame(
            FrameType.LINK_ADVERT,
            source="N1",
            destination="N1",
            next_hop="S6",
            message_id=46,
            ttl=2,
        )
    )
    route = engine.routes.best("N1", 1, approved_only=True)
    assert route is not None
    assert route.source == "link-advert"

    engine.configure(auto_use=False)
    assert engine.routes.best("N1", 1, approved_only=True) is None


def test_disabling_auto_use_preserves_only_manual_approval() -> None:
    routes = DynamicRouteStore(lifetime=30)
    automatic = routes.learn("S1", "N1", 2, 0, 1, 0)
    routes.approve("S1", 0, automatic=True)
    manual = routes.learn("S2", "N2", 2, 0, 2, 0)
    routes.approve("S2", 0)
    engine = DiscoveryEngine("S6", lambda _frame: None, auto_use=False)
    engine.routes = routes
    engine.configure(auto_use=True)
    engine.configure(auto_use=False)
    assert automatic.approved is False
    assert manual.approved is True


def test_auto_use_never_activates_routes_while_discovery_is_off() -> None:
    engine = DiscoveryEngine(
        "S6",
        lambda _frame: None,
        mode=DISCOVERY_OFF,
        auto_use=True,
    )
    engine.tick(1)
    route = engine.routes.learn("S1", "N1", 2, 0, 3, 1)
    engine.configure(auto_use=True)
    assert route.approved is False
    engine.configure(mode=DISCOVERY_ASSISTED)
    assert route.approved is True
    engine.configure(mode=DISCOVERY_OFF)
    assert route.approved is False


def test_two_stations_on_default_settings_find_each_other_and_await_approval() -> None:
    """The shipped profile has to produce something an operator can verify.

    Everything here comes from StationConfig defaults: no experiment enabled,
    no relay, no automatic use. What the operator should see is a query going
    out, an answer coming back, and a route sitting in the table waiting for
    them -- not two silent stations and empty tables.
    """
    config = StationConfig()
    bus = GraphRadioBus({("S6", "N1")})
    stations = {
        call: Orchestrator(
            call,
            bus.endpoint(call),
            auto_route=config.auto_route,
            auto_complete=True,
            relay=config.auto_relay,
            discovery_mode=config.discovery_mode,
            discovery_forward=config.discovery_forward,
            discovery_ttl=config.discovery_ttl,
            discovery_frame_budget=config.discovery_frame_budget,
            discovery_auto_use=config.discovery_auto_use,
            link_advert_enabled=config.link_advert_enabled,
        )
        for call in ("S6", "N1")
    }
    for station in stations.values():
        station.discovery.jitter_min = 0
        station.discovery.jitter_max = 0

    message = stations["S6"].send_message("N1", "hello", msg_id=7100, ttl=5)
    assert message.state is SessionState.MULTIHOP_DISCOVERY

    now = 0.0
    approved = False
    while now <= 40 and message.state is not SessionState.DELIVERED:
        for station in stations.values():
            station.tick(now)
        bus.pump()
        if message.state is SessionState.WAITING_ROUTE_APPROVAL and not approved:
            route = stations["S6"].approve_discovered_route("N1")
            assert route is not None
            assert (route.next_hop, route.hops) == ("N1", 1)
            approved = True
        now += 0.25

    assert approved is True
    assert message.state is SessionState.DELIVERED
    # N1 answered a query about itself, which the retired monitor position
    # could not do -- and it heard S6 while doing so.
    assert any(event.kind == "heard-rreq" for event in stations["N1"].discovery.events)
    assert stations["N1"].heard.is_heard("S6", now)


def test_link_advert_evidence_and_derived_route_expire() -> None:
    engine = DiscoveryEngine(
        "S6",
        lambda _frame: None,
        mode=DISCOVERY_ASSISTED,
        link_advert_enabled=True,
        auto_use=True,
        route_lifetime=10,
    )
    engine.tick(0)
    engine.advertise_neighbors([("N1", 10.0)], force=True)
    engine.receive(
        ControlFrame(
            FrameType.LINK_ADVERT,
            source="N1",
            destination="N1",
            next_hop="S6",
            message_id=47,
            ttl=2,
        )
    )
    assert engine.routes.best("N1", 1, approved_only=True) is not None
    route = engine.routes.best("N1", 1, approved_only=True)
    engine.routes.mark_success("N1", "N1", 5)
    assert route.expires_at == 10
    engine.tick(10.1)
    assert engine.live_topology.links(10.1) == []
    assert engine.routes.best("N1", 10.1, approved_only=True) is None
