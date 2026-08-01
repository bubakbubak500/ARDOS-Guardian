from pathlib import Path

from guardian.routing import Route, RouteTable


def test_routes_are_normalised_replaced_and_resolved() -> None:
    table = RouteTable()
    table.add(Route(" ok1aaa ", " ok1bbb ", " any ", 145_500_000, "fm"))
    table.add(Route("OK1AAA", "OK1CCC", "OK1DDD", 7_100_000, "usb"))

    assert len(table) == 1
    assert table.next_hop("ok1aaa") == "OK1CCC"
    assert table.next_hop("ok1aaa", use_backup=True) == "OK1DDD"
    assert table.freq_for("ok1aaa") == (7_100_000, "USB")


def test_route_table_round_trip_and_remove(tmp_path: Path) -> None:
    path = tmp_path / "routes.json"
    table = RouteTable([Route("OK1AAA", "OK1BBB", "", 145_500_000, "FM")])
    table.save(path)

    restored = RouteTable.load(path)

    assert restored.lookup("ok1aaa") == Route(
        "OK1AAA", "OK1BBB", "", 145_500_000, "FM"
    )
    restored.remove("ok1aaa")
    assert restored.lookup("OK1AAA") is None


def test_separate_working_channel_is_independent_and_persistent(tmp_path: Path) -> None:
    path = tmp_path / "routes.json"
    table = RouteTable(
        [Route("OK1AAA", "", "", 145_500_000, "FM", 145_550_000, "FM")]
    )

    table.save(path)
    restored = RouteTable.load(path)

    assert restored.freq_for("OK1AAA") == (145_500_000, "FM")
    assert restored.working_for("OK1AAA") == (145_550_000, "FM")
    assert restored.working_for("OK9ZZZ") is None


def test_working_frequencies_are_listed_once_in_table_order() -> None:
    # The alert sweep tunes to these, so a frequency shared by three
    # destinations must not be keyed three times.
    table = RouteTable(
        [
            Route("OK1AAA", "", "", 145_500_000, "fm"),
            Route("OK1BBB", "", "", 7_100_000, "usb"),
            Route("OK1CCC", "", "", 145_500_000, "fm"),
            Route("OK1DDD", "OK1AAA"),          # no frequency of its own
        ]
    )

    assert table.frequencies() == [(145_500_000, "FM"), (7_100_000, "USB")]
    assert RouteTable().frequencies() == []


def test_empty_preferred_hop_represents_a_direct_route() -> None:
    table = RouteTable([Route("OK1AAA", "")])

    assert table.lookup("ok1aaa") == Route("OK1AAA", "")
    assert table.next_hop("ok1aaa") is None
