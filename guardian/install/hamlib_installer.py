"""Download and unpack the official Hamlib portable build for Windows.

Hamlib has no winget package, but it ships a portable 64-bit zip on its GitHub
releases page. We fetch that (verifying SHA256 when published), unpack it into
%APPDATA%\\Guardian\\hamlib, and return the path to rigctld.exe. No admin
rights, no system-wide install — everything stays per-station.

Only stdlib is used so this also works inside the PyInstaller .exe.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from ..config import config_dir
from ..radio.presets import find_executable

GITHUB_API = "https://api.github.com/repos/Hamlib/Hamlib/releases/latest"
# Pinned fallback used only if the GitHub API is unreachable/rate-limited.
FALLBACK_VERSION = "4.7.1"
FALLBACK_ZIP = (
    f"https://github.com/Hamlib/Hamlib/releases/download/"
    f"{FALLBACK_VERSION}/hamlib-w64-{FALLBACK_VERSION}.zip"
)
# We will only ever download from these (official Hamlib release) hosts.
ALLOWED_HOSTS = {"github.com", "objects.githubusercontent.com", "api.github.com"}
_HEADERS = {"User-Agent": "Guardian-ARDOS/0.1 (+hamlib-installer)"}


def install_dir() -> Path:
    return config_dir() / "hamlib"


def existing_rigctld(explicit: str = "rigctld") -> str | None:
    """Return a usable rigctld path if Hamlib is already available."""
    return find_executable("rigctld", explicit)


def _check_host(url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"refusing to download from untrusted host: {host!r}")


def _open(url: str):
    _check_host(url)
    req = urllib.request.Request(url, headers=_HEADERS)
    return urllib.request.urlopen(req, timeout=30)  # noqa: S310 - host is allow-listed


def resolve_zip() -> tuple[str, str, str, str | None]:
    """Resolve the latest release artifacts.

    Returns (zip_url, zip_name, version, sha256sum_url). The last item is the
    URL of the SHA256SUM-w64 file (or None if it couldn't be located).
    """
    try:
        with _open(GITHUB_API) as resp:
            data = json.load(resp)
        version = data.get("tag_name", FALLBACK_VERSION)
        zip_url = zip_name = sums_url = None
        for asset in data.get("assets", []):
            name = asset.get("name", "")
            url = asset.get("browser_download_url", "")
            if re.fullmatch(r"hamlib-w64-.*\.zip", name):
                zip_url, zip_name = url, name
            elif name.startswith("SHA256SUM-w64") and not name.endswith(".asc"):
                sums_url = url
        if zip_url and zip_name:
            return zip_url, zip_name, version, sums_url
    except (OSError, ValueError):
        pass
    return FALLBACK_ZIP, f"hamlib-w64-{FALLBACK_VERSION}.zip", FALLBACK_VERSION, None


def _expected_sha256(sums_url: str | None, zip_name: str) -> str | None:
    """Fetch the combined SHA256SUM file and pull out the hash for our zip."""
    if not sums_url:
        return None
    try:
        with _open(sums_url) as resp:
            text = resp.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        return None
    # Lines look like:  <64-hex>  hamlib-w64-4.7.1.zip
    for line in text.splitlines():
        if zip_name in line:
            m = re.search(r"\b([0-9a-fA-F]{64})\b", line)
            if m:
                return m.group(1).lower()
    return None


def _download(url: str, dest: Path, progress=None) -> None:
    log = progress or (lambda *_: None)
    with _open(url) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        got = 0
        last_pct = -1
        with open(dest, "wb") as fh:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                fh.write(chunk)
                got += len(chunk)
                if total:
                    pct = got * 100 // total
                    if pct >= last_pct + 10:
                        last_pct = pct
                        log(f"  downloading… {pct}%  ({got // 1024} KB)")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest().lower()


def _find_under(root: Path, exe: str) -> str | None:
    for p in root.rglob(exe):
        if p.is_file():
            return str(p)
    return None


def install(progress=None, force: bool = False) -> str:
    """Install Hamlib if needed; return the path to rigctld.exe.

    `progress` is an optional callable taking a status string (for the UI log).
    """
    log = progress or (lambda *_: None)

    existing = existing_rigctld()
    if existing and not force:
        log(f"Hamlib already available: {existing}")
        return existing

    zip_url, zip_name, version, sums_url = resolve_zip()
    _check_host(zip_url)
    log(f"Installing Hamlib {version} from {urlparse(zip_url).hostname}")

    expected = _expected_sha256(sums_url, zip_name)
    tmp = Path(tempfile.gettempdir()) / zip_name
    _download(zip_url, tmp, log)

    if expected:
        actual = _sha256(tmp)
        if actual != expected:
            tmp.unlink(missing_ok=True)
            raise ValueError(
                "SHA256 mismatch — download corrupt or tampered, aborting install"
            )
        log("SHA256 verified.")
    else:
        log("No published checksum; verified transport via HTTPS from github.com.")

    dest = install_dir()
    if force and dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)

    log("Extracting…")
    with zipfile.ZipFile(tmp) as zf:
        zf.extractall(dest)
    tmp.unlink(missing_ok=True)

    rigctld = _find_under(dest, "rigctld.exe")
    if not rigctld:
        raise FileNotFoundError("rigctld.exe not found after extraction")
    log(f"Hamlib ready: {rigctld}")
    return rigctld


if __name__ == "__main__":  # `python -m guardian.install.hamlib_installer`
    install(progress=print, force="--force" in __import__("sys").argv)
