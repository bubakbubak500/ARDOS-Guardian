import hashlib
import io
from dataclasses import replace
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from guardian.install.dependencies import DependencyKind
from guardian.install.vara_installer import (
    VaraPackage,
    download_and_extract,
    launch_installer,
)


class Response:
    def __init__(self, payload: bytes, url: str):
        self.payload = payload
        self.url = url
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self) -> str:
        return self.url

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            result = self.payload[self.offset :]
            self.offset = len(self.payload)
            return result
        result = self.payload[self.offset : self.offset + size]
        self.offset += len(result)
        return result


def _archive(name: str, payload: bytes = b"vendor installer") -> bytes:
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as bundle:
        bundle.writestr(name, payload)
    return output.getvalue()


def _package(payload: bytes) -> VaraPackage:
    return VaraPackage(
        kind=DependencyKind.VARA_FM,
        product="VARA FM",
        version="test",
        archive_url=(
            "https://downloads.winlink.org/VARA%20Products/test.zip"
        ),
        archive_name="test.zip",
        archive_size=len(payload),
        archive_sha256=hashlib.sha256(payload).hexdigest(),
        installer_name="VARA setup.exe",
    )


def test_vara_archive_is_verified_and_safely_extracted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    payload = _archive("VARA setup.exe")
    package = _package(payload)
    monkeypatch.setitem(
        __import__(
            "guardian.install.vara_installer",
            fromlist=["PACKAGES"],
        ).PACKAGES,
        DependencyKind.VARA_FM,
        package,
    )
    progress: list[tuple[int, int]] = []
    result = download_and_extract(
        DependencyKind.VARA_FM,
        tmp_path,
        progress=lambda received, total: progress.append((received, total)),
        opener=lambda _request, timeout: Response(payload, package.archive_url),
    )
    assert result.name == "VARA setup.exe"
    assert result.read_bytes() == b"vendor installer"
    assert progress[-1] == (len(payload), len(payload))
    assert not list(tmp_path.rglob("*.part"))


def test_vara_archive_rejects_hash_mismatch_and_unexpected_layout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    payload = _archive("unexpected.exe")
    package = _package(payload)
    monkeypatch.setitem(
        __import__(
            "guardian.install.vara_installer",
            fromlist=["PACKAGES"],
        ).PACKAGES,
        DependencyKind.VARA_FM,
        package,
    )
    with pytest.raises(ValueError, match="layout"):
        download_and_extract(
            DependencyKind.VARA_FM,
            tmp_path,
            opener=lambda _request, timeout: Response(
                payload,
                package.archive_url,
            ),
        )

    bad = replace(package, archive_sha256="0" * 64)
    monkeypatch.setitem(
        __import__(
            "guardian.install.vara_installer",
            fromlist=["PACKAGES"],
        ).PACKAGES,
        DependencyKind.VARA_FM,
        bad,
    )
    with pytest.raises(ValueError, match="SHA-256"):
        download_and_extract(
            DependencyKind.VARA_FM,
            tmp_path / "bad",
            opener=lambda _request, timeout: Response(
                payload,
                package.archive_url,
            ),
        )


def test_launch_requires_verified_executable(tmp_path: Path) -> None:
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"MZ")
    calls: list[Path] = []
    launch_installer(
        installer,
        shell_execute=lambda path: calls.append(path) or 33,
    )
    assert calls == [installer.resolve()]

    archive = tmp_path / "not-an-installer.zip"
    archive.write_bytes(b"PK")
    with pytest.raises(ValueError, match="executable"):
        launch_installer(archive)
