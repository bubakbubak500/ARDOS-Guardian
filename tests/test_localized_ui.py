import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit

from guardian.config import StationConfig
from guardian.i18n import Language, set_language
from guardian.message.forms import FORMS
from guardian.qt.help_dialog import HelpDialog, help_topics
from guardian.qt.mail_workspace import ComposeDialog
from guardian.qt.runtime import ShellRuntime
from guardian.qt.settings_dialog import SettingsDialog
from guardian.qt.shell import GuardianMainWindow
from guardian.qt.theme import DARK_TOKENS, ThemePreference, stylesheet


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_dark_theme_keeps_inactive_tabs_readable() -> None:
    css = stylesheet(DARK_TOKENS)
    assert "QTabBar::tab {" in css
    assert f"color: {DARK_TOKENS.text_secondary}" in css
    assert f"background: {DARK_TOKENS.surface_2}" in css
    assert "QTabBar::tab:selected" in css
    assert f"border-bottom: 2px solid {DARK_TOKENS.accent}" in css


def test_station_settings_action_has_no_shortcut(tmp_path) -> None:
    _application()
    set_language(Language.ENGLISH)
    settings = QSettings(
        str(tmp_path / "menu.ini"),
        QSettings.Format.IniFormat,
    )
    runtime = ShellRuntime()
    window = GuardianMainWindow(runtime, settings)
    try:
        action = window.station_settings_action
        assert action.text() == "Station settings"
        assert action.shortcut().isEmpty()
    finally:
        window.close()
        runtime.close()


def test_structured_templates_have_real_fields_and_interoperable_output() -> None:
    _application()
    set_language(Language.CZECH)
    runtime = ShellRuntime()
    dialog = ComposeDialog(runtime)
    try:
        index = dialog.template.findData("ICS-213")
        dialog.template.setCurrentIndex(index)
        assert isinstance(dialog.field_widgets["subject"], QLineEdit)
        assert isinstance(dialog.field_widgets["message"], QPlainTextEdit)
        assert "PŘEDMĚT" in dialog.form_layout.labelForField(
            dialog.field_widgets["subject"]
        ).text()

        rendered = FORMS["ICS-213"].render(
            {"subject": "Test", "message": "Radio check"}
        )
        assert "SUBJECT: Test" in rendered
        assert "MESSAGE:\n  Radio check" in rendered
        assert "REPLY:" in rendered
    finally:
        dialog.close()
        runtime.close()
        set_language(Language.ENGLISH)


def test_language_setting_persists_and_shell_is_czech(tmp_path, monkeypatch) -> None:
    _application()
    settings = QSettings(
        str(tmp_path / "language.ini"),
        QSettings.Format.IniFormat,
    )
    set_language(Language.ENGLISH)
    config = StationConfig()
    monkeypatch.setattr(StationConfig, "save", lambda self: None)
    dialog = SettingsDialog(
        config,
        ThemePreference.SYSTEM,
        settings=settings,
    )
    try:
        dialog.language.setCurrentIndex(dialog.language.findData("cs"))
        assert dialog.apply()
        assert settings.value("ui/language") == "cs"
    finally:
        dialog.close()

    runtime = ShellRuntime()
    window = GuardianMainWindow(runtime, settings)
    try:
        assert [action.text() for action in window.menuBar().actions()] == [
            "&Soubor",
            "&Zobrazení",
            "&Provoz",
            "&Nastavení",
            "&Nápověda",
        ]
        assert window.metrics["inbox"].label.text() == "Doručené"
    finally:
        window.close()
        runtime.close()
        set_language(Language.ENGLISH)


def test_help_is_detailed_searchable_and_bilingual() -> None:
    _application()
    set_language(Language.ENGLISH)
    english = help_topics()
    assert len(english) >= 10
    assert sum(len(topic.html) for topic in english) > 8_000
    assert any("standardized templates" in topic.title.lower() for topic in english)

    set_language(Language.CZECH)
    czech = help_topics()
    assert czech[0].title == "1. První spuštění a bezpečný postup"
    assert "Připravenost stanice" in czech[0].html

    dialog = HelpDialog()
    try:
        dialog.search.setText("SHA-256")
        assert dialog.topics.count() >= 1
        assert any(
            "Aktualizace" in dialog.topics.item(index).text()
            for index in range(dialog.topics.count())
        )
    finally:
        dialog.close()
        set_language(Language.ENGLISH)
