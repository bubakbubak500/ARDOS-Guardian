"""Focused tests for the Guardian K5/K61 UART and AIOC radio foundation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from guardian.config import StationConfig
from guardian.radio import GuardianK5Radio, GuardianK61Radio, make_driver
from guardian.radio import guardian_handheld
from guardian.radio.guardian_uart import GuardianVfoState, decode_k5_envelope, encode_k5_envelope, k5_packet
from guardian.radio.generic_vox import VoxRadio


def _state() -> GuardianVfoState:
    return GuardianVfoState(
        protocol_version=3,
        active_vfo=0,
        active_profile=1,
        dual_watch=False,
        vfo_a_rx=14_550_000,
        vfo_a_tx=14_550_000,
        vfo_b_rx=43_360_000,
        vfo_b_tx=43_360_000,
    )


class _Port:
    def __init__(self) -> None:
        self.is_open = False
        self.port = None
        self.baudrate = None
        self.dtr = True
        self.rts = False
        self.timeout = None
        self.write_timeout = None
        self.xonxoff = None
        self.rtscts = None
        self.dsrdtr = None
        self.bytesize = None
        self.parity = None
        self.stopbits = None
        self.reset_count = 0
        self.closed = False

    def open(self) -> None:
        self.is_open = True

    def close(self) -> None:
        self.is_open = False
        self.closed = True

    def reset_input_buffer(self) -> None:
        self.reset_count += 1


class _Client:
    def __init__(self, port) -> None:
        self.port = port

    def hello(self) -> str:
        return "Guardian K5FW 1.5"

    def read_state(self) -> GuardianVfoState:
        return _state()


def test_k5_open_and_aioc_ptt_keep_one_safe_serial_owner(monkeypatch) -> None:
    port = _Port()
    monkeypatch.setattr(guardian_handheld, "K5RuntimeClient", _Client)
    radio = GuardianK5Radio(
        "COM3", serial_factory=lambda: port, uart_guard_seconds=0
    )

    radio.open()
    assert radio.is_open
    assert (port.dtr, port.rts) == (False, True)

    radio.set_ptt(True)
    assert (port.dtr, port.rts) == (True, False)
    assert radio.get_state().ptt is True
    radio.set_ptt(False)
    assert (port.dtr, port.rts) == (False, True)

    radio.close()
    assert not radio.is_open
    assert port.closed
    assert (port.dtr, port.rts) == (False, True)


@pytest.mark.parametrize(
    ("driver_type", "backend", "baud"),
    ((GuardianK5Radio, "guardian_k5", 38_400), (GuardianK61Radio, "guardian_k61", 115_200)),
)
def test_factory_exposes_both_guardian_handheld_profiles(driver_type, backend, baud) -> None:
    config = StationConfig(
        radio_backend=backend,
        cat_port="COM7",
        cat_baud=baud,
        guardian_ptt_mode="AIOC",
    )

    driver = make_driver(config)

    assert isinstance(driver, driver_type)
    assert driver.port == "COM7"
    assert driver.baud == baud
    assert driver.ptt_mode == "AIOC"


def test_k5_uart_guard_waits_after_configuration_before_ptt(monkeypatch) -> None:
    port = _Port()
    monkeypatch.setattr(guardian_handheld, "K5RuntimeClient", _Client)
    now = [0.0]
    sleeps: list[float] = []

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    radio = GuardianK5Radio(
        "COM3",
        serial_factory=lambda: port,
        uart_guard_seconds=6.25,
        monotonic=clock,
        sleeper=sleep,
    )
    radio.open()
    radio.set_ptt(True)

    assert sleeps == [pytest.approx(6.25)]
    assert radio.get_state().ptt is True


def test_k5_envelope_round_trip_keeps_command_crc() -> None:
    packet = k5_packet(0x06A4, b"state")
    assert decode_k5_envelope(encode_k5_envelope(packet)) == packet


def test_generic_vox_supports_complementary_aioc_lines() -> None:
    port = SimpleNamespace(dtr=False, rts=True, is_open=True)
    radio = VoxRadio("COM8", ptt_line="AIOC")

    radio._lines(port, True)
    assert (port.dtr, port.rts) == (True, False)
    radio._lines(port, False)
    assert (port.dtr, port.rts) == (False, True)


def test_invalid_guardian_ptt_mode_is_rejected_by_driver() -> None:
    with pytest.raises(ValueError):
        VoxRadio("COM8", ptt_line="bad")
