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

from .presets import DUMMY_MODEL, find_executable


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
        self.args: list[str] = []     # what our own child was started with

    @property
    def available(self) -> bool:
        return self.exe is not None

    def command(
        self,
        model: int,
        com_port: str,
        tcp_port: int = 4532,
        baud: int = 0,
        ptt_type: str = "RIG",
    ) -> list[str]:
        """The argument list rigctld needs for this configuration.

        The subtlety is the dummy rig (model 1): it is a simulator and never
        opens the ``-r`` rig device, so handing it the COM port there *looks*
        configured while the port is never touched — an AIOC-cabled handheld
        keyed that way stays silent forever. A no-CAT radio needs the port
        passed as the *PTT device* (``--ptt-type RTS/DTR --ptt-file COMx``),
        which is exactly what the operator's "PTT via" setting selects.
        """
        args = ["-m", str(int(model)), "-t", str(int(tcp_port))]
        ptt = (ptt_type or "RIG").strip().upper()
        if com_port and int(model) != DUMMY_MODEL:
            args += ["-r", com_port]
        if baud:
            args += ["-s", str(int(baud))]
        if ptt in ("RTS", "DTR") and com_port:
            args += ["-P", ptt, "-p", com_port]
        return args

    def ensure(
        self,
        model: int,
        com_port: str,
        tcp_port: int = 4532,
        baud: int = 0,
        ptt_type: str = "RIG",
    ) -> str:
        """Make sure a *working* rigctld with *these* settings is on tcp_port.

        Reuses a responsive instance; if one is listening but wedged (accepts
        TCP, answers nothing — a dead serial link), kills it and starts fresh.
        A responsive instance that is our own child but was started with
        different arguments is restarted too: a changed PTT line or COM port
        only exists on the rigctld command line, so reusing the old process
        would silently keep the old wiring. Someone else's rigctld is left
        alone — we cannot know what it was started with."""
        desired = self.command(model, com_port, tcp_port, baud, ptt_type)
        if port_in_use("127.0.0.1", tcp_port):
            ours = self.proc is not None and self.proc.poll() is None
            if responds("127.0.0.1", tcp_port):
                if not ours or self.args == desired:
                    return (
                        f"rigctld already running on port {tcp_port} "
                        "(responding — reusing it)"
                    )
                self.stop()
            else:
                kill_stale_rigctld()
            for _ in range(20):  # wait up to ~2 s for the port to free
                if not port_in_use("127.0.0.1", tcp_port, timeout=0.1):
                    break
                time.sleep(0.1)
        return self.start(model, com_port, tcp_port, baud, ptt_type)

    def start(
        self,
        model: int,
        com_port: str,
        tcp_port: int = 4532,
        baud: int = 0,
        ptt_type: str = "RIG",
    ) -> str:
        """Launch rigctld. Returns a human-readable status string."""
        if self.exe is None:
            return "rigctld not found — install Hamlib or set its path"
        if port_in_use("127.0.0.1", tcp_port):
            return f"rigctld already running on port {tcp_port} (reusing it)"
        args = self.command(model, com_port, tcp_port, baud, ptt_type)
        try:
            self.proc = subprocess.Popen(
                [self.exe, *args],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            return f"failed to start rigctld: {exc}"
        self.args = args
        # The full command line is the one fact every PTT/CAT mystery needs;
        # putting it in the log costs a line and saves an afternoon.
        return f"rigctld started: rigctld {' '.join(args)}"

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        self.args = []
