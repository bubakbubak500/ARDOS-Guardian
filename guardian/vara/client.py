"""VARA FM TCP client (Phase 2 skeleton).

VARA exposes two TCP sockets:

  * command port (default 8300) — newline/CR terminated text commands and
    asynchronous notifications ("PTT ON", "CONNECTED", "DISCONNECTED", ...).
  * data port    (default 8301) — the raw message payload stream.

Guardian's job is *orchestration*: set MYCALL, LISTEN, CONNECT to the agreed
next hop, pump the payload over the data socket, and watch for the
CONNECTED / DISCONNECTED / BUFFER notifications. This module gives us a
connection + command/notification plumbing to build the handshake on; the
full session state-machine is wired in a later phase.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class VaraState:
    cmd_connected: bool = False
    data_connected: bool = False
    mycall: str = ""
    link_state: str = "DISCONNECTED"   # as reported by VARA
    last_notification: str = ""
    error: str | None = None
    tx_buffer_bytes: int | None = None
    ptt: bool = False


class VaraClient:
    """Minimal, thread-safe VARA command/data connection."""

    def __init__(self, host: str = "127.0.0.1", cmd_port: int = 8300, data_port: int = 8301):
        self.host = host
        self.cmd_port = cmd_port
        self.data_port = data_port

        self._cmd: socket.socket | None = None
        self._data: socket.socket | None = None
        self._lock = threading.Lock()
        self._rx_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._buffer_update = threading.Event()

        self.state = VaraState()
        # Callback for asynchronous command-port notifications (UI/log hook).
        self.on_notification: Callable[[str], None] | None = None
        # Optional host-PTT hook: called with True/False when VARA signals
        # "PTT ON"/"PTT OFF". Lets Guardian be the sole keyer (CI-V/RTS/DTR) so
        # VARA never needs the COM port. None = VARA keys its own PTT as usual.
        self.on_ptt: Callable[[bool], None] | None = None

    # --- connection management ------------------------------------------
    def connect(self, timeout: float = 3.0) -> None:
        with self._lock:
            if self._cmd is not None:
                return
            self._stop.clear()
            self._cmd = socket.create_connection((self.host, self.cmd_port), timeout=timeout)
            self._cmd.settimeout(None)
            self.state.cmd_connected = True
            try:
                self._data = socket.create_connection((self.host, self.data_port), timeout=timeout)
                self.state.data_connected = True
            except OSError as exc:
                # Data port is optional until we actually transfer.
                self.state.error = f"data port: {exc}"

        self._rx_thread = threading.Thread(target=self._reader, name="vara-rx", daemon=True)
        self._rx_thread.start()

    def disconnect(self) -> None:
        self._stop.set()
        with self._lock:
            for sock_attr in ("_cmd", "_data"):
                sock = getattr(self, sock_attr)
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
                    setattr(self, sock_attr, None)
            self.state.cmd_connected = False
            self.state.data_connected = False
            self.state.link_state = "DISCONNECTED"

    @property
    def connected(self) -> bool:
        return self._cmd is not None

    # --- commands --------------------------------------------------------
    def send_command(self, command: str) -> None:
        """Send a VARA command line (CR-terminated, as VARA expects)."""
        with self._lock:
            if self._cmd is None:
                raise ConnectionError("VARA command port not connected")
            self._cmd.sendall((command + "\r").encode("ascii"))

    def set_mycall(self, callsign: str) -> None:
        self.state.mycall = callsign.upper()
        self.send_command(f"MYCALL {self.state.mycall}")

    def listen(self, on: bool = True) -> None:
        self.send_command(f"LISTEN {'ON' if on else 'OFF'}")

    def connect_to(self, target: str) -> None:
        """Initiate a VARA link to the agreed next hop."""
        self.send_command(f"CONNECT {self.state.mycall} {target.upper()}")

    def abort(self) -> None:
        self.send_command("ABORT")

    def disconnect_link(self) -> None:
        """Gracefully close after VARA has transmitted its queued data."""
        self.send_command("DISCONNECT")

    # --- data path (payload bytes) --------------------------------------
    def prepare_data_transfer(self) -> None:
        """Discard stale BUFFER state before queuing a new payload."""
        self.state.tx_buffer_bytes = None
        self._buffer_update.clear()

    def wait_data_accepted(self, timeout: float = 5.0) -> bool:
        """Wait until VARA confirms that it consumed data from TCP port 8301."""
        return self._buffer_update.wait(timeout)

    def write_data(self, data: bytes) -> None:
        """Send payload bytes over the VARA data port."""
        if self._data is None:
            raise ConnectionError("VARA data port not connected")
        self._data.sendall(data)

    def wait_transfer_complete(self, timeout: float = 180.0) -> bool:
        """Wait until VARA drains its RF queue or the peer closes the link."""
        deadline = time.monotonic() + timeout
        was_connected = self.state.link_state == "CONNECTED"
        while time.monotonic() < deadline:
            if self.state.tx_buffer_bytes == 0:
                return True
            if was_connected and self.state.link_state == "DISCONNECTED":
                return True
            if self.state.link_state == "CONNECTED":
                was_connected = True
            time.sleep(0.05)
        return self.state.tx_buffer_bytes == 0

    def read_exactly(self, n: int, timeout: float = 60.0) -> bytes:
        """Read exactly n payload bytes (raises on timeout/short read)."""
        if self._data is None:
            raise ConnectionError("VARA data port not connected")
        import time as _t
        self._data.settimeout(timeout)
        deadline = _t.monotonic() + timeout
        buf = bytearray()
        while len(buf) < n:
            if _t.monotonic() > deadline:
                raise TimeoutError("timed out reading payload")
            chunk = self._data.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("VARA data connection closed")
            buf += chunk
        return bytes(buf)

    def wait_link(self, target: str, timeout: float = 30.0) -> bool:
        """Block until link_state reaches `target` (e.g. 'CONNECTED')."""
        import time as _t
        deadline = _t.monotonic() + timeout
        while _t.monotonic() < deadline:
            if self.state.link_state == target:
                return True
            if target == "CONNECTED" and self.state.link_state == "DISCONNECTED" \
                    and _t.monotonic() > deadline - timeout + 1:
                pass  # keep waiting; DISCONNECTED is the initial state
            _t.sleep(0.1)
        return self.state.link_state == target

    # --- background notification reader ----------------------------------
    def _reader(self) -> None:
        buf = b""
        while not self._stop.is_set() and self._cmd is not None:
            try:
                chunk = self._cmd.recv(1024)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\r" in buf or b"\n" in buf:
                # VARA terminates notifications with CR; tolerate LF too.
                sep = min(
                    (buf.index(b) for b in (b"\r", b"\n") if b in buf),
                    default=-1,
                )
                if sep < 0:
                    break
                line, buf = buf[:sep], buf[sep + 1 :]
                text = line.decode("ascii", errors="replace").strip()
                if text:
                    self._handle_notification(text)
        self.state.cmd_connected = False

    def _handle_notification(self, text: str) -> None:
        self.state.last_notification = text
        upper = text.upper()
        if upper.startswith("CONNECTED"):
            self.state.link_state = "CONNECTED"
        elif upper.startswith("DISCONNECTED"):
            self.state.link_state = "DISCONNECTED"
        elif upper.startswith("PENDING") or upper.startswith("CONNECTING"):
            self.state.link_state = "CONNECTING"
        elif upper.startswith("BUFFER"):
            for token in upper.replace("=", " ").replace(":", " ").split()[1:]:
                try:
                    self.state.tx_buffer_bytes = max(0, int(token))
                    self._buffer_update.set()
                    break
                except ValueError:
                    continue
        elif upper == "PTT ON" or upper == "PTT OFF":
            self.state.ptt = upper == "PTT ON"
            # VARA wants to key/unkey. If a host-PTT hook is wired, Guardian does
            # the actual keying (so VARA needs no COM port of its own).
            if self.on_ptt is not None:
                try:
                    self.on_ptt(upper == "PTT ON")
                except Exception:
                    pass
        if self.on_notification is not None:
            try:
                self.on_notification(text)
            except Exception:
                pass
