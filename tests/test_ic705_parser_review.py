from datetime import datetime, timezone
from threading import Event

import pytest

from guardian.location import LocationSource
from guardian.radio.icom_gps import (
    IcomGpsPortConflict,
    nmea_checksum,
    parse_nmea_sentence,
    read_icom_gps,
    same_serial_port,
    serial_port_name,
)
from guardian.routing import to_locator


NOW = datetime(2026, 9, 11, 12, 35, 19, tzinfo=timezone.utc)
LATITUDE = 48 + 7.038 / 60
LONGITUDE = 11 + 31.000 / 60


def _nmea(body: str) -> bytes:
    return f"${body}*{nmea_checksum(body)}\r\n".encode("ascii")


def _gga(
    quality: int = 1,
    *,
    latitude: str = "4807.038",
    latitude_hemisphere: str = "N",
    longitude: str = "01131.000",
    longitude_hemisphere: str = "E",
) -> bytes:
    return _nmea(
        "GPGGA,123519,"
        f"{latitude},{latitude_hemisphere},{longitude},{longitude_hemisphere},"
        f"{quality},08,0.9,545.4,M,46.9,M,,"
    )


def _rmc(
    status: str = "A",
    mode: str | None = None,
    *,
    time_value: str = "123519",
    date_value: str = "110926",
) -> bytes:
    body = (
        f"GPRMC,{time_value},"
        f"{status},4807.038,N,01131.000,E,022.4,084.4,{date_value},003.1,W"
    )
    if mode is not None:
        body += f",{mode}"
    return _nmea(body)


def _gll(status: str = "A", mode: str | None = None) -> bytes:
    body = "GPGLL,4807.038,N,01131.000,E,123519," + status
    if mode is not None:
        body += f",{mode}"
    return _nmea(body)


class FakeSerial:
    """Small serial stand-in that records open/close and control-line order."""

    def __init__(self, chunks: list[bytes], events: list[tuple]) -> None:
        self.chunks = list(chunks)
        self.events = events
        self.port = None
        self.timeout = None
        self._rts = None
        self._dtr = None

    @property
    def rts(self):
        return self._rts

    @rts.setter
    def rts(self, value):
        self._rts = value
        self.events.append(("rts", value))

    @property
    def dtr(self):
        return self._dtr

    @dtr.setter
    def dtr(self, value):
        self._dtr = value
        self.events.append(("dtr", value))

    def open(self) -> None:
        self.events.append(("open", self.port, self.rts, self.dtr))

    def read_until(self, *_args) -> bytes:
        if self.chunks:
            return self.chunks.pop(0)
        return b""

    def close(self) -> None:
        self.events.append(("close", self.rts, self.dtr))


def _factory_for(
    chunks: list[bytes], events: list[tuple], holder: list[FakeSerial]
):
    def factory(**kwargs):
        events.append(("factory", kwargs))
        serial = FakeSerial(chunks, events)
        holder.append(serial)
        return serial

    return factory


def test_fragmented_read_until_keeps_chunks_across_a_timeout() -> None:
    sentence = _gga()
    events: list[tuple] = []
    serials: list[FakeSerial] = []
    split = 19

    fix = read_icom_gps(
        "COM7 — IC-705 GPS",
        serial_factory=_factory_for(
            [sentence[:split], b"", sentence[split:]], events, serials
        ),
        timeout_seconds=2,
        max_age_seconds=5,
        now=NOW,
    )

    assert fix is not None
    assert fix.source is LocationSource.GPS
    assert fix.timestamp == NOW
    assert fix.latitude == pytest.approx(LATITUDE)
    assert fix.longitude == pytest.approx(LONGITUDE)
    assert to_locator(fix.latitude, fix.longitude) == to_locator(
        LATITUDE, LONGITUDE
    )
    assert serials[0].chunks == []


def test_parse_rejects_a_sentence_with_the_wrong_checksum() -> None:
    sentence = _gga()
    star = sentence.rfind(b"*")
    checksum = sentence[star + 1 : star + 3]
    wrong = b"00" if checksum != b"00" else b"FF"
    corrupted = sentence[: star + 1] + wrong + b"\r\n"

    assert parse_nmea_sentence(corrupted, now=NOW, max_age_seconds=5) is None


