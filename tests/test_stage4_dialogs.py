import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
)

from guardian.config import StationConfig
from guardian.i18n import tr
from guardian.install.dependencies import DependencyKind, DependencyStatus
from guardian.operations import PTT_TEST_SECONDS
from guardian.qt.diagnostics_dialog import DiagnosticsDialog
from guardian.qt.readiness_dialog import ReadinessDialog
from guardian.qt.runtime import ShellRuntime
from guardian.qt.settings_dialog import SettingsDialog
from guardian.qt.theme import ThemePreference
from guardian.vara.client import VaraState


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
        # The manual Winlink hand-off was removed in 0.6.26; VARA P2P is
        # the only transport the picker offers.
        assert dialog.payload_backend.count() == 1
        assert config.payload_backend == "vara_p2p"
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


def test_vara_probe_finds_the_client_and_never_writes_without_a_link() -> None:
    _application()
    runtime = ShellRuntime()
    diagnostics = DiagnosticsDialog(runtime)
    try:
        # The probe used to look for runtime.vara, which does not exist, so it
        # reported a connected VARA as disconnected.
        assert runtime.operations.vara is not None

        written = []
        state = VaraState(
            cmd_connected=True,
            data_connected=True,
            link_state="DISCONNECTED",
            data_peer_endpoint="127.0.0.1:8301",
        )
        runtime.operations.vara = SimpleNamespace(
            connected=True,
            state=state,
            data_socket_alive=lambda: True,
            write_data=written.append,
        )

        diagnostics._probe_vara()
        text = diagnostics.viewer.toPlainText()

        assert "není připojena" not in text
        assert "data socket  : alive" in text
        # Port 8301 only bridges during a link; a stray write would land in
        # the next real transfer.
        assert written == []
    finally:
        diagnostics.close()
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


class _FakeOperations:
    """Stands in for the live station: records what the button asked for."""

    def __init__(self, refuse: bool = False) -> None:
        self.calls: list[float] = []
        self.refuse = refuse

    def run_ptt_test(self, seconds=PTT_TEST_SECONDS, on_result=None) -> bool:
        self.calls.append(seconds)
        if self.refuse:
            if on_result is not None:
                on_result(False, "no radio control")
            return False
        if on_result is not None:
            on_result(True, "PTT test passed")
        return True


def test_ptt_test_keys_on_the_click_with_nothing_in_the_way(monkeypatch) -> None:
    # One click, one carrier: the operator asked for no confirmation step, so
    # a dialog appearing here would be the regression.
    _application()
    config = StationConfig(radio_backend="hamlib", rig_model=3073)
    operations = _FakeOperations()
    dialog = SettingsDialog(
        config, ThemePreference.SYSTEM, operations=operations
    )
    popups: list[str] = []
    for name in ("question", "information", "warning"):
        monkeypatch.setattr(
            QMessageBox, name,
            staticmethod(
                lambda *a, _name=name, **k: popups.append(_name)
                or QMessageBox.StandardButton.Yes
            ),
        )
    try:
        assert dialog.ptt_test_button.isEnabled()
        dialog.ptt_test_button.click()

        assert operations.calls == [PTT_TEST_SECONDS]
        assert popups == []
        assert dialog.ptt_status.text() == "PTT test passed"
        # The button comes back for a second attempt once the result is in.
        assert dialog.ptt_test_button.isEnabled()
    finally:
        dialog.close()


def test_ptt_test_will_not_key_settings_that_were_never_applied() -> None:
    # The live driver still holds the old port; keying it would prove nothing
    # about what is on screen. Said in the status line, not in a dialog.
    _application()
    config = StationConfig(radio_backend="hamlib", rig_model=3073, cat_port="COM7")
    operations = _FakeOperations()
    dialog = SettingsDialog(
        config, ThemePreference.SYSTEM, operations=operations
    )
    try:
        dialog.cat_port.setCurrentText("COM9")
        dialog.ptt_test_button.click()

        assert operations.calls == []
        assert dialog.ptt_status.text() == tr("settings.ptt_test_unsaved")
    finally:
        dialog.close()


def test_the_serial_port_is_picked_from_the_ports_that_exist(monkeypatch) -> None:
    # It used to be a bare text field: the operator had to remember "COM7".
    _application()
    monkeypatch.setattr(
        "guardian.qt.settings_dialog.list_serial_ports",
        lambda: ["COM3 — USB Serial CH340", "COM7 — Silicon Labs CP210x"],
    )
    config = StationConfig(radio_backend="hamlib", rig_model=3073, cat_port="COM7")
    dialog = SettingsDialog(config, ThemePreference.SYSTEM)
    try:
        assert dialog.cat_port.isEditable(), "an unplugged port is still valid"
        assert dialog.cat_port.currentText() == "COM7 — Silicon Labs CP210x"
        # The description is a label for the operator, never part of the value.
        assert dialog.selected_cat_port() == "COM7"

        dialog.cat_port.setCurrentText("COM3 — USB Serial CH340")
        assert dialog.apply()
        assert config.cat_port == "COM3"

        # A port that is not plugged in right now survives the refresh.
        dialog.cat_port.setCurrentText("COM11")
        dialog._refresh_serial_ports()
        assert dialog.selected_cat_port() == "COM11"
    finally:
        dialog.close()


def test_ptt_test_is_offered_but_disabled_without_a_live_station() -> None:
    _application()
    dialog = SettingsDialog(StationConfig(), ThemePreference.SYSTEM)
    try:
        assert dialog.ptt_test_button.text() == tr("settings.ptt_test")
        assert not dialog.ptt_test_button.isEnabled()
    finally:
        dialog.close()


def test_hamlib_ptt_wiring_is_a_setting_and_participates_in_the_unsaved_check() -> None:
    _application()
    config = StationConfig(radio_backend="hamlib", rig_model=1, cat_port="COM7")
    dialog = SettingsDialog(config, ThemePreference.SYSTEM)
    try:
        assert dialog.ptt_type.currentData() == "RIG"

        dialog.ptt_type.setCurrentIndex(dialog.ptt_type.findData("RTS"))
        # PTT test refuses this state: the live rigctld still keys the old way.
        assert dialog._radio_settings_changed()

        assert dialog.apply()
        assert config.ptt_type == "RTS"
        assert not dialog._radio_settings_changed()
    finally:
        dialog.close()
