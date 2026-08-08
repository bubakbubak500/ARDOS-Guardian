import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
)

from guardian.config import StationConfig
from guardian.i18n import tr
from guardian.modem.audio import AudioDeviceScan
from guardian.ofdm import MCS_TABLE
from guardian.ofdm.config import PROFILE_LADDER, profile_or_default
from guardian.install.dependencies import (
    DependencyKind,
    DependencyStatus,
    inspect_dependencies,
)
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
        # The picker offered a single transport until Guardian OFDM VHF landed.
        # VARA P2P keeps index 0 and stays what an untouched dialog saves:
        # OFDM is experimental and must never become the default by accident.
        assert dialog.payload_backend.count() == 2
        assert dialog.payload_backend.itemData(0) == "vara_p2p"
        assert dialog.payload_backend.currentData() == "vara_p2p"
        assert dialog.payload_backend.itemData(1) == "ofdm_vhf"
        assert config.payload_backend == "vara_p2p"
        assert config.audio_input == "USB Audio CODEC RX"
        assert config.audio_output == "USB Audio CODEC TX"
        assert config.radio == "Icom IC-7300"
        assert config.rig_model == 3073
    finally:
        dialog.close()


def test_network_behaviour_page_owns_the_discovery_limits_and_trust_lists() -> None:
    # The limits are station configuration, so they live beside relay and TTL
    # instead of stealing table space on the operational discovery page.
    _application()
    config = StationConfig(callsign="OK7PS")
    dialog = SettingsDialog(config, ThemePreference.SYSTEM)
    try:
        dialog.discovery_forward.setChecked(True)
        dialog.discovery_ttl.setValue(6)
        dialog.discovery_lifetime.setValue(45)
        dialog.discovery_budget.setValue(9)
        dialog.discovery_allowlist.setText("n1, n2")
        dialog.discovery_denylist.setText("bad")
        assert dialog.apply()

        assert config.discovery_forward is True
        assert config.discovery_ttl == 6
        assert config.discovery_route_lifetime == 2700
        assert config.discovery_frame_budget == 9
        assert config.discovery_allowlist == ["N1", "N2"]
        assert config.discovery_denylist == ["BAD"]
        # The mode itself stays on the Network page the operator works from.
        assert config.discovery_mode == "assisted"
    finally:
        dialog.close()


def test_radio_profile_is_saved_from_the_page_and_restored_by_the_picker(
    monkeypatch,
) -> None:
    # Swapping between a CAT radio and a handheld on an AIOC cable is nine
    # fields re-entered from memory. One name, one pick.
    _application()
    config = StationConfig(callsign="OK7PS")
    dialog = SettingsDialog(config, ThemePreference.SYSTEM)
    try:
        # Read back rather than assert a name: the audio picker resolves to
        # whatever this machine actually has, and the claim under test is that
        # a radio profile does not touch it.
        audio_before = dialog.audio_input.currentText()
        dialog.radio_backend.setCurrentIndex(
            dialog.radio_backend.findData("hamlib")
        )
        dialog.radio_model.setCurrentIndex(dialog.radio_model.findData(3085))
        dialog.cat_port.setCurrentText("COM4")
        dialog.ptt_type.setCurrentIndex(dialog.ptt_type.findData("RIG"))
        dialog.vara_ptt_delay.setValue(0)
        monkeypatch.setattr(
            "guardian.qt.settings_dialog.QInputDialog.getText",
            lambda *args, **kwargs: ("IC-705", True),
        )
        dialog._save_radio_profile()
        assert config.radio_profile_names() == ["IC-705"]

        # The handheld: different rig, different keying, a keying tail.
        dialog.radio_model.setCurrentIndex(dialog.radio_model.findData(1))
        dialog.cat_port.setCurrentText("COM9")
        dialog.ptt_type.setCurrentIndex(dialog.ptt_type.findData("RTS"))
        dialog.vara_ptt_delay.setValue(120)
        monkeypatch.setattr(
            "guardian.qt.settings_dialog.QInputDialog.getText",
            lambda *args, **kwargs: ("AIOC", True),
        )
        dialog._save_radio_profile()
        assert config.radio_profile_names() == ["AIOC", "IC-705"]

        picker = dialog.radio_profile_picker
        picker.setCurrentIndex(picker.findData("IC-705"))

        assert dialog.radio_model.currentData() == 3085
        assert dialog.selected_cat_port() == "COM4"
        assert dialog.ptt_type.currentData() == "RIG"
        assert dialog.vara_ptt_delay.value() == 0
        # Nothing reaches the station until the operator says so.
        assert config.rig_model == 0
        # And a profile is a radio, not a station.
        assert dialog.callsign.text() == "OK7PS"
        assert dialog.audio_input.currentText() == audio_before
        assert dialog.apply()
        assert (config.rig_model, config.cat_port) == (3085, "COM4")
        assert config.callsign == "OK7PS"

        picker.setCurrentIndex(picker.findData("AIOC"))
        assert dialog.vara_ptt_delay.value() == 120
        dialog._delete_radio_profile()
        assert config.radio_profile_names() == ["IC-705"]
    finally:
        dialog.close()


