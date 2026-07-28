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
from dataclasses import dataclass
from enum import Enum
from typing import Callable


class TransferResult(Enum):
    """Outcome of waiting for VARA to consume and transmit a data write."""

    DRAINED = "drained"
    PEER_CLOSED_EARLY = "peer_closed_early"
    TIMEOUT = "timeout"
    NO_BUFFER_REPORTS = "no_buffer_reports"


@dataclass
class VaraState:
    cmd_connected: bool = False
    data_connected: bool = False
    mycall: str = ""
    link_state: str = "DISCONNECTED"   # as reported by VARA
    last_notification: str = ""
    error: str | None = None
    # True once VARA's TCP session dies under us.  A dropped socket forces
    # link_state to DISCONNECTED, which is indistinguishable from a graceful
    # RF close -- without this flag a killed VARA reads as a delivered payload.
    transport_lost: bool = False
    tx_buffer_bytes: int | None = None
    buffer_reports: int = 0
    data_socket_reopens: int = 0
    tx_bitrate_bps: int | None = None
    data_bytes_written: int = 0
    data_bytes_read: int = 0
    data_socket_generation: int = 0
    data_local_endpoint: str | None = None
    data_peer_endpoint: str | None = None
    ptt: bool = False
    ptt_keyings: int = 0


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
        self._buffer_reported = threading.Event()
        self._buffer_nonzero = threading.Event()
        self._last_data_write = 0.0
        self._link_connected_at = 0.0
        self._last_ptt_activity = 0.0

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
            self.state.transport_lost = False
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
        """Start a new BUFFER-notification observation window."""
        self._buffer_reported.clear()
        self._buffer_nonzero.clear()
        self.state.tx_buffer_bytes = None
        self.state.buffer_reports = 0
        self.state.data_bytes_written = 0
        self.state.ptt_keyings = 0

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

    def _notify(self, text: str) -> None:
        if self.on_notification is not None:
            try:
                self.on_notification(text)
            except Exception:
                pass

    def data_socket_alive(self) -> bool:
        """True while VARA still holds its end of the data connection.

        A lone sendall() into a socket the peer has already closed succeeds
        locally -- the reset only arrives afterwards -- so a payload can be
        "written" to a VARA that will never see it.  Peek instead.
        """
        sock = self._data
        if sock is None:
            return False
        try:
            if sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR):
                return False
            sock.setblocking(False)
            try:
                pending = sock.recv(1, socket.MSG_PEEK)
            finally:
                sock.setblocking(True)
        except BlockingIOError:
            return True          # nothing readable: healthy and idle
        except OSError:
            return False
        return bool(pending)     # b"" means VARA closed its end

    def reopen_data_socket(self, timeout: float = 3.0) -> bool:
        """Replace a data socket VARA has dropped, keeping the command port."""
        try:
            data = socket.create_connection(
                (self.host, self.data_port), timeout=timeout
            )
            data.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            data.settimeout(None)
        except OSError as exc:
            self.state.error = f"VARA data port: {exc}"
            self._notify(f"VARA data port reconnect failed: {exc}")
            return False
        with self._lock:
            stale, self._data = self._data, data
            self.state.data_connected = True
            self.state.data_socket_reopens += 1
            self._record_data_socket_locked(data)
        if stale is not None:
            try:
                stale.close()
            except OSError:
                pass
        self._notify(
            "VARA data port reconnected (generation "
            f"{self.state.data_socket_generation})"
        )
        return True

    def write_data(self, data: bytes) -> None:
        """Send payload bytes over the VARA data port."""
        if self._data is None:
            raise ConnectionError("VARA data port not connected")
        if not self.data_socket_alive():
            self._notify(
                "VARA data socket was closed by VARA; reconnecting before write"
            )
            if not self.reopen_data_socket():
                raise ConnectionError("VARA data port could not be reopened")
        self._data.sendall(data)
        self.state.data_bytes_written += len(data)
        # Do not mix locally written bytes with VARA's asynchronous BUFFER
        # telemetry.  The notification state machine below is the only writer
        # of tx_buffer_bytes after prepare_data_transfer().
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

    def wait_transfer_complete(
        self,
        timeout: float = 180.0,
        ingest_timeout: float = 10.0,
    ) -> TransferResult:
        """Wait for a post-write nonzero BUFFER report and its later drain.

        Command port 8300 and data port 8301 are independent TCP streams.  A
        successful sendall() therefore does not prove that VARA has placed the
        bytes in its RF queue.  First require BUFFER > 0, then accept BUFFER 0.
        """
        ingest_deadline = time.monotonic() + min(timeout, ingest_timeout)
        while not self._buffer_nonzero.is_set():
            if self._stop.is_set() or self.state.link_state == "DISCONNECTED":
                return TransferResult.PEER_CLOSED_EARLY
            if time.monotonic() >= ingest_deadline:
                return TransferResult.NO_BUFFER_REPORTS
            self._stop.wait(0.05)

        drain_deadline = time.monotonic() + timeout
        while True:
            if self.state.tx_buffer_bytes == 0:
                return TransferResult.DRAINED
            if self._stop.is_set() or self.state.link_state == "DISCONNECTED":
                return TransferResult.PEER_CLOSED_EARLY
            if time.monotonic() >= drain_deadline:
                return TransferResult.TIMEOUT
            self._stop.wait(0.05)

    def read_exactly(self, n: int, timeout: float = 60.0) -> bytes:
        """Read exactly n payload bytes (raises on timeout/short read)."""
        if self._data is None:
            raise ConnectionError("VARA data port not connected")
        data = self._data
        data.settimeout(timeout)
        deadline = time.monotonic() + timeout
        buf = bytearray()
        try:
            while len(buf) < n:
                if time.monotonic() > deadline:
                    raise TimeoutError("timed out reading payload")
                chunk = data.recv(n - len(buf))
                if not chunk:
                    raise ConnectionError("VARA data connection closed")
                buf += chunk
                self.state.data_bytes_read += len(chunk)
            return bytes(buf)
        finally:
            try:
                data.settimeout(None)
            except OSError:
                pass

    def ptt_quiet_for(self) -> float:
        """Seconds since VARA last keyed or unkeyed; 0.0 while transmitting."""
        if self.state.ptt:
            return 0.0
        if not self._last_ptt_activity:
            return float("inf")
        return time.monotonic() - self._last_ptt_activity

    def wait_link(
        self,
        target: str,
        timeout: float = 30.0,
        *,
        ptt_grace: float = 0.0,
        max_wait: float | None = None,
    ) -> bool:
        """Block until link_state reaches `target` (e.g. 'CONNECTED').

        With ptt_grace > 0 the deadline is pushed back for as long as VARA
        keeps keying the transmitter.  A graceful DISCONNECT puts the queued
        RF payload on the air *before* closing, and at VARA FM's unregistered
        566 bps rate that easily outlasts any fixed timeout -- giving up there
        aborts a transfer that is still being sent.  max_wait bounds the total
        wait so a stuck modem cannot hold the session open forever.
        """
        start = time.monotonic()
        deadline = start + timeout
        hard_deadline = None if max_wait is None else start + max_wait
        while True:
            # Checked first: a lost socket forces link_state to DISCONNECTED,
            # so matching the target here would report a dead VARA as a clean
            # close -- and a payload that never flew as delivered.
            if self.state.transport_lost:
                return False
            if self.state.link_state == target:
                return True
            if self._stop.is_set():
                return False
            now = time.monotonic()
            if hard_deadline is not None and now >= hard_deadline:
                return self.state.link_state == target
            if now >= deadline:
                if ptt_grace <= 0 or self.ptt_quiet_for() >= ptt_grace:
                    return self.state.link_state == target
                # Still keying: VARA is draining its RF queue, keep waiting.
                deadline = now + ptt_grace
            self._stop.wait(0.1)

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
            lost = False
            with self._lock:
                # An obsolete reader must not tear down a newer connection.
                if self._cmd is cmd:
                    self._close_sockets_locked()
                    self.state.cmd_connected = False
                    self.state.data_connected = False
                    self.state.link_state = "DISCONNECTED"
                    self.state.ptt = False
                    # Only an unexpected loss counts; disconnect() is
                    # deliberate and sets _stop before closing the socket.
                    lost = not self._stop.is_set()
                    self.state.transport_lost = lost
            if lost:
                self._notify("VARA TCP session lost (VARA closed or exited)")

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
                    value = max(0, int(token))
                    self.state.tx_buffer_bytes = value
                    self.state.buffer_reports += 1
                    self._buffer_reported.set()
                    if value > 0:
                        self._buffer_nonzero.set()
                    break
                except ValueError:
                    continue
        elif upper.startswith("BITRATE"):
            # e.g. "BITRATE (1)  566 bps TX" -- the rate drives our airtime
            # estimate, which is the only progress signal an unregistered
            # VARA FM link gives us.
            tokens = upper.replace("(", " ").replace(")", " ").split()
            for index, token in enumerate(tokens):
                if token == "BPS" and index:
                    try:
                        self.state.tx_bitrate_bps = max(0, int(tokens[index - 1]))
                    except ValueError:
                        pass
                    break
        elif upper == "PTT ON" or upper == "PTT OFF":
            self.state.ptt = upper == "PTT ON"
            self._last_ptt_activity = time.monotonic()
            if self.state.ptt:
                self.state.ptt_keyings += 1
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
