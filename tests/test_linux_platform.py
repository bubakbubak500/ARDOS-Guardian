"""The Linux product boundary and unchanged Windows defaults."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

import guardian.config as config_module
import guardian.operations as operations_module
import guardian.qt.settings_dialog as settings_module
from guardian.config import StationConfig
from guardian.operations import Operations
from guardian.payload.negotiated import NegotiatedPayload
from guardian.qt.settings_dialog import SettingsDialog
from guardian.qt.theme import ThemePreference


def test_linux_migrates_vara_on_both_radios(monkeypatch):
    monkeypatch.setattr(config_module, "VARA_AVAILABLE", False)
    cfg = StationConfig(payload_backend="vara_p2p", second_radio={"payload_backend": "vara_p2p"})
    assert cfg.enforce_production_policy().payload_backend == "ofdm_vhf"
    assert cfg.second_radio_config().payload_backend == "ofdm_vhf"
    cfg.payload_backend = "ardop"
    assert cfg.enforce_production_policy().payload_backend == "ardop"


def test_windows_policy_keeps_vara(monkeypatch):
    monkeypatch.setattr(config_module, "VARA_AVAILABLE", True)
    assert StationConfig(payload_backend="vara_p2p").enforce_production_policy().payload_backend == "vara_p2p"


def test_linux_never_constructs_vara_backend(monkeypatch):
    monkeypatch.setattr(operations_module, "VARA_AVAILABLE", False)
    calls = []
    primary = SimpleNamespace(name="ofdm_vhf")
    def make(name, **kwargs):
        calls.append(name)
        assert name != "vara_p2p"
        return primary
    monkeypatch.setattr(operations_module, "make_backend", make)
    controller = SimpleNamespace(config=StationConfig(payload_backend="ofdm_vhf"),
                                 _payload_dependencies=lambda: {})
    dispatcher = Operations._make_payload_backend(controller)
    assert calls == ["ofdm_vhf"]
    assert dispatcher.backend_for(SimpleNamespace(payload_transport="ofdm_vhf")) is primary
    with pytest.raises(ValueError, match="unsupported"):
        dispatcher.start_send(SimpleNamespace(payload_transport="vara_p2p"), None)
    dispatcher.cancel(SimpleNamespace(payload_transport="vara_p2p"))


def test_linux_vara_connection_never_opens_socket(monkeypatch):
    monkeypatch.setattr(operations_module, "VARA_AVAILABLE", False)
    controller = SimpleNamespace(_log=lambda *args, **kwargs: None)
    assert Operations.connect_vara(controller) is False


def test_linux_settings_grey_vara_and_ignore_its_validation(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_module, "VARA_AVAILABLE", False)
    monkeypatch.setattr(settings_module, "LINUX", True)
    monkeypatch.setattr(settings_module, "scan_audio_devices",
                        lambda **kwargs: SimpleNamespace(inputs=[], outputs=[], error=""))
    monkeypatch.setattr(settings_module, "list_serial_ports", lambda: [])
    cfg = StationConfig(payload_backend="vara_p2p")
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    dialog = SettingsDialog(cfg, ThemePreference.SYSTEM, settings=settings)
    try:
        assert dialog.payload_backend.currentData() == "ofdm_vhf"
        assert not dialog.payload_backend.model().item(0).isEnabled()
        assert dialog.payload_backend.model().item(1).isEnabled()
        assert dialog.payload_backend.model().item(2).isEnabled()
        assert not dialog.vara_mode.isEnabled()
        assert not dialog.vara_host.isEnabled()
        assert not dialog.vara_fm_path.isEnabled()
        dialog.vara_host.clear()
        dialog.vara_fm_cmd.setValue(8300)
        dialog.vara_fm_data.setValue(8300)
        assert not any("VARA" in error for error in dialog.validation_errors())
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
