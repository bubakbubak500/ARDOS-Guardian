"""Detection and trusted-source metadata for external radio tools."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..config import StationConfig
from ..i18n import dual
from . import hamlib_installer

VARA_OFFICIAL_URL = "https://downloads.winlink.org/VARA%20Products/"


class DependencyKind(StrEnum):
    HAMLIB = "hamlib"
    VARA_FM = "vara_fm"
    VARA_HF = "vara_hf"


@dataclass(frozen=True, slots=True)
class DependencyStatus:
    kind: DependencyKind
    label: str
    available: bool
    executable: str | None
    detail: str
    official_url: str | None = None
    can_install: bool = False


def _existing_file(value: str) -> str | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return str(path.resolve()) if path.is_file() else None


def _first_existing(candidates: list[Path]) -> str | None:
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    return None


def _windows_roots() -> tuple[Path, Path, Path]:
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    local_appdata = Path(os.environ.get("LOCALAPPDATA", ""))
    system_drive = Path(os.environ.get("SystemDrive", "C:") + "\\")
    return program_files, local_appdata, system_drive


def find_vara_fm(explicit: str = "") -> str | None:
    direct = _existing_file(explicit)
    if direct:
        return direct
    on_path = shutil.which("VARAFM.exe")
    if on_path:
        return on_path
    program_files, local_appdata, system_drive = _windows_roots()
    return _first_existing(
        [
            program_files / "VARA FM" / "VARAFM.exe",
            local_appdata / "Programs" / "VARA FM" / "VARAFM.exe",
            system_drive / "VARA FM" / "VARAFM.exe",
        ]
    )


def find_vara_hf(explicit: str = "") -> str | None:
    direct = _existing_file(explicit)
    if direct:
        return direct
    on_path = shutil.which("VARA.exe")
    if on_path:
        return on_path
    program_files, local_appdata, system_drive = _windows_roots()
    return _first_existing(
        [
            program_files / "VARA" / "VARA.exe",
            program_files / "VARA HF" / "VARA.exe",
            local_appdata / "Programs" / "VARA" / "VARA.exe",
            system_drive / "VARA" / "VARA.exe",
        ]
    )


def inspect_dependencies(config: StationConfig) -> tuple[DependencyStatus, ...]:
    hamlib = hamlib_installer.existing_rigctld(config.rigctld_path)
    vara_fm = find_vara_fm(config.vara_fm_path)
    vara_hf = find_vara_hf(config.vara_hf_path)
    return (
        DependencyStatus(
            DependencyKind.HAMLIB,
            "Hamlib / rigctld",
            bool(hamlib),
            hamlib,
            hamlib or dual(
                "Not found. Guardian can install a verified portable build.",
                "Nenalezeno. Guardian může nainstalovat ověřenou přenosnou verzi.",
            ),
            can_install=True,
        ),
        DependencyStatus(
            DependencyKind.VARA_FM,
            "VARA FM",
            bool(vara_fm),
            vara_fm,
            vara_fm or dual(
                "Not found. Guardian can download the pinned official archive.",
                "Nenalezeno. Guardian může stáhnout připnutý oficiální archiv.",
            ),
            official_url=VARA_OFFICIAL_URL,
            can_install=True,
        ),
        DependencyStatus(
            DependencyKind.VARA_HF,
            "VARA HF",
            bool(vara_hf),
            vara_hf,
            vara_hf or dual(
                "Not found. Guardian can download the pinned official archive.",
                "Nenalezeno. Guardian může stáhnout připnutý oficiální archiv.",
            ),
            official_url=VARA_OFFICIAL_URL,
            can_install=True,
        ),
    )
