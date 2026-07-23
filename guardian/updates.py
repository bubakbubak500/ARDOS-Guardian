"""Verified web-manifest update checks and installer downloads."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from . import __version__
from .config import config_dir

DEFAULT_MANIFEST_URL = (
    "https://raw.githubusercontent.com/bubakbubak500/"
    "ARDOS-Guardian/main/release/release-manifest.json"
)
ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "raw.githubusercontent.com",
}
_VERSION_PART = re.compile(r"\d+")


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    version: str
    installer_url: str
    sha256: str
    notes_url: str = ""


def version_key(value: str) -> tuple[int, ...]:
    parts = tuple(int(item) for item in _VERSION_PART.findall(value))
    if not parts:
        raise UpdateError(f"Invalid version: {value!r}")
    return parts


def is_newer(candidate: str, current: str = __version__) -> bool:
    left = version_key(candidate)
    right = version_key(current)
    length = max(len(left), len(right))
    return left + (0,) * (length - len(left)) > right + (0,) * (
        length - len(right)
    )


def _require_trusted_https(url: str, *, manifest: bool = False) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise UpdateError("Update URLs must use HTTPS.")
    allowed = {"raw.githubusercontent.com"} if manifest else ALLOWED_DOWNLOAD_HOSTS
    if parsed.hostname not in allowed:
        raise UpdateError(f"Untrusted update host: {parsed.hostname or '(none)'}")


def check_for_update(
    manifest_url: str = DEFAULT_MANIFEST_URL,
    *,
    current_version: str = __version__,
    opener=urlopen,
    timeout: float = 8.0,
) -> UpdateInfo | None:
    _require_trusted_https(manifest_url, manifest=True)
    request = Request(
        manifest_url,
        headers={"User-Agent": f"ARDOS-Guardian/{current_version}"},
    )
    try:
        with opener(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateError(f"Could not read the update manifest: {exc}") from exc
    try:
        info = UpdateInfo(
            version=str(payload["version"]),
            installer_url=str(payload["installer_url"]),
            sha256=str(payload["sha256"]).lower(),
            notes_url=str(payload.get("notes_url", "")),
        )
    except (KeyError, TypeError) as exc:
        raise UpdateError("The update manifest is incomplete.") from exc
    _require_trusted_https(info.installer_url)
    if info.notes_url:
        _require_trusted_https(info.notes_url)
    if not re.fullmatch(r"[0-9a-f]{64}", info.sha256):
        raise UpdateError("The update manifest has an invalid SHA-256 value.")
    return info if is_newer(info.version, current_version) else None


def download_installer(
    info: UpdateInfo,
    *,
    destination: Path | None = None,
    opener=urlopen,
    timeout: float = 60.0,
) -> Path:
    _require_trusted_https(info.installer_url)
    target = destination or (
        config_dir() / "updates" / f"Guardian-{info.version}-setup-win-x64.exe"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    request = Request(
        info.installer_url,
        headers={"User-Agent": f"ARDOS-Guardian/{__version__}"},
    )
    digest = hashlib.sha256()
    try:
        with opener(request, timeout=timeout) as response, temporary.open("wb") as out:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                out.write(chunk)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise UpdateError(f"Could not download the installer: {exc}") from exc
    if digest.hexdigest().lower() != info.sha256.lower():
        temporary.unlink(missing_ok=True)
        raise UpdateError("Downloaded installer failed SHA-256 verification.")
    temporary.replace(target)
    return target
