import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QSpinBox

from guardian.config import StationConfig
from guardian.install.dependencies import DependencyKind, DependencyStatus
from guardian.qt.diagnostics_dialog import DiagnosticsDialog
from guardian.qt.readiness_dialog import ReadinessDialog
from guardian.qt.runtime import ShellRuntime
from guardian.qt.settings_dialog import SettingsDialog
from guardian.qt.theme import ThemePreference


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_settings_validate_and_apply_grouped_station_profile() -> None:
    _application()
    config = StationConfig()
    dialog = SettingsDialog(config, ThemePreference.SYSTEM)
    try:
        dialog.callsign.setText("x")
        assert not dialog.apply()
        assert dialog.error.isVisibleTo(dialog)

        dialog.callsign.setText("OK7PS")
        dialog.operator_name.setText("Operator")
        dialog.vara_mode.setCurrentText("HF")
        dialog.payload_backend.setCurrentIndex(
            dialog.payload_backend.findData("winlink_manual")
        )
        dialog.audio_input.setCurrentText("USB Audio CODEC RX")
        dialog.audio_output.setCurrentText("USB Audio CODEC TX")
        dialog.radio_backend.setCurrentIndex(
            dialog.radio_backend.findData("hamlib")
        )
        dialog.radio_model.setCurrentIndex(
            dialog.radio_model.findData(3073)
        )
        assert dialog.apply()
        assert config.callsign == "OK7PS"
        assert config.operator_name == "Operator"
        assert config.vara_mode == "HF"
        assert config.vara_cmd_port == config.vara_hf_cmd_port
        assert config.payload_backend == "winlink_manual"
        assert config.audio_input == "USB Audio CODEC RX"
        assert config.audio_output == "USB Audio CODEC TX"
        assert config.radio == "Icom IC-7300"
        assert config.rig_model == 3073
    finally:
        dialog.close()


def test_settings_radio_model_is_selected_by_name_not_typed_as_id() -> None:
    _application()
    config = StationConfig(
        radio_backend="hamlib",
        radio="Yaesu FT-891",
        rig_model=1036,
    )
    dialog = SettingsDialog(config, ThemePreference.SYSTEM)
    try:
        assert not dialog.radio_model.isEditable()
        assert dialog.radio_model.currentText() == "Yaesu FT-891"
        assert dialog.radio_model.currentData() == 1036
        labels = {
            label.text()
            for label in dialog.findChildren(QLabel)
        }
        assert "Hamlib model ID" not in labels
        assert "ID modelu Hamlib" not in labels
        assert all(spin.maximum() != 999_999 for spin in dialog.findChildren(QSpinBox))
    finally:
        dialog.close()


def test_readiness_and_diagnostics_are_non_transmitting(tmp_path) -> None:
    _application()
    settings = QSettings(
        str(tmp_path / "readiness.ini"),
        QSettings.Format.IniFormat,
    )
    runtime = ShellRuntime()
    readiness = ReadinessDialog(runtime, settings)
    diagnostics = DiagnosticsDialog(runtime)
    try:
        report = diagnostics.report()
        assert report["guardian_version"]
        assert "configuration" in report
        assert "snapshot" in report
        assert "message bodies" not in diagnostics.viewer.toPlainText().lower()

        readiness._finish()
        assert settings.value("onboarding/completed", type=bool)
    finally:
        diagnostics.close()
        readiness.close()
        runtime.close()


def test_readiness_offers_direct_vara_downloads(tmp_path) -> None:
    _application()
    settings = QSettings(
        str(tmp_path / "vara-readiness.ini"),
        QSettings.Format.IniFormat,
    )
    runtime = ShellRuntime()
    runtime.dependency_statuses = (
        DependencyStatus(
            DependencyKind.HAMLIB,
            "Hamlib / rigctld",
            True,
            "rigctld.exe",
            "rigctld.exe",
        ),
        DependencyStatus(
            DependencyKind.VARA_FM,
            "VARA FM",
            False,
            None,
            "missing",
            "https://downloads.winlink.org/VARA%20Products/",
            True,
        ),
        DependencyStatus(
            DependencyKind.VARA_HF,
            "VARA HF",
            False,
            None,
            "missing",
            "https://downloads.winlink.org/VARA%20Products/",
            True,
        ),
    )
    readiness = ReadinessDialog(runtime, settings)
    try:
        readiness._scan_pending = False
        readiness._render()
        labels = {
            button.text()
            for button in readiness.findChildren(QPushButton)
        }
        assert "Download and install…" in labels
        assert "Official page" in labels
    finally:
        readiness.close()
        runtime.close()
