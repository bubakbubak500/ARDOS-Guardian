"""Generic VOX / dumb-radio backend.

No CAT, no telemetry — just key PTT by asserting an RS-232 control line
(RTS, DTR, or complementary AIOC) on a serial port. Many cheap interfaces (and home-brew ones)
wire PTT to one of these lines. Frequency/mode are not knowable here, so
those getters report None and the setters are unsupported.
"""

from __future__ import annotations

import threading

from .base import RadioDriver, RadioState

try:
    import serial  # type: ignore
except ImportError:  # pragma: no cover - pyserial is a hard dep, but be safe
    serial = None


class VoxRadio(RadioDriver):
    name = "vox"
    # get_state() reads back the RTS/DTR line *we* asserted, not the radio. It
    # proves the port accepted the change and nothing more.
    reports_ptt = False
    no_cat = True

    def __init__(self, port: str, ptt_line: str = "RTS", baud: int = 9600):
        self.port = port
        self.ptt_line = (ptt_line or "RTS").upper()
        if self.ptt_line not in {"RTS", "DTR", "AIOC"}:
            raise ValueError("PTT line must be RTS, DTR, or AIOC")
        self.baud = baud
        self._ser = None
        self._lock = threading.RLock()
        self.manual_frequency_hz = 0

    def _lines(self, port, on: bool) -> None:
        if self.ptt_line == "AIOC":
            if on:
                port.rts = False
                port.dtr = True
            else:
                port.dtr = False
                port.rts = True
        elif self.ptt_line == "DTR":
            port.rts = False
            port.dtr = on
        else:
            port.dtr = False
            port.rts = on

    @property
    def is_open(self) -> bool:
        return self._ser is not None and getattr(self._ser, "is_open", False)

    def open(self) -> None:
        if serial is None:
            raise RuntimeError("pyserial is not installed")
        with self._lock:
            if self._ser is not None:
                return
            port = serial.Serial(port=None, baudrate=self.baud, timeout=1)
            try:
                # Establish inactive lines before opening; opening with serial
                # defaults can briefly key a firmware-independent AIOC path.
                self._lines(port, False)
                port.port = self.port
                port.open()
                self._lines(port, False)
                self._ser = port
            except Exception:
                port.close()
                raise

    def close(self) -> None:
        with self._lock:
            if self._ser is not None:
                try:
                    self.set_ptt(False)
                finally:
                    try:
                        self._ser.close()
                    finally:
                        self._ser = None

    def set_ptt(self, on: bool) -> None:
        with self._lock:
            if not self.is_open:
                raise ConnectionError("serial port not open")
            self._lines(self._ser, bool(on))

    def get_state(self) -> RadioState:
        with self._lock:
            if not self.is_open:
                return RadioState(connected=False)
            ptt = (self._ser.dtr and not self._ser.rts if self.ptt_line == "AIOC"
                   else self._ser.dtr if self.ptt_line == "DTR" else self._ser.rts)
            return RadioState(connected=True, ptt=bool(ptt))