def test_an_unnamed_radio_profile_is_refused(monkeypatch) -> None:
    _application()
    config = StationConfig(callsign="OK7PS")
    dialog = SettingsDialog(config, ThemePreference.SYSTEM)
    try:
        monkeypatch.setattr(
            "guardian.qt.settings_dialog.QInputDialog.getText",
            lambda *args, **kwargs: ("   ", True),
        )
        dialog._save_radio_profile()
        assert config.radio_profiles == {}

        monkeypatch.setattr(
            "guardian.qt.settings_dialog.QInputDialog.getText",
            lambda *args, **kwargs: ("Cancelled", False),
        )
        dialog._save_radio_profile()
        assert config.radio_profiles == {}
    finally:
        dialog.close()


def test_clear_mail_asks_first_and_reports_what_it_deleted(monkeypatch) -> None:
    _application()
    cleared: list[int] = []
    operations = SimpleNamespace(
        mailstore=SimpleNamespace(list=lambda: [1, 2, 3]),
        clear_mailstore=lambda: cleared.append(3) or 3,
    )
    dialog = SettingsDialog(
        StationConfig(callsign="OK7PS"),
        ThemePreference.SYSTEM,
        operations=operations,
    )
    try:
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **k: QMessageBox.StandardButton.Cancel,
        )
        dialog._clear_mail()
        assert cleared == []  # Cancel means exactly nothing happens

        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **k: QMessageBox.StandardButton.Yes,
        )
        told: list[str] = []
        monkeypatch.setattr(
            QMessageBox,
            "information",
            lambda _parent, _title, text: told.append(text),
        )
        dialog._clear_mail()
        assert cleared == [3]
        assert told and "3" in told[0]
    finally:
        dialog.close()


def test_separate_working_channels_are_opt_in_and_enable_auto_qsy() -> None:
    _application()
    config = StationConfig(auto_qsy=False)
    dialog = SettingsDialog(config, ThemePreference.SYSTEM)
    try:
        assert not dialog.separate_working_channels.isChecked()
        dialog.separate_working_channels.setChecked(True)
        assert dialog.auto_qsy.isChecked()
        dialog.callsign.setText("OK7PS")
        assert dialog.apply()
        assert config.separate_working_channels is True
        assert config.auto_qsy is True
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


def test_vara_keying_delay_is_a_radio_setting_with_a_safe_default() -> None:
    _application()
    config = StationConfig(radio_backend="hamlib", rig_model=1)
    dialog = SettingsDialog(config, ThemePreference.SYSTEM)
    try:
        assert dialog.vara_ptt_delay.value() == 0, "default keeps today's timing"

        dialog.vara_ptt_delay.setValue(300)
        assert dialog.apply()
        assert config.vara_ptt_delay_ms == 300
    finally:
        dialog.close()


