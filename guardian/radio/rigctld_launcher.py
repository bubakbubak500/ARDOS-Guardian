"""Start/stop rigctld for the user.

Most operators shouldn't have to open a terminal. Guardian can spawn rigctld
itself with the right model/port, and tear it down on exit. If something is
already listening on the rigctld TCP port we assume the user (or another app)
started it and leave it alone.
"""

from __future__ import annotations

import platform
import socket
import subprocess
import time

from .presets import find_executable


def port_in_use(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def responds(host: str, port: int, timeout: float = 2.0) -> bool:
    """True if a rigctld on host:port actually *answers* a command — not just
    accepts the TCP connection. A wedged rigctld (dead serial link) accepts the
    socket but never replies, which is exactly the failure we want to catch."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(b"f\n")          # get frequency — cheapest getter
            return bool(s.recv(64))
    except OSError:
        return False


def kill_stale_rigctld() -> bool:
    """Force-kill orphaned rigctld processes (Windows). Used only when the one
    holding our port is wedged. Returns True if the kill command ran."""
    if platform.system() != "Windows":
        return False
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "rigctld.exe"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except OSError:
        return False


class RigctldProcess:
    """Manages a rigctld child process."""

    def __init__(self, rigctld_path: str = "rigctld"):
        self.exe = find_executable("rigctld", rigctld_path)
        self.proc: subprocess.Popen | None = None

    @property
    def available(self) -> bool:
        return self.exe is not None

    def ensure(self, model: int, com_port: str, tcp_port: int = 4532, baud: int = 0) -> str:
        """Make sure a *working* rigctld is on tcp_port, then return status.

        Reuses a responsive instance; if one is listening but wedged (accepts
        TCP, answers nothing — a dead serial link), kills it and starts fresh.
        This is the safety net for an orphaned rigctld left by a crash/kill."""
        if port_in_use("127.0.0.1", tcp_port):
            if responds("127.0.0.1", tcp_port):
                return f"rigctld already running on port {tcp_port} (responding — reusing it)"
            kill_stale_rigctld()
            for _ in range(20):  # wait up to ~2 s for the port to free
                if not port_in_use("127.0.0.1", tcp_port, timeout=0.1):
                    break
                time.sleep(0.1)
        return self.start(model, com_port, tcp_port, baud)

    def start(self, model: int, com_port: str, tcp_port: int = 4532, baud: int = 0) -> str:
        """Launch rigctld. Returns a human-readable status string."""
        if self.exe is None:
            return "rigctld not found — install Hamlib or set its path"
        if port_in_use("127.0.0.1", tcp_port):
            return f"rigctld already running on port {tcp_port} (reusing it)"
        args = [self.exe, "-m", str(int(model)), "-t", str(int(tcp_port))]
        if com_port:
            args += ["-r", com_port]
        if baud:
            args += ["-s", str(int(baud))]
        try:
            self.proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            return f"failed to start rigctld: {exc}"
        return f"rigctld started (model {model} on {com_port or 'default'}, tcp {tcp_port})"

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
