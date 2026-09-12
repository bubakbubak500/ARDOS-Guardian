"""Controllable Guardian-firmware handhelds connected through an AIOC UART."""

from __future__ import annotations

import threading
import time
from dataclasses import replace

from .base import RadioDriver, RadioState
from .guardian_uart import (
    GuardianProtocolError,
    GuardianRadioRejected,
    GuardianVfoState,
    K5RuntimeClient,
    K61RuntimeClient,
)

try:
    import serial  # type: ignore
except ImportError:  # pragma: no cover - pyserial is a packaged dependency
    serial = None


class GuardianHandheldRadio(RadioDriver):
    """One-owner UART + PTT driver shared by the K5 and K61 profiles.

    VFO A is the calling/control channel and VFO B is the working channel.  A
    request matching A switches back to it; any other frequency is written to
    B and selects B.  Every write carries both VFOs and is verified by a fresh
    read-back.
    """

    reports_ptt = False
    no_cat = False
    protocol_kind = "k5"
    default_baud = 38_400
    uart_to_ptt_guard_seconds = 0.0

    def __init__(
        self,
        port: str,
        *,
        baud: int = 0,
        ptt_mode: str = "AIOC",
        serial_factory=None,
        uart_guard_seconds: float | None = None,
        monotonic=None,
        sleeper=None,
    ) -> None:
        self.port = str(port or "")
        self.baud = int(baud or self.default_baud)
        self.ptt_mode = (ptt_mode or "AIOC").upper()
        self._serial_factory = serial_factory
        self._clock = monotonic or time.monotonic
        self._sleep = sleeper or time.sleep
        self._uart_guard_seconds = (
            0.0 if serial_factory is not None and uart_guard_seconds is None
            else max(0.0, float(
                self.uart_to_ptt_guard_seconds
                if uart_guard_seconds is None else uart_guard_seconds
            ))
        )
        self._uart_quiet_until = 0.0
        self._ser = None
        self._client = None
        self._state: GuardianVfoState | None = None
        self._ptt = False
        self._lock = threading.RLock()
        self.firmware_version = ""

    @property
    def is_open(self) -> bool:
        return self._ser is not None and bool(getattr(self._ser, "is_open", False))

    @property
    def connection_summary(self) -> str:
        state = self._state
        if state is None:
            return self.firmware_version
        return (
            f"{self.firmware_version}; VFO A {state.vfo_a_rx * 10 / 1_000_000:.5f} MHz, "
            f"VFO B {state.vfo_b_rx * 10 / 1_000_000:.5f} MHz, "
            f"active {'A' if state.active_vfo == 0 else 'B'}, "
            f"Dual Watch {'on' if state.dual_watch else 'off'}"
        )

    def _make_serial(self):
        if self._serial_factory is not None:
            return self._serial_factory()
        if serial is None:
            raise RuntimeError("pyserial is not installed")
        return serial.Serial(port=None)

    def _safe_lines(self, port) -> None:
        # AIOC's common PTT source is DTR && !RTS.  The complementary mode is
        # therefore inactive as DTR=0, RTS=1.  Plain active-high RTS/DTR modes
        # remain available for interfaces wired like the old Dummy profile.
        port.dtr = False
        port.rts = self.ptt_mode == "AIOC"

    def _mark_uart_activity(self) -> None:
        if self._uart_guard_seconds:
            self._uart_quiet_until = max(
                self._uart_quiet_until,
                self._clock() + self._uart_guard_seconds,
            )

    def _wait_for_uart_quiet(self) -> None:
        remaining = self._uart_quiet_until - self._clock()
        if remaining > 0.0:
            self._sleep(remaining)

    def _configure_serial(self, port) -> None:
        port.port = self.port
        port.baudrate = self.baud
        if serial is not None:
            port.bytesize = serial.EIGHTBITS
            port.parity = serial.PARITY_NONE
            port.stopbits = serial.STOPBITS_ONE
        port.timeout = 0.15
        port.write_timeout = 3.0
        port.xonxoff = False
        port.rtscts = False
        port.dsrdtr = False

    def open(self) -> None:
        with self._lock:
            if self.is_open:
                return
            if not self.port:
                raise ValueError("AIOC UART serial port is not configured")
            port = self._make_serial()
            self._configure_serial(port)
            # Set the inactive wire state both before and after open: some
            # Windows drivers briefly apply their own defaults on open().
            self._safe_lines(port)
            self._ser = port
            try:
                opener = getattr(port, "open", None)
                if callable(opener) and not getattr(port, "is_open", False):
                    opener()
                self._safe_lines(port)
                port.reset_input_buffer()
                client_type = K61RuntimeClient if self.protocol_kind == "k61" else K5RuntimeClient
                self._client = client_type(port)
                self.firmware_version = self._client.hello()
                self._state = self._client.read_state()
                self._mark_uart_activity()
                self._ptt = False
            except Exception:
                try:
                    self._safe_lines(port)
                    port.close()
                finally:
                    self._ser = None
                    self._client = None
                    self._state = None
                    self._ptt = False
                    self._uart_quiet_until = 0.0
                raise

    def close(self) -> None:
        with self._lock:
            port = self._ser
            if port is None:
                return
            try:
                try:
                    self._set_ptt_locked(False)
                finally:
                    self._safe_lines(port)
            finally:
                try:
                    port.close()
                finally:
                    self._ser = None
                    self._client = None
                    self._state = None
                    self._ptt = False
                    self._uart_quiet_until = 0.0

    def _set_ptt_locked(self, on: bool) -> None:
        port = self._ser
        if port is None or not getattr(port, "is_open", False):
            raise ConnectionError("AIOC UART serial port is not open")
        enabled = bool(on)
        if self.ptt_mode == "AIOC":
            if enabled:
                port.rts = False
                port.dtr = True
            else:
                port.dtr = False
                port.rts = True
        elif self.ptt_mode == "DTR":
            port.rts = False
            port.dtr = enabled
        else:
            port.dtr = False
            port.rts = enabled
        self._ptt = enabled

    def set_ptt(self, on: bool) -> None:
        with self._lock:
            if on:
                self._wait_for_uart_quiet()
            self._set_ptt_locked(on)

    def _read_state_locked(self) -> GuardianVfoState:
        if self._client is None or not self.is_open:
            raise ConnectionError("AIOC UART serial port is not open")
        # GK5 deliberately ignores its external PTT pin for six seconds after
        # every serial configuration command.  A status poll must therefore
        # not keep extending that window forever.  Explicit open/tune paths
        # still perform verified reads and refresh this cache.
        if self._state is not None and (
            self._ptt or self._uart_guard_seconds > 0.0
        ):
            return self._state
        self._state = self._client.read_state()
        self._mark_uart_activity()
        return self._state

    def get_state(self) -> RadioState:
        with self._lock:
            if not self.is_open:
                return RadioState(connected=False)
            state = self._read_state_locked()
            return RadioState(
                connected=True,
                frequency_hz=state.active_frequency * 10,
                mode="FM",
                ptt=self._ptt,
            )

    @staticmethod
    def _matches_requested(
        actual: GuardianVfoState,
        expected: GuardianVfoState,
        *,
        active_vfo: int,
        vfo_b_frequency: int,
    ) -> bool:
        return (
            actual.vfo_a_rx == expected.vfo_a_rx
            and actual.vfo_a_tx == expected.vfo_a_tx
            and actual.vfo_b_rx == vfo_b_frequency
            and actual.vfo_b_tx == vfo_b_frequency
            and actual.active_vfo == active_vfo
            and actual.dual_watch == expected.dual_watch
            and actual.active_profile == expected.active_profile
        )

    def set_frequency(self, hz: int) -> None:
        wire = round(int(hz) / 10)
        if wire <= 0 or wire > 0xFFFFFFFF:
            raise ValueError("frequency is outside the Guardian UART range")
        with self._lock:
            if self._ptt:
                raise RuntimeError("cannot tune the radio while PTT is active")
            current = self._read_state_locked()
            active_vfo = 0 if wire == current.vfo_a_rx else 1
            working = current.vfo_b_rx if active_vfo == 0 else wire
            last_error: Exception | None = None
            for _attempt in range(2):
                try:
                    try:
                        reply = self._client.set_state(
                            current,
                            active_vfo=active_vfo,
                            vfo_b_frequency=working,
                        )
                    finally:
                        self._mark_uart_activity()
                    break
                except GuardianRadioRejected:
                    # A definite firmware result (especially "transmitting")
                    # is not an ambiguous lost reply and must not be retried.
                    raise
                except GuardianProtocolError as exc:
                    last_error = exc
            else:
                raise GuardianProtocolError(
                    f"Guardian UART tuning was not confirmed: {last_error}"
                )
            if not self._matches_requested(
                reply,
                current,
                active_vfo=active_vfo,
                vfo_b_frequency=working,
            ):
                raise GuardianProtocolError(
                    "radio acknowledgement does not match the requested VFO state"
                )
            confirmed = self._client.read_state()
            self._mark_uart_activity()
            if not self._matches_requested(
                confirmed,
                current,
                active_vfo=active_vfo,
                vfo_b_frequency=working,
            ):
                raise GuardianProtocolError("Guardian UART VFO read-back mismatch")
            self._state = confirmed

    def set_mode(self, mode: str, passband_hz: int = 0) -> None:
        del passband_hz
        normal = (mode or "").strip().upper().replace("-", "")
        if normal not in {"FM", "NFM", "PKTFM", "DATAFM"}:
            raise ValueError(f"Guardian handheld supports FM channels, not {mode!r}")
        # The UART v3 contract intentionally leaves the Guardian RF/audio
        # profile unchanged.  FM is therefore a compatibility assertion only.

    @property
    def guardian_vfo_state(self) -> GuardianVfoState:
        with self._lock:
            if not self.is_open:
                raise ConnectionError("AIOC UART serial port is not open")
            return self._read_state_locked()

    def read_guardian_eeprom(self, offset: int, size: int) -> bytes:
        """Read GK5 calibration/configuration bytes without permitting a write."""

        with self._lock:
            if self._ptt:
                raise RuntimeError("cannot read Guardian EEPROM while PTT is active")
            if not isinstance(self._client, K5RuntimeClient) or not self.is_open:
                raise NotImplementedError("EEPROM diagnostics are only exposed by GK5")
            try:
                return self._client.read_eeprom(offset, size)
            finally:
                self._mark_uart_activity()

    def set_guardian_profile(self, profile: int, *, persist: bool = False) -> None:
        """Select a GK5 firmware filter profile without changing modem bandwidth."""

        with self._lock:
            if self._ptt:
                raise RuntimeError("cannot change Guardian profile while PTT is active")
            if not isinstance(self._client, K5RuntimeClient):
                raise NotImplementedError("runtime filter profiles are only exposed by GK5")
            try:
                active_profile, register_43 = self._client.set_profile(
                    int(profile), persist=bool(persist)
                )
            finally:
                self._mark_uart_activity()
            if self._state is not None:
                self._state = replace(
                    self._state,
                    active_profile=active_profile,
                    register_43=register_43,
                )


class GuardianK5Radio(GuardianHandheldRadio):
    name = "guardian-k5"
    protocol_kind = "k5"
    default_baud = 38_400
    # GK5 1.5 sets a 12 x 500 ms SerialConfigInProgress timer after every
    # Guardian UART operation and suppresses the external PTT pin until it
    # expires.  The margin covers the firmware scheduler tick and USB jitter.
    uart_to_ptt_guard_seconds = 6.25


class GuardianK61Radio(GuardianHandheldRadio):
    name = "guardian-k61"
    protocol_kind = "k61"
    default_baud = 115_200