def test_refresh_rescans_the_hardware_but_never_under_a_live_channel(
    monkeypatch,
) -> None:
    # Re-initialising PortAudio is what makes a newly plugged codec appear --
    # and it would pull the device out from under a running control channel,
    # so the button must not do it while one is open.
    _application()
    asked: list[bool] = []
    monkeypatch.setattr(
        "guardian.qt.settings_dialog.scan_audio_devices",
        lambda *, reinitialise=False: asked.append(reinitialise)
        or AudioDeviceScan(inputs=["Mic (USB Audio CODEC)"], outputs=["Speakers"]),
    )
    operations = SimpleNamespace(audio_transport=None)
    dialog = SettingsDialog(
        StationConfig(), ThemePreference.SYSTEM, operations=operations
    )
    try:
        asked.clear()
        dialog._rescan_audio_devices()
        assert asked == [True], "idle: really look at the hardware again"

        operations.audio_transport = SimpleNamespace()
        asked.clear()
        dialog._rescan_audio_devices()
        assert asked == [False], "live channel: list only, do not re-initialise"
        assert "control channel" in dialog.audio_status.text()
    finally:
        dialog.close()


def test_an_empty_picker_states_the_reason_it_is_empty(monkeypatch) -> None:
    # "Check the interface and Windows privacy settings" sent an operator
    # hunting through Windows for what turned out to be an unreadable device.
    _application()
    monkeypatch.setattr(
        "guardian.qt.settings_dialog.scan_audio_devices",
        lambda *, reinitialise=False: AudioDeviceScan(
            error="the audio backend could not be loaded (ImportError: DLL load failed)"
        ),
    )
    dialog = SettingsDialog(StationConfig(), ThemePreference.SYSTEM)
    try:
        text = dialog.audio_status.text()
        assert "DLL load failed" in text
        assert "Diagnostics" in text or "Diagnostika" in text
    finally:
        dialog.close()


def test_diagnostics_carry_what_the_audio_backend_itself_reports() -> None:
    # Without this the only evidence of an empty picker was the operator's
    # word for it; now the report says what PortAudio saw, unfiltered.
    _application()
    runtime = ShellRuntime()
    diagnostics = DiagnosticsDialog(runtime)
    try:
        audio = diagnostics.report()["audio_backend"]
        assert audio["backend"] == "sounddevice/PortAudio"
        assert "guardian_sees" in audio
        assert "devices" in audio or "devices_error" in audio
        assert "host_apis" in audio or "host_apis_error" in audio
    finally:
        diagnostics.close()
        runtime.close()


def test_payload_picker_restores_the_saved_transport(monkeypatch) -> None:
    # The picker hard-set index 0 while there was one item, so it never read
    # config.payload_backend back: an OFDM station reopened settings, saw VARA
    # selected and would have saved VARA over its own choice.
    _application()
    # apply() persists, and the saved file is shared by every test in the
    # session; the claim here is about the object, so keep it off disk.
    monkeypatch.setattr(StationConfig, "save", lambda self: None)
    config = StationConfig(callsign="OK7PS", payload_backend="ofdm_vhf")
    dialog = SettingsDialog(config, ThemePreference.SYSTEM)
    try:
        assert dialog.payload_backend.currentData() == "ofdm_vhf"
        assert dialog.apply()
        assert config.payload_backend == "ofdm_vhf"
    finally:
        dialog.close()


def test_ofdm_knobs_are_written_back_to_the_station_config(monkeypatch) -> None:
    _application()
    monkeypatch.setattr(StationConfig, "save", lambda self: None)
    config = StationConfig(callsign="OK7PS", payload_backend="ofdm_vhf")
    dialog = SettingsDialog(config, ThemePreference.SYSTEM)
    try:
        assert dialog.ofdm_mcs.currentData() == config.ofdm_mcs
        # Read the offered list from the table rather than naming indices here:
        # the dialog must offer what the modem implements, not a copy of it.
        assert [
            dialog.ofdm_mcs.itemData(index)
            for index in range(dialog.ofdm_mcs.count())
        ] == [scheme.index for scheme in MCS_TABLE]
        assert dialog.ofdm_mcs.itemText(0) == MCS_TABLE[0].label

        dialog.ofdm_mcs.setCurrentIndex(dialog.ofdm_mcs.findData(2))
        dialog.ofdm_tx_lead.setValue(450)
        dialog.ofdm_tx_tail.setValue(150)
        dialog.ofdm_max_retries.setValue(6)
        assert dialog.apply()

        assert config.ofdm_mcs == 2
        assert config.ofdm_tx_lead_ms == 450
        assert config.ofdm_tx_tail_ms == 150
        assert config.ofdm_max_retries == 6
    finally:
        dialog.close()


