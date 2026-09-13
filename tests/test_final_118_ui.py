"""Last 1.1.8 mailbox and touchpad regressions."""
import os
import time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QItemSelectionModel, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QNativeGestureEvent, QPointingDevice, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGestureEvent, QMessageBox, QPinchGesture

from guardian.message import Attachment, Folder, MailMessage, MessageStore, Status
from guardian.qt.mail_workspace import ComposeDialog, MailWorkspace
from guardian.qt.map_window import MapCanvas, MIN_DEGREES_ACROSS, MAX_DEGREES_ACROSS
from guardian.qt.runtime import ShellRuntime
from guardian.qt.shell import GuardianMainWindow
from guardian.services import MailboxSnapshot


@pytest.fixture
def runtime(tmp_path):
    app = QApplication.instance() or QApplication([])
    runtime = ShellRuntime()
    runtime.mailstore = MessageStore(tmp_path / "mail")
    runtime.operations.mailstore = runtime.mailstore
    runtime.config.callsign = "OK7PS"
    yield runtime
    runtime.close()
    app.processEvents()


def _mail(msg_id, folder=Folder.INBOX):
    return MailMessage(msg_id=msg_id, source="OK2IPW", final_dest="OK7PS",
                       subject="Original", body="Original body", folder=folder,
                       status=Status.RECEIVED if folder == Folder.INBOX else Status.QUEUED,
                       read=False, attachments=[Attachment("report.txt", b"original attachment")])


@pytest.mark.parametrize("selection_count", [1, 2])
def test_delete_key_removes_selected_rows_only(runtime, monkeypatch, selection_count):
    for msg_id in (1, 2, 3):
        runtime.mailstore.add(_mail(msg_id))
    workspace = MailWorkspace(runtime)
    questions = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: questions.append(args) or QMessageBox.StandardButton.Yes)
    try:
        workspace.show()
        workspace.activateWindow()
        QApplication.processEvents()
        workspace.messages.selectRow(0)
        if selection_count == 2:
            workspace.messages.selectionModel().select(
                workspace.messages.model().index(1, 0),
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
            )
        selected = set(workspace.selected_ids)
        workspace.reader.setFocus()
        QTest.keyClick(workspace.reader, Qt.Key.Key_Delete)
        assert questions == []  # Reader focus is not a mailbox-delete shortcut.
        workspace.messages.setFocus()
        QTest.keyClick(workspace.messages, Qt.Key.Key_Delete)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            runtime.drain_workers()
            QApplication.processEvents()
            if len(runtime.mailstore.list()) == 3 - selection_count:
                break
            QTest.qWait(10)
        assert len(questions) == 1
        assert {m["msg_id"] for m in runtime.mailstore.list()} == {1, 2, 3} - selected
    finally:
        workspace.close()


def test_forward_button_creates_new_mail_with_original_content_and_attachments(runtime, monkeypatch):
    original = _mail(1)
    runtime.mailstore.add(original)
    workspace = MailWorkspace(runtime)
    def compose(dialog):
        assert dialog.destination.text() == ""
        assert original.source in dialog.body.toPlainText()
        assert original.final_dest in dialog.body.toPlainText()
        assert original.body in dialog.body.toPlainText()
        assert dialog.attachments == original.attachments
        assert dialog.attachments[0] is not original.attachments[0]
        dialog.destination.setText("OK2JLD")
        dialog._queue()
        return 1
    monkeypatch.setattr(ComposeDialog, "exec", compose)
    try:
        workspace.messages.selectRow(0)
        assert workspace.forward_button.isEnabled()
        workspace.forward_button.click()
        queued = runtime.mailstore.list(Folder.OUTBOX)
        assert len(queued) == 1 and queued[0]["msg_id"] != original.msg_id
        forwarded = runtime.mailstore.get(queued[0]["msg_id"])
        assert forwarded.source == "OK7PS" and forwarded.final_dest == "OK2JLD"
        assert forwarded.subject == "Fwd: Original"
        assert forwarded.attachments == original.attachments
        assert runtime.mailstore.get(original.msg_id).body == original.body
        assert runtime.mailstore.get(original.msg_id).folder == Folder.INBOX
    finally:
        workspace.close()


def test_header_refresh_uses_current_folder_counts_after_mail_changes(runtime, tmp_path):
    window = GuardianMainWindow(runtime, QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat))
    try:
        runtime.snapshots.update(mailbox=MailboxSnapshot(outbox=99, transit=88, unread=77))
        runtime.mailstore.add(_mail(1, Folder.OUTBOX))
        runtime.mailstore.add(_mail(2, Folder.TRANSIT))
        runtime.mailstore.add(_mail(3))
        window._refresh()
        assert window.metrics["outbox"].value.text() == "1"
        assert window.metrics["transit"].value.text() == "1"
        assert window.metrics["unread"].value.text() == "1"
        runtime.mailstore.set_status(1, folder=Folder.SENT, status=Status.DELIVERED)
        runtime.mailstore.delete(2)
        runtime.mailstore.mark_read(3)
        window._refresh()
        assert window.metrics["outbox"].value.text() == "0"
        assert window.metrics["transit"].value.text() == "0"
        assert window.metrics["unread"].value.text() == "0"
        assert "99" not in window.context_activity.text()
    finally:
        window.close()


def test_map_native_pinch_keeps_position_under_fingers(runtime):
    canvas = MapCanvas()
    canvas.resize(800, 600)
    point = QPointF(230, 210)
    before = canvas.to_position(point)
    across = canvas.degrees_across
    event = QNativeGestureEvent(Qt.NativeGestureType.ZoomNativeGesture,
                               QPointingDevice.primaryPointingDevice(), 2,
                               point, point, point, 0.25, QPointF())
    QApplication.sendEvent(canvas, event)
    assert canvas.degrees_across == pytest.approx(across / 1.25)
    assert canvas.to_position(point) == pytest.approx(before)
    canvas.close()


def test_map_pinch_and_pixel_wheel_use_bounded_zoom(runtime):
    canvas = MapCanvas()
    canvas.resize(800, 600)
    across = canvas.degrees_across
    pinch = QPinchGesture()
    pinch.setTotalScaleFactor(2.0)
    QApplication.sendEvent(canvas, QGestureEvent([pinch]))
    assert canvas.degrees_across == pytest.approx(across / 2)
    pinch.setTotalScaleFactor(3.0)
    QApplication.sendEvent(canvas, QGestureEvent([pinch]))
    assert canvas.degrees_across == pytest.approx(across / 3)
    point = QPointF(400, 300)
    def wheel(pixel, angle):
        return QWheelEvent(point, point, pixel, angle, Qt.MouseButton.NoButton,
                           Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate, False)
    before = canvas.degrees_across
    canvas.wheelEvent(wheel(QPoint(), QPoint()))
    assert canvas.degrees_across == before
    canvas.wheelEvent(wheel(QPoint(0, 60), QPoint()))
    assert canvas.degrees_across < before
    canvas.zoom_by(1e12)
    assert canvas.degrees_across == MIN_DEGREES_ACROSS
    canvas.zoom_by(1e-12)
    assert canvas.degrees_across == MAX_DEGREES_ACROSS
    canvas.close()
