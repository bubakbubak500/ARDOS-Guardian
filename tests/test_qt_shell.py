import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from guardian.qt.runtime import ShellRuntime
from guardian.services import MailboxSnapshot
from guardian.qt.shell import GuardianMainWindow
from guardian.qt.theme import DARK_TOKENS, LIGHT_TOKENS, ThemePreference


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_monitor_tokens_keep_light_and_dark_semantics_distinct() -> None:
    assert LIGHT_TOKENS.accent != DARK_TOKENS.accent
    assert LIGHT_TOKENS.application_background != DARK_TOKENS.application_background
    assert LIGHT_TOKENS.spacing_1 == DARK_TOKENS.spacing_1 == 4
    assert LIGHT_TOKENS.radius_medium == DARK_TOKENS.radius_medium == 4


def test_shell_has_native_menu_minimum_size_and_snapshot_content(tmp_path) -> None:
    _application()
    settings = QSettings(
        str(tmp_path / "guardian-shell.ini"),
        QSettings.Format.IniFormat,
    )
    settings.setValue("ui/theme", ThemePreference.LIGHT.value)
    runtime = ShellRuntime()
    # This case covers the five VARA readiness checks, independently of
    # profiles saved by earlier settings tests in the same process.
    runtime.config.payload_backend = "vara_p2p"
    window = GuardianMainWindow(runtime, settings)
    try:
        assert window.spectrum_window.parent() is None
        assert not (
            window.spectrum_window.windowFlags()
            & Qt.WindowType.WindowStaysOnTopHint
        )
        window.show_map()
        assert window.map_window.parent() is None
        assert not (
            window.map_window.windowFlags()
            & Qt.WindowType.WindowStaysOnTopHint
        )
        assert window.minimumWidth() <= 720
        assert window.minimumHeight() <= 440
        assert [action.text() for action in window.menuBar().actions()] == [
            "&File",
            "&View",
            "&Tools",
            "&Settings",
            "&Help",
        ]
        assert window.readiness.topLevelItemCount() == 5
        assert "Inbox" == window.metrics["inbox"].label.text()
        assert "operational workspace" in window.statusBar().currentMessage().lower()
    finally:
        window.close()
        runtime.close()


def test_theme_preference_is_persisted(tmp_path) -> None:
    application = _application()
    settings = QSettings(
        str(tmp_path / "guardian-theme.ini"),
        QSettings.Format.IniFormat,
    )
    runtime = ShellRuntime()
    window = GuardianMainWindow(runtime, settings)
    try:
        window.theme_controller.set_preference(ThemePreference.DARK)
        assert settings.value("ui/theme") == "dark"
        assert window.theme_controller.tokens is DARK_TOKENS
        assert application.styleSheet()
    finally:
        window.close()
        runtime.close()


def test_sc_ready_and_ardos_online_statuses(tmp_path) -> None:
    import time

    _application()
    runtime = ShellRuntime()
    runtime.config.payload_backend = "ofdm_vhf"
    runtime.config.ardos_cz_enabled = True
    runtime.ardos_cz.state = "online"
    runtime.ardos_cz.lease_deadline = time.monotonic() + 60
    settings = QSettings(str(tmp_path / "status.ini"), QSettings.Format.IniFormat)
    window = GuardianMainWindow(runtime, settings)
    try:
        window._apply_snapshot(runtime.snapshots.read())
        assert window.vara_status.property("statusRole") == "success"
        assert "SC-FTN" in window.vara_status.text()
        assert window.ardos_cz_status.property("statusRole") == "success"
        runtime.ardos_cz.lease_deadline = 0
        window._apply_snapshot(runtime.snapshots.read())
        assert window.ardos_cz_status.property("statusRole") == "inactive"
    finally:
        window.close()
        runtime.close()


def test_ardos_registration_lives_in_settings(tmp_path) -> None:
    from guardian.i18n import tr
    from guardian.qt.ardos_cz_panel import ArdosPanel
    from guardian.qt.settings_dialog import SettingsDialog

    _application()
    runtime = ShellRuntime()
    settings = QSettings(str(tmp_path / "ardos-settings.ini"), QSettings.Format.IniFormat)
    dialog = SettingsDialog(runtime.config, ThemePreference.SYSTEM,
                            settings=settings, operations=runtime.operations)
    try:
        labels = [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())]
        assert "ARDOS CZ" in labels
        ardos_tab = dialog.tabs.widget(labels.index("ARDOS CZ"))
        assert ardos_tab.findChild(ArdosPanel) is not None
        network = dialog.tabs.widget(labels.index(tr("settings.network")))
        assert network.findChild(ArdosPanel) is None
    finally:
        dialog.close()
        runtime.close()