def test_payload_selection_shows_only_the_rows_that_transport_uses() -> None:
    # Four TCP ports and two executable pickers on an OFDM station invite the
    # operator to maintain settings that reach nothing.
    _application()
    config = StationConfig(callsign="OK7PS", vara_mode="HF")
    dialog = SettingsDialog(config, ThemePreference.SYSTEM)
    try:
        # isHidden(), not isVisibleTo(dialog): every widget here lives on a tab
        # page, and QTabWidget hides the pages it is not showing, so
        # isVisibleTo would be False for the whole page whatever this row does.
        assert not dialog.vara_host.isHidden()
        assert not dialog.vara_fm_cmd.isHidden()
        assert not dialog.vara_hf_path.isHidden()
        assert not dialog.vara_host_ptt.isHidden()
        assert not dialog.vara_hf_bandwidth.isHidden()
        assert dialog.ofdm_mcs.isHidden()
        assert dialog.ofdm_summary.isHidden()

        dialog.payload_backend.setCurrentIndex(
            dialog.payload_backend.findData("ofdm_vhf")
        )
        assert dialog.vara_host.isHidden()
        assert dialog.vara_fm_cmd.isHidden()
        assert dialog.vara_hf_data.isHidden()
        assert dialog.vara_fm_path.isHidden()
        assert dialog.vara_host_ptt.isHidden()
        # Hidden even in HF mode here: with OFDM carrying the payload there is
        # no VARA session for a bandwidth command to reach.
        assert dialog.vara_hf_bandwidth.isHidden()
        # The caption goes with its field, or the page keeps an orphan label.
        assert dialog.vara_hf_bandwidth_label.isHidden()
        assert not dialog.ofdm_mcs.isHidden()
        assert not dialog.ofdm_tx_lead.isHidden()
        assert not dialog.ofdm_tx_tail.isHidden()
        assert not dialog.ofdm_max_retries.isHidden()
        assert not dialog.ofdm_summary.isHidden()
        # The control plane belongs to neither transport exclusively.
        assert not dialog.vara_mode.isHidden()
        assert not dialog.control_modem.isHidden()

        dialog.payload_backend.setCurrentIndex(
            dialog.payload_backend.findData("vara_p2p")
        )
        assert not dialog.vara_host.isHidden()
        assert not dialog.vara_host_ptt.isHidden()
        assert not dialog.vara_hf_bandwidth.isHidden()
        assert dialog.ofdm_mcs.isHidden()
        assert dialog.ofdm_summary.isHidden()
    finally:
        dialog.close()


def test_ofdm_summary_reports_the_resolved_profile_and_offers_no_dsp_fields() -> None:
    # The occupied RF bandwidth is still to be measured on real radios, so the
    # waveform geometry is reported, never offered as a field.
    _application()
    config = StationConfig(callsign="OK7PS", payload_backend="ofdm_vhf")
    dialog = SettingsDialog(config, ThemePreference.SYSTEM)
    try:
        waveform = profile_or_default(config.ofdm_profile)
        low, high = waveform.occupied_band
        text = dialog.ofdm_summary.text()
        assert waveform.name in text
        assert str(waveform.sample_rate) in text
        assert str(waveform.fft_size) in text
        assert str(waveform.cp_length) in text
        assert str(waveform.num_carriers) in text
        assert str(waveform.num_data_carriers) in text
        assert str(waveform.num_pilots) in text
        assert f"{low:.0f}" in text
        assert f"{high:.0f}" in text
        assert f"{waveform.occupied_bandwidth:.0f}" in text
        assert f"{waveform.symbol_duration * 1000:.1f}" in text
        assert "xperimental" in text
        # Sample rate is not bandwidth; the summary states both so the two can
        # never be read as one number.
        assert f"{waveform.occupied_bandwidth:.0f}" != str(waveform.sample_rate)

        assert dialog.ofdm_summary.objectName() == "Metadata"
        assert dialog.ofdm_summary.wordWrap()
        widgets = set(dialog.findChildren(QSpinBox)) | set(
            dialog.findChildren(QComboBox)
        )
        assert dialog.ofdm_summary not in widgets
        # The geometry is readable prose in the summary and nowhere else: no
        # row caption offers FFT size or cyclic prefix as something to change.
        captions = {
            label.text()
            for label in dialog.findChildren(QLabel)
            if label is not dialog.ofdm_summary
        }
        assert not any("FFT" in caption for caption in captions)
        assert not any("prefix" in caption.lower() for caption in captions)
    finally:
        dialog.close()


