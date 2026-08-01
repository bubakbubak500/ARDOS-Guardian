from pathlib import Path

from guardian.routing import Route, read_csv, write_csv
from guardian.routing.csv_io import routes_from_csv, routes_to_csv, template_csv


def test_export_is_semicolon_separated_utf8_with_bom(tmp_path: Path) -> None:
    # A Czech Excel opens a comma-separated file as one column per row, and
    # mangles diacritics without the BOM.
    path = write_csv(
        tmp_path / "net.csv",
        [Route("OK2IPW", "", "", 145_237_500, "FM")],
    )
    raw = path.read_bytes()

    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    assert text.splitlines()[0] == (
        "destination;preferred;backup;frequency_mhz;mode;"
        "working_frequency_mhz;working_mode"
    )
    assert "OK2IPW;;;145.2375;FM;;" in text


def test_round_trip_preserves_every_field(tmp_path: Path) -> None:
    routes = [
        Route("OK2IPW", "", "", 145_237_500, "FM", 145_550_000, "FM"),
        Route("OK1AAA", "OK2IPW", "ANY", 145_300_000, "FM"),
        Route("OSTRAVA", "OK1AAA", "", 0, ""),
    ]
    path = write_csv(tmp_path / "net.csv", routes)

    report = read_csv(path)

    assert report.problems == []
    assert [(
        r.destination,
        r.preferred,
        r.backup,
        r.freq_hz,
        r.mode,
        r.working_freq_hz,
        r.working_mode,
    )
            for r in report.routes] == [
        ("OK2IPW", "", "", 145_237_500, "FM", 145_550_000, "FM"),
        ("OK1AAA", "OK2IPW", "ANY", 145_300_000, "FM", 0, ""),
        ("OSTRAVA", "OK1AAA", "", 0, "", 0, ""),
    ]


def test_import_tolerates_what_a_spreadsheet_actually_produces() -> None:
    # Comma-separated (Google Sheets), decimal comma (Czech Excel), stray
    # whitespace, lower-case calls, a blank line and a BOM.
    text = (
        "﻿Destination,Preferred,Backup,Frequency_MHz,Mode\r\n"
        " ok2ipw , , ,145.2375, fm \r\n"
        "\r\n"
        "ok1aaa,ok2ipw,ANY,145300000,FM\r\n"
    )

    report = routes_from_csv(text)

    assert report.problems == []
    assert [r.destination for r in report.routes] == ["OK2IPW", "OK1AAA"]
    assert report.routes[0].freq_hz == 145_237_500
    assert report.routes[0].mode == "FM"
    # A bare Hz figure is accepted as well as MHz.
    assert report.routes[1].freq_hz == 145_300_000

    czech = "destination;frequency_mhz\r\nOK2IPW;145,2375\r\n"
    assert routes_from_csv(czech).routes[0].freq_hz == 145_237_500


def test_import_reports_bad_rows_instead_of_dropping_them_silently() -> None:
    text = (
        "destination;preferred;backup;frequency_mhz;mode\n"
        "OK2IPW;;;not-a-frequency;FM\n"
        ";OK1AAA;;145.5;FM\n"
        "OK1AAA;;;145.5;FM\n"
        "OK1AAA;OK2IPW;;145.6;FM\n"
    )

    report = routes_from_csv(text)

    assert report.imported == 3
    joined = " ".join(report.problems)
    assert "unreadable frequency" in joined
    assert "no destination" in joined
    assert "repeated" in joined
    # The unreadable frequency does not lose the route, just its frequency.
    assert report.routes[0].destination == "OK2IPW"
    assert report.routes[0].freq_hz == 0


def test_a_headerless_file_falls_back_to_the_documented_column_order() -> None:
    report = routes_from_csv("OK2IPW;OK1AAA;ANY;145.2375;FM\n")

    assert report.problems == []
    assert report.routes[0].preferred == "OK1AAA"
    assert report.routes[0].freq_hz == 145_237_500


def test_empty_input_is_reported_not_crashed() -> None:
    assert routes_from_csv("").problems
    assert routes_from_csv("   \n").problems
    assert routes_from_csv("").routes == []


def test_the_template_is_a_valid_file_operators_can_fill_in() -> None:
    report = routes_from_csv(template_csv())

    assert report.problems == []
    assert report.imported == 3
    assert routes_to_csv(report.routes) == template_csv()
