"""Hamlib backend — talk to rigctld over TCP.

Guardian never touches the COM port or the CAT dialect itself; rigctld does
that. We speak the simple rigctld text protocol:

    f            -> get frequency (Hz)
    F <hz>       -> set frequency
    m            -> get mode + passband
    M <mode> <p> -> set mode
    t            -> get PTT (0/1)
    T <0|1>      -> set PTT
    l STRENGTH   -> get S-meter

Each command's reply ends with a status line "RPRT <n>" (0 = OK) when the
command produces no data, or the data value(s) followed by nothing for getters.
We connect a fresh short-lived socket model is avoided — we keep one socket and
serialise calls behind a lock.
"""

from __future__ import annotations

import socket
import threading

from .base import RadioDriver, RadioState


class HamlibRadio(RadioDriver):
    name = "hamlib"

    def __init__(self, host: str = "127.0.0.1", port: int = 4532, timeout: float = 2.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._rx_buffer = bytearray()

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    def open(self) -> None:
        with self._lock:
            if self._sock is not None:
                return
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            sock.settimeout(self.timeout)
            self._sock = sock
            self._rx_buffer.clear()

    def close(self) -> None:
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                finally:
                    self._sock = None
                    self._rx_buffer.clear()

    def _readline(self) -> str:
        """Read one complete rigctld line without consuming the next reply."""
        if self._sock is None:
            raise ConnectionError("rigctld not connected")
        while b"\n" not in self._rx_buffer:
            chunk = self._sock.recv(1024)
            if not chunk:
                raise ConnectionError("rigctld closed the connection")
            self._rx_buffer.extend(chunk)
        raw, _, remaining = self._rx_buffer.partition(b"\n")
        self._rx_buffer = bytearray(remaining)
        return raw.rstrip(b"\r").decode("ascii", errors="replace")

    def _command(self, cmd: str, reply_lines: int = 1) -> list[str]:
        """Send one command and consume its complete logical reply.

        Several rigctld getters have different reply lengths. In particular
        ``m`` returns two lines (mode and passband). Reading an arbitrary TCP
        chunk left the second line queued and shifted every later CAT reply,
        which could turn a PTT command into a stale ``RPRT`` response.
        """
        if self._sock is None:
            raise ConnectionError("rigctld not connected")
        self._sock.sendall((cmd + "\n").encode("ascii"))
        return [self._readline() for _ in range(reply_lines)]

    @staticmethod
    def _ok(lines: list[str]) -> bool:
        for ln in lines:
            if ln.startswith("RPRT"):
                try:
                    return int(ln.split()[1]) == 0
                except (IndexError, ValueError):
                    return False
        return True  # getters have no RPRT on success

    def set_ptt(self, on: bool) -> None:
        with self._lock:
            lines = self._command(f"T {1 if on else 0}")
            if not self._ok(lines):
                raise IOError(f"PTT command failed: {lines}")

    def set_frequency(self, hz: int) -> None:
        with self._lock:
            lines = self._command(f"F {int(hz)}")
            if not self._ok(lines):
                raise IOError(f"set frequency failed: {lines}")

    def set_mode(self, mode: str, passband: int = 0) -> None:
        with self._lock:
            lines = self._command(f"M {mode} {passband}")
            if not self._ok(lines):
                raise IOError(f"set mode failed: {lines}")

    def get_state(self) -> RadioState:
        if self._sock is None:
            return RadioState(connected=False)
        st = RadioState(connected=True)
        try:
            with self._lock:
                freq = self._command("f")
                mode = self._command("m", reply_lines=2)
                ptt = self._command("t")
                sig = self._command("l STRENGTH")
            if freq and freq[0].lstrip("-").isdigit():
                st.frequency_hz = int(freq[0])
            if mode:
                st.mode = mode[0].strip()
            if ptt and ptt[0].strip() in ("0", "1"):
                st.ptt = ptt[0].strip() == "1"
            if sig and _is_int(sig[0]):
                st.signal = int(sig[0])
        except (OSError, ValueError) as exc:
            st.error = str(exc)
        return st


def _is_int(s: str) -> bool:
    s = s.strip()
    return s.lstrip("-").isdigit()