def test_no_cat_header_shows_manual_frequency_and_qsy_defaults_to_cancel(
    tmp_path, monkeypatch
) -> None:
    _application()
    settings = QSettings(
        str(tmp_path / "guardian-no-cat.ini"),
        QSettings.Format.IniFormat,
    )
    runtime = ShellRuntime()
    runtime.config.radio_backend = "hamlib"
    runtime.config.rig_model = 1
    runtime.config.manual_frequency_hz = 145_500_000
    window = GuardianMainWindow(runtime, settings)
    asked: list[tuple[str, object]] = []

    def question(*args):
        asked.append((args[2], args[-1]))
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    try:
        window._apply_snapshot(runtime.snapshots.read())
        assert not window.manual_frequency_row.isHidden()
        assert window.manual_frequency.value() == 145_500_000
        assert not window._confirm_manual_qsy("OK2IPW", 145_550_000, "FM")
        assert "OK2IPW" in asked[0][0]
        assert "145.5500 MHz" in asked[0][0]
        assert asked[0][1] == QMessageBox.StandardButton.Cancel
    finally:
        window.close()
        runtime.close()


def test_station_context_shows_actionable_mail_state(tmp_path) -> None:
    _application()
    settings = QSettings(
        str(tmp_path / "guardian-context.ini"),
        QSettings.Format.IniFormat,
    )
    runtime = ShellRuntime()
    window = GuardianMainWindow(runtime, settings)
    runtime.snapshots.update(
        mailbox=MailboxSnapshot(inbox=2, unread=1, outbox=3, transit=1)
    )
    try:
        window._apply_snapshot(runtime.snapshots.read())
        text = window.context_activity.text()
        assert "Unread messages: 1" in text
        assert "Waiting to send: 3" in text
        assert window.context_activity.isVisibleTo(window)

        # A failed message stays in the outbox for a retry, but it is not
        # waiting to send: reporting it as pending left the line reading
        # "waiting to send: 1" forever with nothing in flight.
        runtime.snapshots.update(
            mailbox=MailboxSnapshot(inbox=2, unread=0, outbox=1, outbox_failed=1)
        )
        window._apply_snapshot(runtime.snapshots.read())
        text = window.context_activity.text()
        assert "Waiting to send" not in text
        assert "Failed, awaiting retry: 1" in text

        # Both at once stay distinguishable.
        runtime.snapshots.update(
            mailbox=MailboxSnapshot(inbox=0, unread=0, outbox=3, outbox_failed=1)
        )
        window._apply_snapshot(runtime.snapshots.read())
        text = window.context_activity.text()
        assert "Waiting to send: 2" in text
        assert "Failed, awaiting retry: 1" in text
    finally:
        window.close()
        runtime.close()


def test_spectrum_auto_opens_only_for_vara_p2p(tmp_path) -> None:
    _application()
    settings = QSettings(
        str(tmp_path / "guardian-spectrum.ini"),
        QSettings.Format.IniFormat,
    )
    runtime = ShellRuntime()
    window = GuardianMainWindow(runtime, settings)
    calls = 0

    def record_show() -> None:
        nonlocal calls
        calls += 1

    window.show_spectrum = record_show
    try:
        # The picker keeps room for a future transport; the spectrum is a
        # VARA view and must stay shut for anything that is not VARA P2P.
        runtime.config.payload_backend = "some_future_transport"
        window.show_spectrum_if_applicable()
        assert calls == 0
        runtime.config.payload_backend = "vara_p2p"
        window.show_spectrum_if_applicable()
        assert calls == 1
    finally:
        window.close()
        runtime.close()


