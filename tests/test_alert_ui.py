import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from guardian.i18n import Language, set_language, tr
from guardian.operations import AlertRecord
from guardian.protocol import ALERTS, Priority
from guardian.qt.alerts import AlertBanner, AlertDialog
from guardian.qt.runtime import ShellRuntime
from guardian.qt.shell import GuardianMainWindow


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _record(code: int = 0x01, note: str = "", source: str = "OK2IPW", **kw):
    return AlertRecord(
        code=code,
        note=note,
        source=source,
        received=kw.pop("received", time.time()),
        mine=kw.pop("mine", False),
    )


def test_banner_sits_between_the_station_context_and_the_counters(tmp_path) -> None:
    # The operator asked for the alert exactly here: under the station
    # context bar, above the mailbox counters.
    _application()
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    window = GuardianMainWindow(ShellRuntime(), settings)
    try:
        layout = window.centralWidget().layout()
        order = [
            layout.itemAt(index).widget().objectName()
            for index in range(layout.count())
            if layout.itemAt(index).widget() is not None
        ]
        assert order.index("OperationalHeader") + 1 == order.index("AlertBanner")
        assert order.index("AlertBanner") + 1 == order.index("MetricStrip")
    finally:
        window.close()


def test_shell_refresh_pushes_a_new_alert_into_the_banner(tmp_path) -> None:
    # The banner is only useful if the ordinary UI poll feeds it -- nothing
    # else calls show_latest on a running station.
    _application()
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    runtime = ShellRuntime()
    window = GuardianMainWindow(runtime, settings)
    try:
        window._apply_snapshot(runtime.snapshots.read())
        assert not window.alert_banner.isVisible()
        runtime.operations.alerts.insert(0, _record(0x03, "OBEC HORNI"))
        window._apply_snapshot(runtime.snapshots.read())
        assert window.alert_banner.isVisibleTo(window)
        assert window.alert_banner.headline.text() == tr("alert.evacuation")
    finally:
        window.close()


def test_banner_stays_hidden_until_an_alert_arrives_and_after_dismissal() -> None:
    _application()
    banner = AlertBanner()
    banner.show_latest([])
    assert not banner.isVisible()

    first = _record(0x01, "POZAR SKLAD B", received=1_000.0)
    banner.show_latest([first])
    assert banner.isVisible()
    assert banner.headline.text() == tr("alert.mayday")
    assert banner.note.text() == "POZAR SKLAD B"
    assert "OK2IPW" in banner.origin.text()

    banner.dismiss()
    banner.show_latest([first])
    assert not banner.isVisible(), "a dismissed alert must not come back"

    later = _record(0x02, "", received=1_001.0)
    banner.show_latest([later, first])
    assert banner.isVisible(), "a newer alert is shown even after a dismissal"
    assert banner.headline.text() == tr("alert.medical")
    assert not banner.note.isVisible(), "no note, no empty line"


def test_banner_marks_urgency_so_the_border_can_shout() -> None:
    _application()
    banner = AlertBanner()
    banner.show_latest([_record(0x01)])
    assert banner.property("alertRole") == "urgent"
    banner.show_latest([_record(0x12, received=2_000.0)])
    assert banner.property("alertRole") == "routine"


def test_unknown_alert_code_is_still_displayed() -> None:
    # Codes outlive builds: a station running an older Guardian has to show
    # that something was broadcast rather than swallow it.
    _application()
    banner = AlertBanner()
    banner.show_latest([_record(0x77, "???")])
    assert banner.isVisible()
    assert "0x77" in banner.headline.text()


def test_alert_dialog_offers_every_code_and_counts_the_room_left() -> None:
    _application()
    runtime = ShellRuntime()
    dialog = AlertDialog(runtime)
    room = runtime.operations.max_alert_note()

    codes = [
        dialog.kind_picker.itemData(index)
        for index in range(dialog.kind_picker.count())
    ]
    assert codes == [kind.code for kind in ALERTS]
    assert dialog.note_edit.maxLength() == room
    assert tr("alert.dialog_room", used=0, total=room) == dialog.room_label.text()

    dialog.note_edit.setText("A" * 12)
    assert tr("alert.dialog_room", used=12, total=room) == dialog.room_label.text()

    # The hint tells the operator what this particular code wants in the note.
    dialog.kind_picker.setCurrentIndex(codes.index(0x11))
    assert dialog.note_edit.placeholderText() == tr("alert.hint_frequency")


def test_alert_dialog_needs_confirmation_and_a_control_channel(monkeypatch) -> None:
    _application()
    runtime = ShellRuntime()
    dialog = AlertDialog(runtime)
    dialog.note_edit.setText("POZAR")

    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )
    sent: list[tuple[int, str]] = []
    monkeypatch.setattr(
        runtime.operations, "send_alert",
        lambda code, note="": sent.append((code, note)) or True,
    )
    dialog._broadcast()
    assert sent == [], "declining the confirmation must not key the radio"

    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    dialog._broadcast()
    assert sent == [(0x01, "POZAR")]

    # A refused send (no control channel) keeps the dialog open to say so.
    warned: list[str] = []
    monkeypatch.setattr(
        runtime.operations, "send_alert", lambda code, note="": False
    )
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda _p, _t, text, *a, **k: warned.append(text)),
    )
    dialog._broadcast()
    assert warned == [tr("alert.no_control")]


def test_alert_text_reads_in_the_operators_language() -> None:
    # The whole point of sending a code instead of a sentence.
    _application()
    try:
        set_language(Language.CZECH)
        banner = AlertBanner()
        banner.show_latest([_record(0x03)])
        czech = banner.headline.text()
        set_language(Language.ENGLISH)
        banner_en = AlertBanner()
        banner_en.show_latest([_record(0x03)])
        assert czech == "Probíhá evakuace"
        assert banner_en.headline.text() == "Evacuation under way"
    finally:
        set_language(Language.ENGLISH)


def test_mail_workspace_carries_the_send_control() -> None:
    _application()
    runtime = ShellRuntime()
    from guardian.qt.mail_workspace import MailWorkspace

    workspace = MailWorkspace(runtime)
    assert workspace.alert_button.text() == tr("alert.send")
    assert workspace.alert_button.isEnabled()


def test_every_alert_code_has_a_sentence_and_a_hint_in_both_languages() -> None:
    from guardian.i18n import TRANSLATIONS

    for kind in ALERTS:
        for key in (kind.key, kind.hint_key):
            assert key in TRANSLATIONS, key
            english, czech = TRANSLATIONS[key]
            assert english and czech and english != czech
        assert isinstance(kind.priority, Priority)
