"""Start/stop rigctld for the user.

Most operators shouldn't have to open a terminal. Guardian can spawn rigctld
itself with the right model/port, and tear it down on exit. If something is
already listening on the rigctld TCP port we assume the user (or another app)
started it and leave it alone.
"""

from __future__ import annotations

import socket
import subprocess

from .presets import find_executable


def port_in_use(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
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