def test_ardop_disables_vara_spectrum_and_backend_switch_restores_it(tmp_path) -> None:
    _application()
    settings = QSettings(
        str(tmp_path / "guardian-spectrum-backend.ini"), QSettings.Format.IniFormat
    )
    runtime = ShellRuntime()
    runtime.config.payload_backend = "ardop"
    window = GuardianMainWindow(runtime, settings)
    try:
        assert not window.spectrum_action.isEnabled()
        assert not window.vara_button.isEnabled()
        assert "ARDOP" in window.vara_button.toolTip()
        for backend in ("ofdm_vhf", "vara_p2p", "ardop"):
            runtime.config.payload_backend = backend
            window._apply_snapshot(runtime.snapshots.read())
            assert window.spectrum_action.isEnabled() == (backend != "ardop")
            assert window.vara_button.isEnabled() == (backend != "ardop")
            assert bool(window.vara_button.toolTip()) == (backend == "ardop")
    finally:
        window.close()
        runtime.close()


@pytest.mark.parametrize("cat_mode,expected_mode", [
    ("USB", "USB"), ("LSB", "LSB"), ("FM", "FM"), (None, "SSB"),
])
def test_ardop_ready_context_and_readiness_use_own_transport(
    tmp_path, monkeypatch, cat_mode, expected_mode
) -> None:
    from dataclasses import replace
    from guardian import i18n

    monkeypatch.setattr(i18n, "_language", i18n.Language.ENGLISH)
    _application()
    runtime = ShellRuntime()
    runtime.config.payload_backend = "ardop"
    runtime.config.vara_mode = "HF"
    runtime.snapshots.update(
        radio=replace(runtime.snapshots.read().radio, mode=cat_mode),
    )
    monkeypatch.setattr(runtime.operations, "ardop_status", lambda: None,
                        raising=False)
    settings = QSettings(str(tmp_path / "ardop-ready.ini"),
                         QSettings.Format.IniFormat)
    window = GuardianMainWindow(runtime, settings)
    try:
        assert window.vara_status.property("statusRole") == "success"
        assert "ARDOP: ready" in window.vara_status.text()
        assert expected_mode in window.context_value.text()
        assert "500 Hz" in window.context_value.text()
        assert "HF" not in window.context_value.text()
        rows = [window.readiness.topLevelItem(i)
                for i in range(window.readiness.topLevelItemCount())]
        assert all("VARA" not in item.text(0) for item in rows)
        payload_row = next(item for item in rows if "ARDOP" in item.text(1))
        assert "500 Hz" in payload_row.text(2)
        assert "USB/LSB" in payload_row.text(2)
    finally:
        window.close()
        runtime.close()


def test_ardop_active_and_idle_never_show_stale_vara_progress(
    tmp_path, monkeypatch
) -> None:
    from dataclasses import replace
    from types import SimpleNamespace
    from guardian import i18n

    monkeypatch.setattr(i18n, "_language", i18n.Language.ENGLISH)
    _application()
    runtime = ShellRuntime()
    runtime.config.payload_backend = "ardop"
    runtime.snapshots.update(vara=replace(
        runtime.snapshots.read().vara, command_connected=True,
        data_bytes_written=9000, tx_buffer_bytes=0,
        transfer_source="OLD1", transfer_destination="OLD2",
    ))
    status = [SimpleNamespace(
        state="sending", direction="send", total_bytes=400,
        progress_bytes=100, total_bytes_exact=True,
        transfer_source="OK1AAA", transfer_destination="OK2BBB",
        transfer_via="OK2BBB",
    )]
    monkeypatch.setattr(runtime.operations, "ardop_status", lambda: status[0],
                        raising=False)
    monkeypatch.setattr(runtime.operations, "payload_active", lambda: True)
    settings = QSettings(str(tmp_path / "ardop-progress.ini"),
                         QSettings.Format.IniFormat)
    window = GuardianMainWindow(runtime, settings)
    try:
        assert "ARDOP: active" in window.vara_status.text()
        assert not window.transfer_panel.isHidden()
        assert "ARDOP" in window.transfer_panel.title.text()
        assert "100 of 400 B" in window.transfer_panel.detail.text()
        assert "OLD" not in window.transfer_panel.detail.text()
        status[0] = None
        window._apply_snapshot(runtime.snapshots.read())
        assert "ARDOP: ready" in window.vara_status.text()
        assert window.transfer_panel.isHidden()

        runtime.config.payload_backend = "vara_p2p"
        window._apply_snapshot(runtime.snapshots.read())
        assert "VARA" in window.vara_status.text()
        assert not window.transfer_panel.isHidden()
        assert "VARA" in window.transfer_panel.title.text()
        assert "9000" in window.transfer_panel.detail.text()
    finally:
        window.close()
        runtime.close()


