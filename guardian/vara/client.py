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
    data_bytes_written: int = 0
    data_bytes_read: int = 0
    data_socket_generation: int = 0
    data_local_endpoint: str | None = None
    data_peer_endpoint: str | None = None
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
        self._last_data_write = 0.0
        self._link_connected_at = 0.0

        self.state = VaraState()
        # Callback for asynchronous command-port notifications (UI/log hook).
        self.on_notification: Callable[[str], None] | None = None
        # Optional host-PTT hook: called with True/False when VARA signals
        # "PTT ON"/"PTT OFF". Lets Guardian be the sole keyer (CI-V/RTS/DTR) so
        # VARA never needs the COM port. None = VARA keys its own PTT as usual.
        self.on_ptt: Callable[[bool], None] | None = None

    # --- connection management ------------------------------------------
    def connect(self, timeout: float = 3.0) -> None:
        cmd: socket.socket | None = None
        with self._lock:
            if (
                self._cmd is not None
                and self._data is not None
                and self.state.cmd_connected
                and self.state.data_connected
            ):
                return
            self._close_sockets_locked()
            self._stop.clear()
            try:
                cmd = socket.create_connection(
                    (self.host, self.cmd_port), timeout=timeout
                )
                cmd.settimeout(None)
            except OSError as exc:
                if cmd is not None:
                    try:
                        cmd.close()
                    except OSError:
                        pass
                self.state.error = f"VARA TCP pair: {exc}"
                raise
            self._cmd = cmd
            self.state.cmd_connected = True
            self.state.data_connected = False
            self.state.error = None

        try:
            # Native VARA clients open the command/data TCP pair back-to-back.
            # Delaying 8301 until a command-reader thread has run can leave the
            # accepted data socket outside the command session VARA associates
            # with this application.
            data = socket.create_connection(
                (self.host, self.data_port), timeout=timeout
            )
            data.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            data.settimeout(None)
        except OSError as exc:
            self.state.error = f"VARA data port: {exc}"
            self.disconnect()
            raise
        with self._lock:
            if self._cmd is not cmd or not self.state.cmd_connected:
                data.close()
                raise ConnectionError("VARA command port closed during data pairing")
            self._data = data
            self.state.data_connected = True
            self._record_data_socket_locked(data)

        self._rx_thread = threading.Thread(
            target=self._reader, args=(cmd,), name="vara-rx", daemon=True
        )
        self._rx_thread.start()

    def disconnect(self) -> None:
        self._stop.set()
        with self._lock:
            self._close_sockets_locked()
            self.state.cmd_connected = False
            self.state.data_connected = False
            self.state.link_state = "DISCONNECTED"

    def _close_sockets_locked(self) -> None:
        for sock_attr in ("_cmd", "_data"):
            sock = getattr(self, sock_attr)
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError:
                    pass
                setattr(self, sock_attr, None)
        self.state.data_local_endpoint = None
        self.state.data_peer_endpoint = None

    @staticmethod
    def _endpoint(sock: socket.socket, peer: bool = False) -> str | None:
        try:
            value = sock.getpeername() if peer else sock.getsockname()
            return f"{value[0]}:{value[1]}"
        except (OSError, AttributeError, IndexError, TypeError):
            return None

    def _record_data_socket_locked(self, data: socket.socket) -> None:
        self.state.data_socket_generation += 1
        self.state.data_local_endpoint = self._endpoint(data)
        self.state.data_peer_endpoint = self._endpoint(data, peer=True)

    def renew_connection_pair(self, timeout: float = 3.0) -> None:
        """Replace VARA's command/data TCP pair before an RF session.

        VARA treats ports 8300 and 8301 as one application session. Closing
        either socket makes VARA close the other one as well, so attempting to
        renew only the data socket races the command reader and cannot produce
        a valid replacement pair.
        """
        with self._lock:
            if self._cmd is None or not self.state.cmd_connected:
                raise ConnectionError("VARA command port not connected")
            mycall = self.state.mycall

        self.disconnect()
        # Give VARA time to retire both halves of the old application session
        # before offering the next command/data pair.
        time.sleep(0.25)
        self.connect(timeout=timeout)

        # A fresh command socket is a fresh VARA application session. Restore
        # the protocol settings before the caller selects LISTEN ON/OFF.
        self.send_command("PUBLIC ON")
        self.send_command("COMPRESSION OFF")
        if mycall:
            self.set_mycall(mycall)

    @property
    def connected(self) -> bool:
        return (
            self._cmd is not None
            and self._data is not None
            and self.state.cmd_connected
            and self.state.data_connected
        )

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
        """Reset locally tracked TX bytes before queuing a new payload."""
        self.state.tx_buffer_bytes = 0
        self.state.data_bytes_written = 0

    def wait_data_ready(self, minimum_connected: float = 1.0) -> None:
        """Let VARA finish its CONNECTED/BREAK transition before port 8301 I/O.

        VARA FM may ignore an application write made immediately as CONNECTED
        is reported.  Waiting through the first link turnaround avoids that
        native-modem race while keeping the persistent TCP data socket intact.
        """
        remaining = minimum_connected - (
            time.monotonic() - self._link_connected_at
        )
        if remaining > 0:
            self._stop.wait(remaining)

    def write_data(self, data: bytes) -> None:
        """Send payload bytes over the VARA data port."""
        if self._data is None:
            raise ConnectionError("VARA data port not connected")
        socket_error = self._data.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        if socket_error:
            raise OSError(socket_error, "VARA data socket is not healthy")
        self._data.sendall(data)
        self.state.data_bytes_written += len(data)
        # VARA does not acknowledge each application write. BUFFER is an
        # asynchronous queue update and may not be emitted while the modem is
        # transmitting, so successful sendall() is the handoff boundary.
        self.state.tx_buffer_bytes = (
            (self.state.tx_buffer_bytes or 0) + len(data)
        )
        self._last_data_write = time.monotonic()

    def finish_data_write(self, minimum_delay: float = 2.0) -> None:
        """Let the independent data socket reach VARA before DISCONNECT.

        VARA's command and data streams are independent.  Its native protocol
        recommends allowing the final data write to arrive before sending the
        graceful DISCONNECT command, otherwise the command can overtake data.
        """
        remaining = minimum_delay - (time.monotonic() - self._last_data_write)
        if remaining > 0:
            self._stop.wait(remaining)

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
            self.state.data_bytes_read += len(chunk)
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
    def _reader(self, cmd: socket.socket) -> None:
        buf = b""
        try:
            while not self._stop.is_set():
                try:
                    chunk = cmd.recv(1024)
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
        finally:
            with self._lock:
                # An obsolete reader must not tear down a newer connection.
                if self._cmd is cmd:
                    self._close_sockets_locked()
                    self.state.cmd_connected = False
                    self.state.data_connected = False
                    self.state.link_state = "DISCONNECTED"
                    self.state.ptt = False

    def _handle_notification(self, text: str) -> None:
        self.state.last_notification = text
        upper = text.upper()
        if upper.startswith("CONNECTED"):
            self.state.link_state = "CONNECTED"
            self._link_connected_at = time.monotonic()
        elif upper.startswith("DISCONNECTED"):
            self.state.link_state = "DISCONNECTED"
        elif upper.startswith("PENDING") or upper.startswith("CONNECTING"):
            self.state.link_state = "CONNECTING"
        elif upper.startswith("BUFFER"):
            for token in upper.replace("=", " ").replace(":", " ").split()[1:]:
                try:
                    self.state.tx_buffer_bytes = max(0, int(token))
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