def test_parse_rejects_an_old_rmc_date_and_time() -> None:
    old_fix = _rmc(time_value="123519", date_value="100926")

    assert parse_nmea_sentence(old_fix, now=NOW, max_age_seconds=5) is None


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [("4860.000", "01131.000"), ("4807.038", "18100.000")],
)
def test_parse_rejects_out_of_range_coordinates(
    latitude: str, longitude: str
) -> None:
    sentence = _gga(latitude=latitude, longitude=longitude)

    assert parse_nmea_sentence(sentence, now=NOW, max_age_seconds=5) is None


def test_reader_discards_an_overlong_fragmented_sentence_and_recovers() -> None:
    overlong = b"$GPGGA," + b"X" * 600 + b"\r\n"
    events: list[tuple] = []
    serials: list[FakeSerial] = []
    chunks = [overlong[:200], overlong[200:400], overlong[400:], _gga()]

    fix = read_icom_gps(
        "COM7",
        serial_factory=_factory_for(chunks, events, serials),
        timeout_seconds=2,
        max_age_seconds=5,
        now=NOW,
    )

    assert fix is not None
    assert fix.latitude == pytest.approx(LATITUDE)
    assert fix.longitude == pytest.approx(LONGITUDE)
    assert serials[0].chunks == []


@pytest.mark.parametrize("quality", [6, 7, 8, 999])
def test_gga_quality_values_above_five_are_not_a_usable_fix(quality: int) -> None:
    assert parse_nmea_sentence(_gga(quality), now=NOW, max_age_seconds=5) is None


@pytest.mark.parametrize(
    "sentence",
    [
        _rmc(status="V"),
        _gll(status="V"),
        _rmc(mode="Z"),
        _gll(mode="Z"),
        _nmea(
            "GPGNS,123519,4807.038,N,01131.000,E,N,08,0.9,545.4,"
        ),
    ],
)
def test_invalid_rmc_gll_status_or_mode_and_gns_mode_are_rejected(sentence) -> None:
    assert parse_nmea_sentence(sentence, now=NOW, max_age_seconds=5) is None


def test_rmc_and_gll_modes_are_optional_when_absent() -> None:
    for sentence in (_rmc(), _gll()):
        fix = parse_nmea_sentence(sentence, now=NOW, max_age_seconds=5)
        assert fix is not None
        assert fix.latitude == pytest.approx(LATITUDE)
        assert fix.longitude == pytest.approx(LONGITUDE)


def test_serial_port_normalization_handles_leading_whitespace_and_windows_device_path() -> None:
    gps = "  \\.\\COM7 — IC-705 GPS"
    cat = "COM7 — CAT/PTT"

    assert serial_port_name(gps) == "com7"
    assert same_serial_port(gps, cat)


def test_read_refuses_gps_port_that_matches_configured_cat_port() -> None:
    factory_called = []

    def factory(**_kwargs):
        factory_called.append(True)
        raise AssertionError("conflicting GPS port must not be opened")

    with pytest.raises(IcomGpsPortConflict):
        read_icom_gps(
            "  \\.\\COM7 — IC-705 GPS",
            forbidden_port="COM7 — CAT/PTT",
            serial_factory=factory,
        )
    assert factory_called == []


def test_serial_control_lines_are_low_before_open_and_on_close() -> None:
    events: list[tuple] = []
    serials: list[FakeSerial] = []
    fix = read_icom_gps(
        "COM8",
        serial_factory=_factory_for([_gga()], events, serials),
        timeout_seconds=2,
        max_age_seconds=5,
        now=NOW,
    )

    assert fix is not None
    assert events.index(("rts", False)) < events.index(("open", "COM8", False, False))
    assert events.index(("dtr", False)) < events.index(("open", "COM8", False, False))
    assert events[-1] == ("close", False, False)


def test_cancellation_still_drives_lines_low_and_closes_serial() -> None:
    events: list[tuple] = []
    serials: list[FakeSerial] = []
    stop = Event()

    class CancelSerial(FakeSerial):
        def read_until(self, *_args) -> bytes:
            stop.set()
            return b""

    def factory(**_kwargs):
        serial = CancelSerial([], events)
        serials.append(serial)
        return serial

    assert (
        read_icom_gps(
            "COM9",
            stop_event=stop,
            serial_factory=factory,
            timeout_seconds=2,
            now=NOW,
        )
        is None
    )
    assert events[-1] == ("close", False, False)
    assert serials[0].rts is False
    assert serials[0].dtr is False
