import os

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
