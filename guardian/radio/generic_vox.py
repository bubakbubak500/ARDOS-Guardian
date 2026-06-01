"""Generic VOX / dumb-radio backend.

No CAT, no telemetry — just key PTT by asserting an RS-232 control line
(RTS or DTR) on a serial port. Many cheap interfaces (and home-brew ones)
wire PTT to one of these lines. Frequency/mode are not knowable here, so
those getters report None and the setters are unsupported.
"""

from __future__ import annotations

from .base import RadioDriver, RadioState

try:
    import serial  # type: ignore
except ImportError:  # pragma: no cover - pyserial is a hard dep, but be safe
    serial = None


class VoxRadio(RadioDriver):
    name = "vox"

    def __init__(self, port: str, ptt_line: str = "RTS", baud: int = 9600):
        self.port = port
        self.ptt_line = (ptt_line or "RTS").upper()
        self.baud = baud
        self._ser = None

    @property
    def is_open(self) -> bool:
        return self._ser is not None and getattr(self._ser, "is_open", False)

    def open(self) -> None:
        if serial is None:
            raise RuntimeError("pyserial is not installed")
        if self._ser is not None:
            return
        self._ser = serial.Serial(self.port, self.baud, timeout=1)
        self.set_ptt(False)

    def close(self) -> None:
        if self._ser is not None:
            try:
                self.set_ptt(False)
                self._ser.close()
            finally:
                self._ser = None

    def set_ptt(self, on: bool) -> None:
        if self._ser is None:
            raise ConnectionError("serial port not open")
        if self.ptt_line == "DTR":
            self._ser.dtr = on
        else:
            self._ser.rts = on

    def get_state(self) -> RadioState:
        if not self.is_open:
            return RadioState(connected=False)
        ptt = self._ser.dtr if self.ptt_line == "DTR" else self._ser.rts
        return RadioState(connected=True, ptt=bool(ptt))
