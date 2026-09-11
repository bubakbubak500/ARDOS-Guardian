import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QPlainTextEdit

from guardian.qt.log_format import render_events
from guardian.qt.diagnostics_dialog import DiagnosticsDialog
from guardian.qt.runtime import ShellRuntime
from guardian.qt.shell import GuardianMainWindow
from guardian.qt.theme import ThemePreference
from guardian.services import EventBus, LogEventKind, LogLevel


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_render_events_keeps_text_searchable_and_emphasises_structured_events() -> None:
    _application()
    bus = EventBus()
    bus.publish("ordinary")
    important = bus.publish(
        "transfer completed",
        kind=LogEventKind.TRANSFER_COMPLETED,
    )
    failed = bus.publish("radio failed", LogLevel.ERROR, source="radio")

    viewer = QPlainTextEdit()
    render_events(viewer, bus.history())

    assert viewer.toPlainText().splitlines() == [
        bus.history()[0].display_text,
        "",
        important.display_text,
        "",
        failed.display_text,
    ]
    document = viewer.document()
    warning_block = document.find(important.display_text).block()
    error_block = document.find(failed.display_text).block()
    assert warning_block.blockFormat().topMargin() > 0
    assert error_block.blockFormat().topMargin() > 0
    assert error_block.charFormat().foreground().color().isValid()
    assert error_block.charFormat().foreground().color() != QPalette().color(
        QPalette.ColorRole.Text
    )


def test_home_projection_is_stable_across_noise_and_ui_rebuild(tmp_path) -> None:
    _application()
    runtime = ShellRuntime()
    settings = QSettings(
        str(tmp_path / "guardian-log.ini"),
        QSettings.Format.IniFormat,
    )
    window = GuardianMainWindow(runtime, settings)
    try:
        runtime.events.publish(
            "transfer started",
            source="session",
            kind=LogEventKind.TRANSFER_STARTED,
        )
        window._refresh()
        before_noise = window.activity.toPlainText()
        before_count = window.activity_count.text()

        for text, kind in (
            ("[VARA] PTT ON", LogEventKind.VARA_PTT),
            ("[VARA] PTT OFF", LogEventKind.VARA_PTT),
            ("[VARA] BUSY ON", LogEventKind.VARA_BUSY),
            ("[VARA] BUSY OFF", LogEventKind.VARA_BUSY),
        ):
            runtime.events.publish(text, source="vara", kind=kind)
        window._refresh()

        assert window.activity.toPlainText() == before_noise
        assert window.activity_count.text() == before_count

        window._rebuild_translated_ui()
        window._refresh()
        assert window.activity.toPlainText() == before_noise
        assert window.activity.toPlainText().count("transfer started") == 1

        full_log = window.workspace_names["log"]
        full_log.refresh()
        assert "[VARA] PTT ON" in full_log.viewer.toPlainText()
        assert "[VARA] BUSY OFF" in full_log.viewer.toPlainText()

        full_log.level.setCurrentIndex(full_log.level.findData("info"))
        full_log.search.setText("PTT ON")
        window._rebuild_translated_ui()
        rebuilt_log = window.workspace_names["log"]
        assert rebuilt_log.level.currentData() == "info"
        assert rebuilt_log.search.text() == "PTT ON"
        assert rebuilt_log.viewer.toPlainText().count("PTT ON") == 1
    finally:
        window.close()
        runtime.close()


def test_vara_client_notifications_update_ptt_and_stay_in_diagnostics(
    monkeypatch,
) -> None:
    _application()
    runtime = ShellRuntime()
    callbacks = []
    runtime.operations.vara.on_ptt = callbacks.append
    monkeypatch.setattr(
        "guardian.qt.diagnostics_dialog.audio_backend_report",
        lambda: {"backend": "test"},
    )
    diagnostics = DiagnosticsDialog(runtime)
    try:
        for text in ("PTT ON", "PTT OFF", "BUSY ON", "BUSY OFF"):
            runtime.operations.vara._handle_notification(text)

        state = runtime.operations.vara.state
        assert callbacks == [True, False]
        assert not state.ptt
        assert state.ptt_keyings == 1
        assert state.last_notification == "BUSY OFF"

        events = runtime.events.history()
        assert [event.kind for event in events[-4:]] == [
            LogEventKind.VARA_PTT,
            LogEventKind.VARA_PTT,
            LogEventKind.VARA_BUSY,
            LogEventKind.VARA_BUSY,
        ]
        report_events = diagnostics.report()["events"]
        assert [item["message"] for item in report_events[-4:]] == [
            "[VARA] PTT ON",
            "[VARA] PTT OFF",
            "[VARA] BUSY ON",
            "[VARA] BUSY OFF",
        ]
        assert [item["kind"] for item in report_events[-4:]] == [
            LogEventKind.VARA_PTT.value,
            LogEventKind.VARA_PTT.value,
            LogEventKind.VARA_BUSY.value,
            LogEventKind.VARA_BUSY.value,
        ]
    finally:
        diagnostics.close()
        runtime.close()


def test_home_log_repaints_when_theme_changes_without_new_events(tmp_path) -> None:
    _application()
    runtime = ShellRuntime()
    settings = QSettings(
        str(tmp_path / "guardian-log-theme.ini"),
        QSettings.Format.IniFormat,
    )
    window = GuardianMainWindow(runtime, settings)
    try:
        event = runtime.events.publish(
            "transfer completed",
            source="session",
            kind=LogEventKind.TRANSFER_COMPLETED,
        )
        window._refresh()
        history = runtime.events.activity_history()

        window.theme_controller.set_preference(ThemePreference.LIGHT)
        light = window.activity.document().find(event.display_text)
        light_colour = light.charFormat().foreground().color().name()
        assert light_colour == "#8a5a00"
        assert runtime.events.activity_history() == history
        assert window._activity_rendered_events == history

        window.theme_controller.set_preference(ThemePreference.DARK)
        dark = window.activity.document().find(event.display_text)
        dark_colour = dark.charFormat().foreground().color().name()
        assert dark_colour == "#d1a44b"
        assert dark_colour != light_colour
        assert runtime.events.activity_history() == history
        assert window._activity_rendered_events == history
    finally:
        window.close()
        runtime.close()
