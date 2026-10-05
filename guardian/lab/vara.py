"""Private VARA installation copies, exact binary selection and TCP ownership."""
from __future__ import annotations

import configparser
import ctypes
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

from .identity import sha256


def tcp_owners(*, established: bool = False) -> dict[int, int]:
    if sys.platform != "win32":
        raise RuntimeError("Owned VARA processes currently require Windows")
    class Row(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulong) for name in
                    ("state", "local_addr", "local_port", "remote_addr", "remote_port", "pid")]
    size = ctypes.c_ulong(0)
    api = ctypes.windll.iphlpapi.GetExtendedTcpTable
    table_class = 5 if established else 3  # OWNER_PID_ALL / OWNER_PID_LISTENER
    result = api(None, ctypes.byref(size), False, socket.AF_INET, table_class, 0)
    if result not in (0, 122):
        raise OSError(result, "Cannot query TCP ownership")
    buf = ctypes.create_string_buffer(size.value)
    result = api(buf, ctypes.byref(size), False, socket.AF_INET, table_class, 0)
    if result:
        raise OSError(result, "Cannot query TCP ownership")
    count = ctypes.c_ulong.from_buffer(buf).value
    rows = (Row * count).from_buffer(buf, ctypes.sizeof(ctypes.c_ulong))
    allowed = {2, 5} if established else {2}  # LISTEN / ESTABLISHED
    return {socket.ntohs(row.local_port & 0xffff): int(row.pid)
            for row in rows if row.state in allowed}


def listeners() -> dict[int, int]:
    return tcp_owners()


def read_ini(path: Path):
    raw = path.read_bytes()
    encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else ("mbcs" if sys.platform == "win32" else "cp1252")
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        parser.read_string(raw.decode(encoding))
    except (configparser.Error, UnicodeError):
        # ConfigParser exceptions can contain the raw offending licence line.
        raise ValueError("VARA INI format cannot be read; check the source profile") from None
    return parser, encoding


def prepare(endpoint: dict, config: dict, destination: Path) -> dict:
    from guardian.install.dependencies import find_vara_fm, find_vara_hf
    mode = config["vara_mode"].upper()
    select = find_vara_fm if mode == "FM" else find_vara_hf
    field = "vara_fm_path" if mode == "FM" else "vara_hf_path"
    selected = select(config[field])
    if not selected:
        raise ValueError(f"Production Guardian cannot find VARA {mode}")
    reference = Path(selected).resolve()
    source = Path(endpoint.get("vara_directory") or reference.parent).resolve()
    executable = source / reference.name
    if not executable.is_file() or sha256(executable) != sha256(reference):
        raise ValueError("LAB VARA binary differs from the executable selected by Guardian")
    ini_name = "VARAFM.ini" if mode == "FM" else "VARA.ini"
    parser, encoding = read_ini(source / ini_name)
    call = config["callsign"].upper().split("-")[0]
    licenses = [parser.get("Setup", f"Callsign Licence {i}", fallback="").upper() for i in range(4)]
    if call not in [v.split("-")[0] for v in licenses]:
        raise ValueError(f"VARA {mode} profile has no licence entry for {call}")
    slot = [v.split("-")[0] for v in licenses].index(call)
    code_key = "Registration Code" + (f" {slot}" if slot else "")
    if not parser.get("Setup", code_key, fallback="").strip():
        raise ValueError(f"VARA {mode} profile has no registration code for {call}")
    if config.get("vara_host_ptt") and parser.get("PTT", "Via", fallback="") != "3":
        raise ValueError("For Guardian host PTT, set this VARA profile to PTT Via VOX (Via=3)")
    base = int(endpoint["vara_command_port"])
    occupied = listeners()
    if any(port in occupied for port in (base, base+1, base+2)):
        raise ValueError("LAB VARA ports are already owned by another process")
    destination.mkdir(parents=True, exist_ok=False)
    for item in source.iterdir():
        if item.is_file() and item.suffix.lower() in {".exe", ".dll", ".ocx", ".ini", ".wav", ".dat"}:
            shutil.copy2(item, destination / item.name)
    parser.set("Setup", "TCP Command Port", str(base))
    parser.set("Setup", "TCP Scan Port", str(base+2))
    parser.set("Setup", "Enable KISS", "0")
    parser.set("Setup", "Updates", "0")
    with (destination / ini_name).open("w", encoding=encoding) as stream:
        parser.write(stream, space_around_delimiters=False)
    config.update(vara_host="127.0.0.1", vara_cmd_port=base, vara_data_port=base+1)
    config[f"vara_{mode.lower()}_cmd_port"] = base
    config[f"vara_{mode.lower()}_data_port"] = base+1
    config[field] = str(destination / executable.name)
    # License keys stay only in the private working copy, never the report.
    return {"mode": mode, "sha256": sha256(executable), "reference": str(reference),
            "executable": str(destination / executable.name), "ini": ini_name,
            "license_label": endpoint.get("license_label", ""), "licensed_callsign": call,
            "license_validation": "configured; vendor acceptance is not exposed by this API",
            "soundcard": {key: parser.get("Soundcard", key, fallback="") for key in (
                "Input Device Name", "Output Device Name", "ALC Drive Level", "Channel", "RA-Board Device Path")},
            "fm_mode": parser.get("Setup", "FM Mode", fallback=None),
            "ptt": {key: parser.get("PTT", key, fallback="") for key in (
                "Rig", "PTTPort", "CATPort", "Baud", "Pin", "RTS", "DTR", "Via", "Icom Address")},
            "command_port": base, "data_port": base+1}


class OwnedVara:
    def __init__(self, report: dict):
        self.report = report
        self.process = None

    def start(self, cancelled):
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        executable = Path(self.report["executable"])
        self.process = subprocess.Popen([str(executable)], cwd=executable.parent,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            startupinfo=startup)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not cancelled.is_set():
            if self.process.poll() is not None:
                raise RuntimeError("Owned VARA process exited before opening its ports")
            found = listeners()
            ports = (self.report["command_port"], self.report["data_port"])
            if all(found.get(port) == self.process.pid for port in ports):
                self.report["pid"] = self.process.pid
                return
            if any(port in found and found[port] != self.process.pid for port in ports):
                raise RuntimeError("VARA port taken by a different process")
            cancelled.wait(0.2)
        raise RuntimeError("VARA startup cancelled or timed out")

    def verify(self):
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("Owned VARA process is no longer running")
        if sha256(Path(self.report["executable"])) != self.report["sha256"]:
            raise RuntimeError("VARA executable changed during the campaign")
        found = tcp_owners(established=True)
        if any(found.get(self.report[key]) != self.process.pid
               for key in ("command_port", "data_port")):
            observed = {key: found.get(self.report[key])
                        for key in ("command_port", "data_port")}
            raise RuntimeError(f"VARA TCP ownership changed: {observed}")

    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