def test_the_ofdm_profile_picker_offers_the_ladder_in_bandwidth_order() -> None:
    # The occupied bandwidth a radio really passes can only be found out by
    # trying, so the profile is a choice -- offered in the order the rungs are
    # meant to be tried in, which is not the alphabetical order of their names.
    _application()
    config = StationConfig(callsign="OK7PS", payload_backend="ofdm_vhf")
    dialog = SettingsDialog(config, ThemePreference.SYSTEM)
    try:
        offered = [
            dialog.ofdm_profile.itemData(index)
            for index in range(dialog.ofdm_profile.count())
        ]
        assert offered == list(PROFILE_LADDER)
        widths = [profile_or_default(name).occupied_bandwidth for name in offered]
        assert widths == sorted(widths)
        # No rung is a proven air profile, and every label says so rather than
        # letting the default read as measured.
        labels = [
            dialog.ofdm_profile.itemText(index)
            for index in range(dialog.ofdm_profile.count())
        ]
        assert all("untried on air" in label for label in labels)
        # The one rung that needs a faster sound card names the rate where it is
        # chosen: a card that will not open at it is a failure to anticipate.
        fast = [
            name for name in offered
            if profile_or_default(name).sample_rate != 48_000
        ]
        assert fast
        for name in fast:
            label = labels[offered.index(name)]
            rate = profile_or_default(name).sample_rate
            assert f"{rate // 1000} kHz sound card" in label
    finally:
        dialog.close()


def test_every_ladder_rung_is_restored_and_written_back(monkeypatch) -> None:
    _application()
    monkeypatch.setattr(StationConfig, "save", lambda self: None)
    for name in PROFILE_LADDER:
        # Restored: a station already set to this rung must open on it.
        config = StationConfig(
            callsign="OK7PS", payload_backend="ofdm_vhf", ofdm_profile=name
        )
        dialog = SettingsDialog(config, ThemePreference.SYSTEM)
        try:
            assert dialog.ofdm_profile.currentData() == name
            assert profile_or_default(name).name in dialog.ofdm_summary.text()
        finally:
            dialog.close()

        # Written back: selecting it reaches the station profile on apply.
        config = StationConfig(callsign="OK7PS", payload_backend="ofdm_vhf")
        dialog = SettingsDialog(config, ThemePreference.SYSTEM)
        try:
            dialog.ofdm_profile.setCurrentIndex(
                dialog.ofdm_profile.findData(name)
            )
            assert dialog.apply()
            assert config.ofdm_profile == name
        finally:
            dialog.close()


def test_the_waveform_summary_follows_the_profile_picker() -> None:
    # A summary that kept describing the saved profile would contradict the
    # picker sitting directly above it, which is worse than having no summary.
    _application()
    config = StationConfig(
        callsign="OK7PS", payload_backend="ofdm_vhf", ofdm_profile="BENCH"
    )
    dialog = SettingsDialog(config, ThemePreference.SYSTEM)
    try:
        assert "BENCH" in dialog.ofdm_summary.text()
        assert "96000" not in dialog.ofdm_profile_hint.text()

        dialog.ofdm_profile.setCurrentIndex(
            dialog.ofdm_profile.findData("WIDE_20K")
        )
        wide = profile_or_default("WIDE_20K")
        text = dialog.ofdm_summary.text()
        assert "WIDE_20K" in text
        assert "BENCH" not in text
        assert str(wide.num_carriers) in text
        assert f"{wide.occupied_bandwidth:.0f}" in text
        # Still not editable, whichever rung is chosen.
        assert dialog.ofdm_summary not in set(dialog.findChildren(QComboBox))

        dialog.ofdm_profile.setCurrentIndex(
            dialog.ofdm_profile.findData("WIDE_40K")
        )
        assert "96000" in dialog.ofdm_summary.text()
        hint = dialog.ofdm_profile_hint.text()
        assert "96000" in hint
        assert "sound card" in hint
        assert dialog.ofdm_profile_hint.objectName() == "Metadata"
    finally:
        dialog.close()


