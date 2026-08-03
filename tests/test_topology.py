from guardian.routing import (
    Link,
    Route,
    RouteTable,
    Topology,
    read_topology_csv,
    topology_from_csv,
    topology_to_csv,
    write_topology_csv,
)


def _sample() -> Topology:
    return Topology(
        [
            Link("S6", "N1", freq_hz=145_500_000, mode="FM"),
            Link("S5", "N1"),
            Link("N1", "N2", freq_hz=145_525_000, mode="FM"),
            Link("N2", "N3", freq_hz=145_550_000, mode="FM"),
            Link("N2", "S3"),
            Link("N2", "S4"),
            Link("N3", "S1", freq_hz=145_575_000, mode="FM"),
            Link("N3", "S2"),
        ]
    )


def test_one_topology_derives_the_correct_local_view_at_every_station() -> None:
    topology = _sample()

    s6 = {route.destination: route for route in topology.derive_routes("s6")}
    n1 = {route.destination: route for route in topology.derive_routes("N1")}
    n2 = {route.destination: route for route in topology.derive_routes("N2")}
    n3 = {route.destination: route for route in topology.derive_routes("N3")}

    assert (s6["S1"].preferred, s6["S1"].freq_hz) == ("N1", 145_500_000)
    assert s6["N1"].preferred == ""
    assert (n1["S1"].preferred, n1["S1"].freq_hz) == ("N2", 145_525_000)
    assert (n2["S1"].preferred, n2["S1"].freq_hz) == ("N3", 145_550_000)
    assert (n3["S1"].preferred, n3["S1"].freq_hz) == ("", 145_575_000)
    assert all(route.source == "topology" for route in s6.values())


def test_direction_and_cost_control_reachability_and_route_choice() -> None:
    topology = Topology(
        [
            Link("A", "B", "a_to_b", cost=8),
            Link("A", "C", cost=1),
            Link("C", "B", cost=1),
        ]
    )
    from_a = {route.destination: route for route in topology.derive_routes("A")}
    from_b = {route.destination: route for route in topology.derive_routes("B")}

    assert from_a["B"].preferred == "C"
    assert from_b["A"].preferred == "C"


def test_alternate_first_hop_becomes_route_backup() -> None:
    topology = Topology(
        [
            Link("A", "B", cost=1),
            Link("B", "D", cost=1),
            Link("A", "C", cost=2),
            Link("C", "D", cost=2),
        ]
    )
    route = {item.destination: item for item in topology.derive_routes("A")}["D"]
    assert (route.preferred, route.backup) == ("B", "C")


def test_manual_route_remains_an_override_when_topology_is_recomputed() -> None:
    table = RouteTable([Route("S1", "MANUAL")])
    table.replace_topology(_sample().derive_routes("S6"))

    assert table.lookup("S1").preferred == "MANUAL"
    assert table.lookup("S1").source == "manual"
    assert table.lookup("S2").source == "topology"

    table.replace_topology([])
    assert table.routes == [Route("S1", "MANUAL").normalised()]


def test_topology_json_and_csv_round_trip(tmp_path) -> None:
    topology = _sample()
    json_path = topology.save(tmp_path / "topology.json")
    assert Topology.load(json_path).links == topology.links

    csv_path = write_topology_csv(tmp_path / "topology.csv", topology)
    report = read_topology_csv(csv_path)
    assert report.problems == []
    assert report.topology.links == topology.links
    assert topology_from_csv(topology_to_csv(topology)).topology.links == topology.links


def test_topology_import_reports_bad_rows_without_losing_good_links() -> None:
    report = topology_from_csv(
        "station_a;station_b;direction;frequency_mhz;mode;working_frequency_mhz;"
        "working_mode;cost;enabled\n"
        "S6;N1;both;145.5000;FM;;;1;yes\n"
        "N1;N1;sideways;bad;FM;;;zero;yes\n"
    )
    assert len(report.topology.links) == 1
    assert len(report.problems) >= 3


def test_topology_warns_about_unreachable_and_unheard_first_hops() -> None:
    topology = Topology(
        [
            Link("A", "B"),
            Link("C", "D", "a_to_b"),
        ]
    )
    warnings = topology.warnings("A", heard=set())
    assert "C is not reachable from A" in warnings
    assert "D is not reachable from A" in warnings
    assert "next hop B has not been heard by A" in warnings
