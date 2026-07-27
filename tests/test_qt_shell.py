import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

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
    window = GuardianMainWindow(runtime, settings)
    try:
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
        runtime.config.payload_backend = "winlink_manual"
        window.show_spectrum_if_applicable()
        assert calls == 0
        runtime.config.payload_backend = "vara_p2p"
        window.show_spectrum_if_applicable()
        assert calls == 1
    finally:
        window.close()
        runtime.close()
