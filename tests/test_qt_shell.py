import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from guardian.i18n import Language, set_language
from guardian.modem.recorder import RecordingSummary
from guardian.ofdm.config import profile_or_default
from guardian.operations import StationLabStatus
from guardian.qt.runtime import ShellRuntime
from guardian.services import MailboxSnapshot
from guardian.qt.shell import GuardianMainWindow
from guardian.qt.theme import DARK_TOKENS, LIGHT_TOKENS, ThemePreference
from guardian.station_lab import CalibrationState


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
    # This test asserts the five-row VARA layout. Do not let configuration
    # state left by another UI test turn it into the eight-row OFDM layout.
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
        assert window.minimumWidth() == 1180
        assert window.minimumHeight() == 720
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


def test_incoming_autotune_offer_interrupts_any_workspace_and_can_be_accepted(
    tmp_path, monkeypatch
) -> None:
    _application()
    settings = QSettings(
        str(tmp_path / "guardian-autotune-offer.ini"),
        QSettings.Format.IniFormat,
    )
    runtime = ShellRuntime()
    window = GuardianMainWindow(runtime, settings)
    asked = []

    def question(*args):
        asked.append(args)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    try:
        window._show_workspace("home")
        runtime.operations.station_lab = StationLabStatus(
            state=CalibrationState.WAITING_APPROVAL.value,
            peer="OK1AAA",
            session_id=22,
            pending_offer=True,
            message="incoming test",
        )

        window._poll_station_lab_offer()

        assert len(asked) == 1
        assert "OK1AAA" in asked[0][2]
        assert window.workspace_stack.currentWidget() is window.workspace_names["station_lab"]
        assert runtime.operations.station_lab.state == CalibrationState.PREPARING.value
        assert not runtime.operations.station_lab.pending_offer
        window._poll_station_lab_offer()
        assert len(asked) == 1
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
    runtime.snapshots.update(
        mailbox=MailboxSnapshot(inbox=2, unread=1, outbox=3, transit=1)
    )
    window = GuardianMainWindow(runtime, settings)
    try:
        window._refresh()
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
        window._refresh()
        text = window.context_activity.text()
        assert "Waiting to send" not in text
        assert "Failed, awaiting retry: 1" in text

        # Both at once stay distinguishable.
        runtime.snapshots.update(
            mailbox=MailboxSnapshot(inbox=0, unread=0, outbox=3, outbox_failed=1)
        )
        window._refresh()
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


