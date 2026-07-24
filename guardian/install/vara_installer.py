"""Consent-driven downloads of pinned official VARA vendor installers.

VARA is proprietary third-party software and is never bundled with Guardian.
Winlink hosts the archives maintained by the VARA author, but that directory
does not publish checksums. Guardian therefore pins the exact archive URL,
size, and SHA-256 in each Guardian release. A new VARA version must be reviewed
and deliberately added here before the application will offer it.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

from .dependencies import DependencyKind

DOWNLOAD_HOST = "downloads.winlink.org"
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_INSTALLER_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class VaraPackage:
    kind: DependencyKind
    product: str
    version: str
    archive_url: str
    archive_name: str
    archive_size: int
    archive_sha256: str
    installer_name: str

    @property
    def size_megabytes(self) -> float:
        return self.archive_size / (1024 * 1024)


PACKAGES: dict[DependencyKind, VaraPackage] = {
    DependencyKind.VARA_FM: VaraPackage(
        kind=DependencyKind.VARA_FM,
        product="VARA FM",
        version="4.4.0",
        archive_url=(
            "https://downloads.winlink.org/VARA%20Products/"
            "VARA%20FM%20v4.4.0%20setup.zip"
        ),
        archive_name="VARA FM v4.4.0 setup.zip",
        archive_size=6_318_275,
        archive_sha256=(
            "5c3ee6a6a124e25aaabf5e494b33d268"
            "d37b21e4f1462e798f271070a6bc5915"
        ),
        installer_name="VARA FM setup (Run as Administrator).exe",
    ),
    DependencyKind.VARA_HF: VaraPackage(
        kind=DependencyKind.VARA_HF,
        product="VARA HF",
        version="4.9.0",
        archive_url=(
            "https://downloads.winlink.org/VARA%20Products/"
            "VARA%20HF%20v4.9.0%20%20setup.zip"
        ),
        archive_name="VARA HF v4.9.0 setup.zip",
        archive_size=4_511_767,
        archive_sha256=(
            "5ad7d75c722e4414705dec998c28a711"
            "b7567f8568bea75cb84e8aa7c991f48a"
        ),
        installer_name="VARA setup (Run as Administrator).exe",
    ),
}


def package_for(kind: DependencyKind) -> VaraPackage:
    try:
        return PACKAGES[kind]
    except KeyError as exc:
        raise ValueError("Only VARA FM and VARA HF can be downloaded.") from exc


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != DOWNLOAD_HOST:
        raise ValueError("VARA download points outside the approved official host.")


def _open(request, *, timeout: int = 60):
    return urlopen(request, timeout=timeout)


def download_and_extract(
    kind: DependencyKind,
    destination: str | Path,
    *,
    progress: Callable[[int, int], None] | None = None,
    opener: Callable[..., object] = _open,
) -> Path:
    """Download a pinned VARA archive, verify it, and extract its one installer."""

    package = package_for(kind)
    _validate_url(package.archive_url)
    if not 0 < package.archive_size <= MAX_ARCHIVE_BYTES:
        raise ValueError("Pinned VARA archive size is outside the safety limit.")

    target_dir = Path(destination) / f"{package.product} {package.version}"
    target_dir.mkdir(parents=True, exist_ok=True)
    archive = target_dir / package.archive_name
    partial = archive.with_suffix(archive.suffix + ".part")
    request = Request(
        package.archive_url,
        headers={
            "Accept": "application/zip, application/octet-stream",
            "User-Agent": "ARDOS-Guardian",
        },
    )
    digest = hashlib.sha256()
    received = 0
    try:
        with opener(request, timeout=60) as response, partial.open("wb") as output:
            final_url = str(
                getattr(response, "geturl", lambda: package.archive_url)()
            )
            _validate_url(final_url)
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if received > package.archive_size:
                    raise ValueError(
                        "VARA archive is larger than the pinned release metadata."
                    )
                output.write(chunk)
                digest.update(chunk)
                if progress is not None:
                    progress(received, package.archive_size)
        if received != package.archive_size:
            raise ValueError(
                "VARA archive size does not match the pinned release metadata."
            )
        if digest.hexdigest().lower() != package.archive_sha256:
            raise ValueError("Downloaded VARA archive failed SHA-256 verification.")
        partial.replace(archive)
    except Exception:
        partial.unlink(missing_ok=True)
        raise

    installer = target_dir / package.installer_name
    installer_partial = installer.with_suffix(installer.suffix + ".part")
    try:
        with ZipFile(archive) as bundle:
            entries = [item for item in bundle.infolist() if not item.is_dir()]
            if (
                len(entries) != 1
                or entries[0].filename != package.installer_name
                or not 0 < entries[0].file_size <= MAX_INSTALLER_BYTES
            ):
                raise ValueError(
                    "Verified VARA archive has an unexpected installer layout."
                )
            with bundle.open(entries[0]) as source, installer_partial.open(
                "wb"
            ) as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
        installer_partial.replace(installer)
    except (BadZipFile, OSError, ValueError):
        installer_partial.unlink(missing_ok=True)
        raise
    return installer


def _windows_shell_execute(path: Path) -> int:
    if os.name != "nt":
        raise OSError("VARA installers can only be launched on Windows.")
    execute = ctypes.windll.shell32.ShellExecuteW
    execute.restype = ctypes.c_void_p
    result = execute(None, "open", str(path), None, str(path.parent), 1)
    return int(result or 0)


def launch_installer(
    path: str | Path,
    *,
    shell_execute: Callable[[Path], int] = _windows_shell_execute,
) -> None:
    """Launch an already verified vendor installer through the Windows shell."""

    resolved = Path(path).resolve(strict=True)
    if resolved.suffix.lower() != ".exe":
        raise ValueError("Only a verified Windows executable can be launched.")
    result = shell_execute(resolved)
    if result <= 32:
        raise OSError(f"Windows ShellExecute failed with code {result}.")
