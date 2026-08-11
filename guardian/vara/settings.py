"""Safe, narrow edits to VARA's own AES setting.

VARA exposes compression through its TCP command port, but its optional
commercial AES-256 mode is configured only in VARA.ini/VARAFM.ini.  Keep the
edit deliberately small: preserve every unknown line byte-for-byte and replace
only the two keys VARA itself writes in the ``[Setup]`` section.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


_SECTION = re.compile(r"^\s*\[([^]]+)]\s*$", re.IGNORECASE)
_ENCRYPTION = re.compile(r"^\s*Encryption\s*=", re.IGNORECASE)
_PASSWORD = re.compile(r"^\s*Password encryption\s*=", re.IGNORECASE)
_PASSWORD_VALUE = re.compile(r"^[A-Za-z0-9]{1,32}$")


@dataclass(frozen=True)
class VaraEncryptionStatus:
    enabled: bool = False
    has_password: bool = False


def ini_path_for(executable: str | Path) -> Path:
    exe = Path(executable)
    return exe.with_name(f"{exe.stem}.ini")


def read_encryption_status(executable: str | Path) -> VaraEncryptionStatus:
    path = ini_path_for(executable)
    try:
        # latin-1 is intentional: it is a one-byte round trip for VARA's
        # locale-encoded VB6 INI, including non-ASCII sound-device names.
        lines = path.read_bytes().decode("latin-1").splitlines()
    except OSError:
        return VaraEncryptionStatus()
    in_setup = False
    enabled = False
    password = ""
    for line in lines:
        section = _SECTION.match(line)
        if section:
            in_setup = section.group(1).strip().casefold() == "setup"
            continue
        if not in_setup:
            continue
        if _ENCRYPTION.match(line):
            enabled = line.partition("=")[2].strip() not in {"", "0"}
        elif _PASSWORD.match(line):
            password = line.partition("=")[2].strip()
    return VaraEncryptionStatus(enabled=enabled, has_password=bool(password))


def configure_encryption(
    executable: str | Path,
    *,
    enabled: bool,
    password: str = "",
) -> Path:
    """Write VARA's fixed-key AES switch and optional password.

    A blank password preserves the one already stored by VARA. Enabling with
    neither an existing nor a supplied password fails closed: the UI must never
    claim encryption while VARA is unable to establish an encrypted link.
    """
    path = ini_path_for(executable)
    supplied = password.strip()
    if supplied and not _PASSWORD_VALUE.fullmatch(supplied):
        raise ValueError("VARA encryption password must be 1-32 ASCII letters or digits")

    newline = "\r\n"
    try:
        raw = path.read_bytes().decode("latin-1")
        if "\r\n" not in raw:
            newline = "\n"
        lines = raw.splitlines()
    except FileNotFoundError:
        lines = []

    status = read_encryption_status(executable)
    if enabled and not supplied and not status.has_password:
        raise ValueError("VARA encryption needs a configured password")

    setup_start = None
    setup_end = len(lines)
    for index, line in enumerate(lines):
        section = _SECTION.match(line)
        if not section:
            continue
        if section.group(1).strip().casefold() == "setup":
            setup_start = index
            continue
        if setup_start is not None and index > setup_start:
            setup_end = index
            break
    if setup_start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("[Setup]")
        setup_start = len(lines) - 1
        setup_end = len(lines)

    encryption_index = None
    password_index = None
    for index in range(setup_start + 1, setup_end):
        if _ENCRYPTION.match(lines[index]):
            encryption_index = index
        elif _PASSWORD.match(lines[index]):
            password_index = index

    encryption_line = f"Encryption={1 if enabled else 0}"
    if encryption_index is None:
        lines.insert(setup_end, encryption_line)
        setup_end += 1
    else:
        lines[encryption_index] = encryption_line
    if supplied:
        password_line = f"Password encryption={supplied}"
        if password_index is None:
            lines.insert(setup_end, password_line)
        else:
            lines[password_index] = password_line

    path.write_bytes((newline.join(lines) + newline).encode("latin-1"))
    return path
