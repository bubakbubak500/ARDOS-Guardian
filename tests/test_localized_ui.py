import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPlainTextEdit

from guardian.config import StationConfig
from guardian.i18n import Language, TRANSLATIONS, set_language
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


def test_payload_page_is_bilingual_in_both_directions() -> None:
    # The page used to describe VARA only; it now hosts two data modems and the
    # experimental one has to say so in whichever language the operator reads.
    _application()
    config = StationConfig(callsign="OK7PS", payload_backend="ofdm_vhf")

    set_language(Language.ENGLISH)
    dialog = SettingsDialog(config, ThemePreference.SYSTEM)
    try:
        titles = [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())]
        assert "Payload & data modem" in titles
        assert dialog.payload_backend.itemText(0) == "Guardian VARA P2P"
        assert (
            dialog.payload_backend.itemText(1)
            == "Guardian OFDM VHF (Experimental)"
        )
        summary = dialog.ofdm_summary.text()
        assert "experimental" in summary
        assert "sample rate" in summary
        captions = {label.text() for label in dialog.findChildren(QLabel)}
        assert "OFDM modulation (MCS)" in captions
        assert "Keying lead before transmit" in captions
    finally:
        dialog.close()

    set_language(Language.CZECH)
    dialog = SettingsDialog(config, ThemePreference.SYSTEM)
    try:
        titles = [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())]
        assert "Přenos a datový modem" in titles
        assert (
            dialog.payload_backend.itemText(1)
            == "Guardian OFDM VHF (Experimentální)"
        )
        summary = dialog.ofdm_summary.text()
        assert "experimentální" in summary
        assert "vzorkování" in summary
        assert "nosných" in summary
        captions = {label.text() for label in dialog.findChildren(QLabel)}
        assert "Modulace OFDM (MCS)" in captions
        assert "Předstih klíčování" in captions
        assert "Použitý vlnový průběh" in captions
    finally:
        dialog.close()
        set_language(Language.ENGLISH)


def test_ofdm_readiness_rows_and_keys_exist_in_both_languages(tmp_path) -> None:
    _application()
    for key in (
        "ready.audio_rx",
        "ready.audio_tx",
        "ready.no_audio_device",
        "ready.audio_unresolved",
        "ready.keying",
        "ready.keying_cat",
        "ready.keying_vox",
        "ready.keying_missing",
        "ready.ofdm_profile",
        "ready.ofdm_profile_detail",
        "ready.experimental",
        "readiness.not_required",
        "settings.vara",
    ):
        assert key in TRANSLATIONS, key
        english, czech = TRANSLATIONS[key]
        assert english and czech and english != czech, key

    set_language(Language.CZECH)
    settings = QSettings(
        str(tmp_path / "ofdm-czech.ini"),
        QSettings.Format.IniFormat,
    )
    runtime = ShellRuntime()
    runtime.config.callsign = "OK7PS"
    runtime.config.payload_backend = "ofdm_vhf"
    runtime.config.radio_backend = "vox"
    runtime.config.ptt_line = "RTS"
    window = GuardianMainWindow(runtime, settings)
    try:
        window._apply_snapshot(runtime.snapshots.read())
        table = [
            (
                window.readiness.topLevelItem(index).text(0),
                window.readiness.topLevelItem(index).text(2),
            )
            for index in range(window.readiness.topLevelItemCount())
        ]
        components = [component for component, _ in table]
        assert "Zvuk z rádia (příjem)" in components
        assert "Zvuk do rádia (vysílání)" in components
        assert "Klíčování vysílače" in components
        assert "Vlnový průběh OFDM" in components
        details = [detail for _, detail in table]
        assert any("Sériová linka PTT RTS" == detail for detail in details)
        assert any("neměřený na pásmu" in detail for detail in details)
    finally:
        window.close()
        runtime.close()
        set_language(Language.ENGLISH)