def test_the_profile_picker_is_hidden_for_a_vara_station() -> None:
    # A VARA station never builds an OFDM waveform, so the choice would be a
    # setting that reaches nothing.
    _application()
    config = StationConfig(callsign="OK7PS", payload_backend="vara_p2p")
    dialog = SettingsDialog(config, ThemePreference.SYSTEM)
    try:
        assert dialog.ofdm_profile.isHidden()
        assert dialog.ofdm_profile_hint.isHidden()
        dialog.payload_backend.setCurrentIndex(
            dialog.payload_backend.findData("ofdm_vhf")
        )
        assert not dialog.ofdm_profile.isHidden()
        assert not dialog.ofdm_profile_hint.isHidden()
    finally:
        dialog.close()


def test_ofdm_station_has_no_vara_blocker_anywhere(tmp_path) -> None:
    # The condition this transport exists for: a station that never launches
    # VARA must not be held back by an executable it will never use.
    config = StationConfig(
        callsign="OK7PS",
        payload_backend="ofdm_vhf",
        rigctld_path=str(tmp_path / "missing-rigctld.exe"),
        vara_fm_path=str(tmp_path / "missing-VARAFM.exe"),
        vara_hf_path=str(tmp_path / "missing-VARA.exe"),
    )
    by_kind = {status.kind: status for status in inspect_dependencies(config)}

    # Every kind keeps a row -- "not needed" is reported, not hidden.
    assert set(by_kind) == set(DependencyKind)
    assert not by_kind[DependencyKind.VARA_FM].required
    assert not by_kind[DependencyKind.VARA_HF].required
    # Hamlib is not VARA's dependency: the OFDM modem keys the radio itself.
    assert by_kind[DependencyKind.HAMLIB].required
    # The vendor installer stays reachable for an operator who switches back.
    assert by_kind[DependencyKind.VARA_FM].can_install
    assert by_kind[DependencyKind.VARA_FM].official_url


def test_vara_station_still_requires_its_vara_executables(tmp_path) -> None:
    config = StationConfig(
        callsign="OK7PS",
        vara_fm_path=str(tmp_path / "missing-VARAFM.exe"),
        vara_hf_path=str(tmp_path / "missing-VARA.exe"),
    )
    by_kind = {status.kind: status for status in inspect_dependencies(config)}
    assert by_kind[DependencyKind.VARA_FM].required
    assert by_kind[DependencyKind.VARA_HF].required
    assert not by_kind[DependencyKind.VARA_FM].available


def test_readiness_verdict_follows_the_payload_workflow(tmp_path) -> None:
    # An OFDM station used to read "not ready" forever, because the verdict
    # indexed the VARA row for the active flavour whatever carried the payload.
    _application()
    settings = QSettings(
        str(tmp_path / "ofdm-readiness.ini"),
        QSettings.Format.IniFormat,
    )
    runtime = ShellRuntime()
    runtime.config.callsign = "OK7PS"
    runtime.config.radio_backend = "vox"
    runtime.config.payload_backend = "ofdm_vhf"
    runtime.config.vara_fm_path = str(tmp_path / "missing-VARAFM.exe")
    runtime.config.vara_hf_path = str(tmp_path / "missing-VARA.exe")
    runtime.dependency_statuses = inspect_dependencies(runtime.config)
    dialog = ReadinessDialog(runtime, settings)
    try:
        dialog._scan_pending = False
        dialog._render()
        texts = {label.text() for label in dialog.findChildren(QLabel)}
        assert dialog.summary.property("statusRole") == "success", dialog.summary.text()
        assert any("Not needed" in text for text in texts)
        assert any(
            "Not needed by the selected payload workflow" in text
            for text in texts
        )
        # The vendor download stays one click away for a later switch back.
        buttons = {button.text() for button in dialog.findChildren(QPushButton)}
        assert "Download and install…" in buttons

        # The same missing executable becomes a blocker again under VARA P2P.
        runtime.config.payload_backend = "vara_p2p"
        runtime.dependency_statuses = inspect_dependencies(runtime.config)
        dialog._render()
        assert dialog.summary.property("statusRole") == "warning"
        assert any(
            "◆ " + tr("common.missing") == label.text()
            for label in dialog.findChildren(QLabel)
        )
    finally:
        dialog.close()
        runtime.close()
