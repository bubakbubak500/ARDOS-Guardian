import hashlib
import json

import pytest

from guardian.updates import (
    DEFAULT_MANIFEST_URL,
    UpdateError,
    UpdateInfo,
    check_for_update,
    download_installer,
    is_newer,
)


class Response:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            result = self.payload[self.offset :]
            self.offset = len(self.payload)
            return result
        result = self.payload[self.offset : self.offset + size]
        self.offset += len(result)
        return result


def opener_for(payload: bytes):
    return lambda _request, timeout: Response(payload)


def test_version_comparison_handles_different_component_counts() -> None:
    assert DEFAULT_MANIFEST_URL.endswith(
        "/releases/latest/download/release-manifest.json"
    )
    assert is_newer("0.2.0", "0.1.9")
    assert is_newer("1.0", "0.9.99")
    assert not is_newer("0.1", "0.1.0")


def test_manifest_requires_trusted_https_and_reports_new_version() -> None:
    payload = b"\xef\xbb\xbf" + json.dumps(
        {
            "version": "0.2.0",
            "installer_url": (
                "https://github.com/example/project/releases/download/"
                "v0.2.0/setup.exe"
            ),
            "sha256": "a" * 64,
            "notes_url": "https://github.com/example/project/releases/tag/v0.2.0",
        }
    ).encode()
    result = check_for_update(
        "https://raw.githubusercontent.com/example/project/main/manifest.json",
        current_version="0.1.0",
        opener=opener_for(payload),
    )
    assert result is not None
    assert result.version == "0.2.0"

    with pytest.raises(UpdateError, match="HTTPS"):
        check_for_update("http://raw.githubusercontent.com/a/b/main/x.json")

    with pytest.raises(UpdateError, match="large"):
        check_for_update(
            "https://github.com/example/project/releases/latest/download/"
            "release-manifest.json",
            opener=opener_for(b"{" + b" " * 1_000_000),
        )


def test_download_is_published_only_after_sha256_verification(tmp_path) -> None:
    payload = b"verified installer bytes"
    target = tmp_path / "Guardian-0.2.0.exe"
    info = UpdateInfo(
        "0.2.0",
        "https://github.com/example/project/releases/download/v0.2.0/setup.exe",
        hashlib.sha256(payload).hexdigest(),
    )
    result = download_installer(
        info,
        destination=target,
        opener=opener_for(payload),
    )
    assert result == target
    assert target.read_bytes() == payload
    assert not target.with_suffix(".exe.part").exists()

    invalid = UpdateInfo(info.version, info.installer_url, "0" * 64)
    with pytest.raises(UpdateError, match="SHA-256"):
        download_installer(
            invalid,
            destination=tmp_path / "invalid.exe",
            opener=opener_for(payload),
        )
    assert not (tmp_path / "invalid.exe").exists()
