import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from guardian import updates
from guardian.config import StationConfig
from guardian.i18n import Language, set_language
from guardian.install import dependencies, hamlib_installer
from guardian.install.dependencies import DependencyKind
from guardian.qt import readiness_dialog, update_dialog
from guardian.services import TaskResult


class Response:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        end = len(self.payload) if size < 0 else self.offset + size
        result = self.payload[self.offset:end]
        self.offset += len(result)
        return result


def opener_for(payload: bytes):
    return lambda _request, timeout: Response(payload)


def archive_info(payload: bytes = b"archive") -> updates.UpdateInfo:
    return updates.UpdateInfo(
        "1.1.25",
        "https://github.com/example/project/releases/download/v1.1.25/"
        "Guardian-1.1.25-linux-x64.tar.gz",
        hashlib.sha256(payload).hexdigest(),
    )


def test_linux_default_manifest_is_separate_from_windows():
    result = subprocess.run(
        [sys.executable, "-c",
         "from guardian import platform_support; platform_support.LINUX = True; "
         "from guardian import updates; print(updates.DEFAULT_MANIFEST_URL)"],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip().endswith("/release-manifest-linux-x64.json")


@pytest.mark.parametrize("filename", ["setup.exe", "setup.zip", "setup.exe?file=x.tar.gz"])
def test_linux_manifest_refuses_windows_and_other_update_packages(monkeypatch, filename):
    monkeypatch.setattr(updates, "LINUX", True)
    payload = json.dumps({
        "version": "1.1.25",
        "installer_url": "https://github.com/example/project/" + filename,
        "sha256": "a" * 64,
    }).encode()
    with pytest.raises(updates.UpdateError, match="tar.gz"):
        updates.check_for_update(
            current_version="1.1.24", opener=opener_for(payload)
        )


def test_linux_manifest_accepts_verified_archive_metadata(monkeypatch):
    monkeypatch.setattr(updates, "LINUX", True)
    info = archive_info()
    payload = json.dumps({
        "version": info.version,
        "installer_url": info.installer_url,
        "sha256": info.sha256,
    }).encode()
    assert updates.check_for_update(
        current_version="1.1.24", opener=opener_for(payload)
    ) == info


def test_linux_download_uses_archive_name_and_verifies_checksum(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "LINUX", True)
    monkeypatch.setattr(updates, "config_dir", lambda: tmp_path)
    payload = b"verified Linux archive bytes"
    path = updates.download_installer(archive_info(payload), opener=opener_for(payload))
    assert path == tmp_path / "updates" / "Guardian-1.1.25-linux-x64.tar.gz"
    assert path.read_bytes() == payload
    assert not path.with_suffix(".gz.part").exists()
    invalid = updates.UpdateInfo("1.1.25", archive_info().installer_url, "0" * 64)
    target = tmp_path / "invalid.tar.gz"
    with pytest.raises(updates.UpdateError, match="SHA-256"):
        updates.download_installer(invalid, destination=target, opener=opener_for(payload))
    assert not target.exists()
    assert not target.with_suffix(".gz.part").exists()


def test_linux_download_refuses_windows_before_network_or_filesystem(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "LINUX", True)
    def unexpected(*_args, **_kwargs):
        pytest.fail("Windows installer must not be downloaded on Linux")
    info = updates.UpdateInfo("1.1.25", "https://github.com/example/setup.exe", "a" * 64)
    target = tmp_path / "updates" / "setup.exe"
    with pytest.raises(updates.UpdateError, match="tar.gz"):
        updates.download_installer(info, destination=target, opener=unexpected)
    assert not target.parent.exists()


def test_linux_hamlib_detects_native_explicit_and_path_executables(monkeypatch, tmp_path):
    monkeypatch.setattr(hamlib_installer, "LINUX", True)
    native = tmp_path / "rigctld"
    native.touch()
    monkeypatch.setattr(hamlib_installer.os, "access", lambda path, mode: path == native)
    assert hamlib_installer.existing_rigctld(str(native)) == str(native.resolve())
    monkeypatch.setattr(hamlib_installer.shutil, "which", lambda name: str(native))
    assert hamlib_installer.existing_rigctld() == str(native.resolve())
    assert hamlib_installer.install(force=True) == str(native.resolve())
    windows = tmp_path / "rigctld.exe"
    windows.touch()
    monkeypatch.setattr(hamlib_installer.os, "access", lambda *_: True)
    assert hamlib_installer.existing_rigctld(str(windows)) is None
    monkeypatch.setattr(hamlib_installer.os, "access", lambda *_: False)
    assert hamlib_installer.existing_rigctld(str(native)) is None


def test_linux_hamlib_does_not_download_windows_portable_build(monkeypatch):
    monkeypatch.setattr(hamlib_installer, "LINUX", True)
    monkeypatch.setattr(hamlib_installer, "existing_rigctld", lambda *_: None)
    def unexpected():
        pytest.fail("Linux must not resolve a Windows Hamlib package")
    monkeypatch.setattr(hamlib_installer, "resolve_zip", unexpected)
    with pytest.raises(FileNotFoundError, match="libhamlib-utils"):
        hamlib_installer.install()


def test_linux_dependency_report_disables_vara_and_explains_native_hamlib(monkeypatch):
    monkeypatch.setattr(dependencies, "LINUX", True)
    monkeypatch.setattr(dependencies, "VARA_AVAILABLE", False)
    monkeypatch.setattr(hamlib_installer, "existing_rigctld", lambda *_: None)
    statuses = dependencies.inspect_dependencies(StationConfig())
    by_kind = {status.kind: status for status in statuses}
    assert "libhamlib-utils" in by_kind[DependencyKind.HAMLIB].detail
    assert not by_kind[DependencyKind.HAMLIB].can_install
    for kind in (DependencyKind.VARA_FM, DependencyKind.VARA_HF):
        status = by_kind[kind]
        assert not status.available
        assert not status.can_install
        assert status.official_url is None
        assert "SC-FTN" in status.detail and "ARDOP" in status.detail
    assert dependencies.find_vara_fm("VARAFM.exe") is None
    assert dependencies.find_vara_hf("VARA.exe") is None


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


class UpdateRuntime:
    def download_update(self, _info, completed, _progress):
        self.completed = completed
        return True

    def drain_workers(self):
        pass


def test_linux_update_dialog_opens_archive_folder_without_launching(monkeypatch, tmp_path):
    app = application()
    set_language(Language.ENGLISH)
    monkeypatch.setattr(update_dialog, "LINUX", True)
    opened = []
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(update_dialog.QDesktopServices, "openUrl", lambda url: opened.append(url))
    def unexpected(*_args):
        pytest.fail("Linux update must not execute its archive")
    monkeypatch.setattr(update_dialog.QProcess, "startDetached", unexpected)
    runtime = UpdateRuntime()
    dialog = update_dialog.UpdateDialog(runtime, archive_info())
    try:
        assert dialog.download.text() == "Download verified archive"
        dialog._download()
        path = tmp_path / "Guardian-1.1.25-linux-x64.tar.gz"
        runtime.completed(TaskResult("update-download", value=path))
        assert "Verified archive" in dialog.status.text()
        assert "Extract the archive" in dialog.status.text()
        assert len(opened) == 1
        assert Path(opened[0].toLocalFile()) == path.parent
        assert dialog.close_button.isEnabled()
    finally:
        dialog.close()
        app.processEvents()


def test_linux_readiness_has_disabled_vara_and_native_hamlib_picker(monkeypatch, tmp_path):
    app = application()
    set_language(Language.ENGLISH)
    monkeypatch.setattr(dependencies, "LINUX", True)
    monkeypatch.setattr(dependencies, "VARA_AVAILABLE", False)
    monkeypatch.setattr(readiness_dialog, "LINUX", True)
    monkeypatch.setattr(readiness_dialog, "VARA_AVAILABLE", False)
    monkeypatch.setattr(hamlib_installer, "existing_rigctld", lambda *_: None)
    config = StationConfig(payload_backend="ofdm_vhf")
    runtime = SimpleNamespace(
        config=config,
        dependency_statuses=dependencies.inspect_dependencies(config),
        request_dependency_refresh=lambda: False,
        drain_workers=lambda: None,
        workers=SimpleNamespace(is_active=lambda *_: False),
    )
    settings = QSettings(str(tmp_path / "readiness.ini"), QSettings.Format.IniFormat)
    dialog = readiness_dialog.ReadinessDialog(runtime, settings)
    dialog.timer.stop()
    try:
        dialog._render()
        assert dialog.grid.itemAtPosition(1, 3).widget().text() == "Locate…"
        assert "libhamlib-utils" in dialog.grid.itemAtPosition(1, 2).widget().text()
        for row in (2, 3):
            assert all(not dialog.grid.itemAtPosition(row, col).widget().isEnabled()
                       for col in range(4))
        pickers = []
        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            lambda *_args: (pickers.append(_args) or ("", "")))
        dialog._locate(DependencyKind.HAMLIB)
        assert "rigctld (rigctld)" in pickers[0][3]
        assert "*.exe" not in pickers[0][3]
        dialog._locate(DependencyKind.VARA_FM)
        assert len(pickers) == 1
        dialog._download_vara(DependencyKind.VARA_FM)
    finally:
        dialog.close()
        app.processEvents()