def test_spectrum_stays_shut_for_the_ofdm_transport(tmp_path) -> None:
    # The sentinel above proves unknown transports are refused; this names the
    # real second transport, so the day OFDM grows a view of its own it is a
    # decision someone makes here rather than a silent side effect.
    _application()
    settings = QSettings(
        str(tmp_path / "guardian-spectrum-ofdm.ini"),
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
        runtime.config.payload_backend = "ofdm_vhf"
        window.show_spectrum_if_applicable()
        assert calls == 0
    finally:
        window.close()
        runtime.close()


def test_station_header_names_the_transport_it_is_actually_using(tmp_path) -> None:
    # The fall-through label was the literal "Winlink", left from the hand-off
    # 0.6.26 removed: an OFDM station announced a workflow it does not have.
    _application()
    settings = QSettings(
        str(tmp_path / "guardian-header.ini"),
        QSettings.Format.IniFormat,
    )
    runtime = ShellRuntime()
    runtime.config.callsign = "OK7PS"
    # Stated, not assumed: the runtime loads the config the session has been
    # writing, so a neighbouring test's choice would otherwise decide this one.
    runtime.config.payload_backend = "vara_p2p"
    window = GuardianMainWindow(runtime, settings)
    try:
        window._apply_snapshot(runtime.snapshots.read())
        assert "VARA P2P" in window.context_value.text()

        runtime.config.payload_backend = "ofdm_vhf"
        window._apply_snapshot(runtime.snapshots.read())
        text = window.context_value.text()
        assert "OFDM VHF" in text
        assert "Winlink" not in text
        assert "OK7PS" in text
    finally:
        window.close()
        runtime.close()


def test_home_readiness_rows_report_what_the_transport_depends_on(
    tmp_path, monkeypatch
) -> None:
    _application()
    settings = QSettings(
        str(tmp_path / "guardian-ready-rows.ini"),
        QSettings.Format.IniFormat,
    )
    runtime = ShellRuntime()
    runtime.config.callsign = "OK7PS"
    runtime.config.vara_mode = "FM"
    runtime.config.payload_backend = "vara_p2p"
    runtime.config.radio_backend = "none"
    window = GuardianMainWindow(runtime, settings)

    def rows() -> list[tuple[str, str, str]]:
        return [
            tuple(
                window.readiness.topLevelItem(index).text(column)
                for column in range(3)
            )
            for index in range(window.readiness.topLevelItemCount())
        ]

    try:
        window._apply_snapshot(runtime.snapshots.read())
        vara_rows = rows()
        # Regression-pinned: a VARA station's table is exactly what it was.
        assert len(vara_rows) == 5
        assert vara_rows[3][0] == "VARA FM"
        assert vara_rows[3][2] == (
            f"{runtime.config.vara_host}:{runtime.config.vara_cmd_port}"
        )

        # An OFDM station: the modem is Guardian's own, so the TCP endpoint is
        # replaced by the two audio devices, keying and the waveform profile.
        runtime.config.payload_backend = "ofdm_vhf"
        runtime.config.radio_backend = "hamlib"
        runtime.config.ptt_type = "RTS"
        runtime.config.audio_input = "USB Audio CODEC RX"
        runtime.config.audio_output = "USB Audio CODEC TX"
        monkeypatch.setattr(
            "guardian.qt.shell.resolve_device",
            lambda name, kind: 4 if kind == "input" else name,
        )
        window._apply_snapshot(runtime.snapshots.read())
        ofdm_rows = rows()
        components = [row[0] for row in ofdm_rows]
        assert not any("VARA" in component for component in components)
        assert components[3:] == [
            "Radio audio in (RX)",
            "Radio audio out (TX)",
            "Transmit keying",
            "OFDM waveform",
            "Payload workflow",
        ]
        # An int from resolve_device means Guardian can open the stream; the
        # name coming back means it could not be found on this computer.
        assert ofdm_rows[3][1] == "Available"
        assert ofdm_rows[4][1] == "Missing"
        assert "USB Audio CODEC TX" in ofdm_rows[4][2]
        assert ofdm_rows[5][1] == "Configured"
        assert "RTS" in ofdm_rows[5][2]
        waveform = profile_or_default(runtime.config.ofdm_profile)
        assert ofdm_rows[6][1] == "Experimental"
        assert waveform.name in ofdm_rows[6][2]
        assert f"{waveform.occupied_bandwidth:.0f}" in ofdm_rows[6][2]
        assert "OFDM VHF" in ofdm_rows[7][1]

        # Nothing selected is a different failure from nothing found.
        runtime.config.audio_input = ""
        window._apply_snapshot(runtime.snapshots.read())
        assert rows()[3][1] == "Not configured"
    finally:
        window.close()
        runtime.close()


def _fake_recorder(runtime, monkeypatch, summary=None):
    """Drive the shell's recording controls without touching PortAudio.

    Everything the shell shows is read back from `Operations`, so replacing those
    six methods is enough to exercise the whole control -- and it is the only way
    to test the failed-start path, which on real hardware needs a missing device.
    """
    state = {"active": False, "started": 0, "stopped": 0,
             "seconds": 0.0, "level": 0.0, "path": summary.path if summary else None}

    def start(*, sample_rate=None, exclusive=False):
        state["started"] += 1
        state["sample_rate"] = sample_rate
        state["exclusive"] = exclusive
        state["active"] = summary is not None
        return summary.path if summary is not None else None

    def stop():
        state["stopped"] += 1
        state["active"] = False
        return summary

    monkeypatch.setattr(runtime.operations, "recording_active",
                        lambda: state["active"])
    monkeypatch.setattr(runtime.operations, "start_recording", start)
    monkeypatch.setattr(runtime.operations, "stop_recording", stop)
    monkeypatch.setattr(runtime.operations, "recording_seconds",
                        lambda: state["seconds"])
    monkeypatch.setattr(runtime.operations, "recording_level",
                        lambda: state["level"])
    monkeypatch.setattr(runtime.operations, "recording_path",
                        lambda: state["path"])
    return state


def test_the_recording_control_lives_in_modem_files(
    tmp_path, monkeypatch
) -> None:
    _application()
    settings = QSettings(
        str(tmp_path / "guardian-record.ini"),
        QSettings.Format.IniFormat,
    )
    runtime = ShellRuntime()
    window = GuardianMainWindow(runtime, settings)
    summary = RecordingSummary(
        path=tmp_path / "captures" / "capture-20260808-101500.wav",
        sample_rate=48_000,
        samples=48_000,
        rms=0.05,
        peak=0.4,
        clipped_samples=0,
    )
    state = _fake_recorder(runtime, monkeypatch, summary)
    try:
        workspace = window.workspace_names["modem"]
        assert not hasattr(window, "record_button")
        assert not hasattr(window, "record_action")
        assert workspace.tabs.count() == 1
        assert workspace.tabs.tabText(0) == "Files"
        assert workspace.record_button.text() == "Record received audio"
        workspace.record_button.click()
        assert state["started"] == 1
        assert state["sample_rate"] == workspace.selected_profile().sample_rate
        assert state["exclusive"]
        assert workspace.record_button.text() == "Stop recording"
        workspace.record_button.click()
        assert state["stopped"] == 1
        assert workspace.record_button.text() == "Record received audio"
        assert workspace._running == "modem-decode"
    finally:
        window.close()
        runtime.close()


def test_a_recording_that_will_not_start_never_claims_to_be_running(
    tmp_path, monkeypatch
) -> None:
    # `start_recording` returns None for a missing RX device, a payload transfer
    # holding the codec, or a PortAudio refusal -- and has already logged which.
    # The one unacceptable outcome is a shell that says it is recording anyway.
    _application()
    settings = QSettings(
        str(tmp_path / "guardian-record-failed.ini"),
        QSettings.Format.IniFormat,
    )
    runtime = ShellRuntime()
    window = GuardianMainWindow(runtime, settings)
    state = _fake_recorder(runtime, monkeypatch, summary=None)
    try:
        workspace = window.workspace_names["modem"]
        workspace.record_button.click()
        assert state["started"] == 1
        assert not state["active"]
        assert workspace.record_button.text() == "Record received audio"
        assert workspace.recording_indicator.property("statusRole") == "inactive"
        assert "could not start" in workspace.capture_status.text()
        assert getattr(window, "capture_dialog", None) is None
        # The poll must not talk it back into a recording state either.
        workspace.refresh()
        assert workspace.record_button.text() == "Record received audio"
    finally:
        window.close()
        runtime.close()


def test_the_live_indicator_shows_elapsed_time_and_the_peak_level_so_far(
    tmp_path, monkeypatch
) -> None:
    # The level is the point of the indicator: an operator who sees a silent or
    # clipped capture at the radio does not have to ask for the session again.
    _application()
    settings = QSettings(
        str(tmp_path / "guardian-record-live.ini"),
        QSettings.Format.IniFormat,
    )
    runtime = ShellRuntime()
    window = GuardianMainWindow(runtime, settings)
    summary = RecordingSummary(
        path=tmp_path / "capture.wav",
        sample_rate=48_000,
        samples=1,
        rms=0.1,
        peak=0.1,
        clipped_samples=0,
    )
    state = _fake_recorder(runtime, monkeypatch, summary)
    try:
        workspace = window.workspace_names["modem"]
        workspace.record_button.click()
        state["seconds"], state["level"] = 12.5, 0.35
        workspace.refresh()
        text = workspace.recording_indicator.text()
        assert "12.5 s" in text
        assert "-9 dBFS" in text
        assert workspace.recording_indicator.property("statusRole") == "success"

        # At full scale the peaks are being flattened; say so, in danger colour.
        state["seconds"], state["level"] = 20.0, 1.0
        workspace.refresh()
        text = workspace.recording_indicator.text()
        assert "20.0 s" in text
        assert "clipping" in text
        assert workspace.recording_indicator.property("statusRole") == "danger"

        # Below -60 dBFS nothing is connected, whatever the elapsed time says.
        state["level"] = 0.0005
        workspace.refresh()
        assert "silent" in workspace.recording_indicator.text()
        assert workspace.recording_indicator.property("statusRole") == "warning"

        # No sample at all is not a measurement of zero.
        state["level"] = 0.0
        workspace.refresh()
        assert "unavailable" in workspace.recording_indicator.text()
        assert "0 dBFS" not in workspace.recording_indicator.text()
    finally:
        window.close()
        runtime.close()


def test_the_recording_control_reads_in_czech_too(tmp_path, monkeypatch) -> None:
    _application()
    settings = QSettings(
        str(tmp_path / "guardian-record-czech.ini"),
        QSettings.Format.IniFormat,
    )
    set_language(Language.CZECH)
    runtime = ShellRuntime()
    window = GuardianMainWindow(runtime, settings)
    summary = RecordingSummary(
        path=tmp_path / "capture.wav",
        sample_rate=48_000,
        samples=48_000,
        rms=0.05,
        peak=0.4,
        clipped_samples=0,
    )
    state = _fake_recorder(runtime, monkeypatch, summary)
    try:
        workspace = window.workspace_names["modem"]
        assert workspace.record_button.text() == "Nahrávat přijímaný zvuk"
        workspace.record_button.click()
        assert workspace.record_button.text() == "Ukončit nahrávání"
        state["seconds"], state["level"] = 4.0, 1.0
        workspace.refresh()
        assert "Nahrávám 4.0 s" in workspace.recording_indicator.text()
        assert "přebuzeno" in workspace.recording_indicator.text()
    finally:
        window.close()
        runtime.close()
        set_language(Language.ENGLISH)


def test_closing_the_shell_closes_an_open_capture_file(
    tmp_path, monkeypatch
) -> None:
    # A WAV header is only written when the recorder is stopped, so a capture
    # left running through shutdown would be a file no tool can open.
    _application()
    settings = QSettings(
        str(tmp_path / "guardian-record-close.ini"),
        QSettings.Format.IniFormat,
    )
    runtime = ShellRuntime()
    window = GuardianMainWindow(runtime, settings)
    summary = RecordingSummary(
        path=tmp_path / "capture.wav",
        sample_rate=48_000,
        samples=48_000,
        rms=0.05,
        peak=0.4,
        clipped_samples=0,
    )
    state = _fake_recorder(runtime, monkeypatch, summary)
    try:
        window.workspace_names["modem"].record_button.click()
        assert state["active"]
        window.close()
        assert state["stopped"] == 1
        assert not state["active"]
    finally:
        runtime.close()


def test_the_audio_rows_do_not_enumerate_portaudio_on_every_tick(
    tmp_path, monkeypatch
) -> None:
    # The table is rebuilt twice a second and resolve_device walks the whole
    # PortAudio device list; doing that per tick on the UI thread is a stall.
    _application()
    settings = QSettings(
        str(tmp_path / "guardian-audio-probe.ini"),
        QSettings.Format.IniFormat,
    )
    runtime = ShellRuntime()
    runtime.config.callsign = "OK7PS"
    runtime.config.payload_backend = "ofdm_vhf"
    runtime.config.audio_input = "RX device"
    runtime.config.audio_output = "TX device"
    asked: list[tuple[str, str]] = []

    def counting_resolve(name, kind):
        asked.append((name, kind))
        return 7

    # Patched before the window exists: building it refreshes once already.
    monkeypatch.setattr("guardian.qt.shell.resolve_device", counting_resolve)
    window = GuardianMainWindow(runtime, settings)
    try:
        for _ in range(8):
            window._apply_snapshot(runtime.snapshots.read())
        assert asked == [("RX device", "input"), ("TX device", "output")]
    finally:
        window.close()
        runtime.close()