def test_second_radio_vara_button_follows_its_own_payload_backend() -> None:
    from types import SimpleNamespace
    from guardian.config import StationConfig
    from guardian.qt.second_radio_panel import SecondRadioPanel

    _application()
    snapshot = SimpleNamespace(radio=SimpleNamespace(error="", connected=False))
    radio = SimpleNamespace(
        config=StationConfig(payload_backend="ardop"),
        snapshots=SimpleNamespace(read=lambda: snapshot),
        current_frequency=lambda: 0,
        vara=SimpleNamespace(connected=False, state=SimpleNamespace(error="")),
        audio_transport=None,
        has_frequency_control=lambda: False,
        network_settings_busy=lambda: False,
    )
    runtime = SimpleNamespace(
        config=StationConfig(payload_backend="vara_p2p"),
        radio_coordinator=SimpleNamespace(radios=[object(), radio]),
    )
    panel = SecondRadioPanel(runtime)
    try:
        assert not panel.vara_button.isEnabled()
        assert "ARDOP" in panel.vara_button.toolTip()
        runtime.config.payload_backend = "ardop"
        for backend in ("ofdm_vhf", "vara_p2p", "ardop"):
            radio.config.payload_backend = backend
            panel.refresh()
            assert panel.vara_button.isEnabled() == (backend != "ardop")
            assert bool(panel.vara_button.toolTip()) == (backend == "ardop")
    finally:
        panel.close()


def test_settings_reopens_control_modem_with_unchanged_audio_endpoints(
    tmp_path, monkeypatch
) -> None:
    from types import SimpleNamespace
    from guardian.modem import make_modem
    from guardian.qt.settings_dialog import SettingsDialog

    _application()
    settings = QSettings(
        str(tmp_path / "guardian-control-settings.ini"), QSettings.Format.IniFormat
    )
    runtime = ShellRuntime()
    runtime.config.payload_backend = "vara_p2p"
    runtime.config.vara_mode = "FM"
    runtime.config.control_modem = "auto"
    restarts = []
    restart_success = [True]

    def restart():
        if not restart_success[0]:
            runtime.operations.audio_transport = None
            return False
        modem = make_modem(runtime.config.active_modem())
        if runtime.config.payload_backend == "ardop":
            modem.tx_scale = runtime.config.ardop_tx_percent / 100.0
        restarts.append(modem)
        runtime.operations.audio_transport = SimpleNamespace(
            actual_input_device_name="RX", actual_output_device_name="TX"
        )
        return True

    def settings_exec(dialog):
        runtime.operations.audio_transport = SimpleNamespace()
        endpoints = (runtime.config.audio_input, runtime.config.audio_output)
        runtime.config.payload_backend = "ardop"
        dialog.saved.emit()
        assert "verified" in dialog.audio_status.text().lower()
        assert (runtime.config.audio_input, runtime.config.audio_output) == endpoints
        assert restarts[-1].name == "ardop500"
        runtime.config.ardop_tx_percent = 65
        dialog.saved.emit()
        assert restarts[-1].tx_scale == 0.65
        runtime.config.payload_backend = "ofdm_vhf"
        dialog.saved.emit()
        assert restarts[-1].name == "afsk1200"
        runtime.config.ardop_tx_percent = 55
        dialog.saved.emit()
        assert len(restarts) == 3
        restart_success[0] = False
        runtime.config.payload_backend = "ardop"
        dialog.saved.emit()
        assert runtime.operations.audio_transport is None
        assert "control modem" in dialog.audio_status.text()
        assert "stopped" in dialog.audio_status.text()
        return 0

    monkeypatch.setattr(runtime.operations, "restart_control_channel", restart)
    monkeypatch.setattr(SettingsDialog, "exec", settings_exec)
    window = GuardianMainWindow(runtime, settings)
    try:
        window._show_settings()
    finally:
        runtime.operations.audio_transport = None
        window.close()
        runtime.close()
