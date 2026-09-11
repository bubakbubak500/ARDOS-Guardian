"""Independent review coverage for mailbox changes in G1 points 5--7."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QMessageBox

from guardian.config import StationConfig
from guardian.message import Folder, MailMessage, MessageStore, Status
from guardian.operations import Operations
from guardian.qt.mail_workspace import MailWorkspace
from guardian.routing import HeardStations, RouteTable
from guardian.services import EventBus, SnapshotStore, WorkerPool


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _mail(
    msg_id: int,
    *,
    folder: str,
    status: str,
    created: float = 1_700_000_000.0,
    subject: str | None = None,
) -> MailMessage:
    return MailMessage(
        msg_id=msg_id,
        source="OK2IPW",
        final_dest="OK7PS",
        subject=subject or f"Message {msg_id}",
        body=f"Body {msg_id}",
        created=created,
        folder=folder,
        status=status,
    )


def _operations(tmp_path, **overrides):
    config = StationConfig(callsign="OK7PS", radio_backend="none", **overrides)
    workers = WorkerPool(max_workers=1)
    store = MessageStore(tmp_path / "mail")
    operations = Operations(
        config,
        EventBus(),
        SnapshotStore(),
        workers,
        store,
        RouteTable(),
        HeardStations(),
    )
    return operations, workers, store


def test_partial_bulk_delete_keeps_failed_file_index_and_snapshot_consistent(
    tmp_path, monkeypatch
) -> None:
    operations, workers, store = _operations(tmp_path)
    first = _mail(1, folder=Folder.OUTBOX, status=Status.QUEUED)
    second = _mail(2, folder=Folder.OUTBOX, status=Status.QUEUED)
    store.add(first)
    store.add(second)
    second_bundle = tmp_path / "mail" / "2.bundle"
    original_unlink = Path.unlink

    def fail_second(path, *args, **kwargs):
        if path == second_bundle:
            raise PermissionError("locked by another process")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_second)
    try:
        removed = operations.delete_mail(
            (first.msg_id, second.msg_id), folder=Folder.OUTBOX
        )
        assert removed == 1
        assert store.get(first.msg_id) is None
        assert store.get(second.msg_id) is not None
        assert not (tmp_path / "mail" / "1.bundle").exists()
        assert second_bundle.exists()
        assert operations.snapshots.read().mailbox.outbox == 1
        assert any(
            "partly deleted" in event.message
            for event in operations.events.history()
        )
    finally:
        operations.close()
        workers.close(wait=True)


def test_selected_ids_survive_a_sort_and_refresh_by_message_id(
    tmp_path, monkeypatch
) -> None:
    _application()
    store = MessageStore(tmp_path / "mail")
    for msg_id, subject in ((1, "Zulu"), (2, "Alpha"), (3, "Mike")):
        store.add(
            _mail(
                msg_id,
                folder=Folder.OUTBOX,
                status=Status.QUEUED,
                created=1_700_000_000 + msg_id,
                subject=subject,
            )
        )
    runtime = SimpleNamespace(
        mailstore=store,
        config=SimpleNamespace(callsign="OK7PS"),
        refresh=lambda: None,
        events=SimpleNamespace(publish=lambda *args, **kwargs: None),
    )
    workspace = MailWorkspace(runtime)
    try:
        workspace.folders.setCurrentRow(1)
        table = workspace.messages
        table.selectRow(0)
        table.selectionModel().select(
            table.model().index(2, 0),
            QItemSelectionModel.SelectionFlag.Select
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        _application().processEvents()
        expected = set(workspace.selected_ids)
        assert expected == {1, 3}

        table.sortItems(1, Qt.SortOrder.AscendingOrder)
        _application().processEvents()
        original_get = store.get
        bundle_reads = []
        monkeypatch.setattr(
            store,
            "get",
            lambda msg_id: bundle_reads.append(msg_id) or original_get(msg_id),
        )
        workspace.refresh()

        row_ids = [
            int(table.item(row, 0).data(Qt.ItemDataRole.UserRole))
            for row in range(table.rowCount())
        ]
        selected_rows = {
            int(table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole))
            for index in table.selectionModel().selectedRows()
        }
        assert row_ids == [2, 3, 1]
        assert workspace.selected_ids == expected
        assert selected_rows == expected

        # A single selected message exercises the reader cache. Preserve a
        # text selection and scroll position while refreshing unchanged rows;
        # the bundle must not be unpacked again just to repaint the table.
        table.clearSelection()
        table.selectRow(1)  # message 3 after subject sorting
        _application().processEvents()
        cursor = workspace.reader.textCursor()
        cursor.setPosition(0)
        cursor.movePosition(
            QTextCursor.MoveOperation.NextWord,
            QTextCursor.MoveMode.KeepAnchor,
        )
        workspace.reader.setTextCursor(cursor)
        workspace.reader.verticalScrollBar().setValue(
            workspace.reader.verticalScrollBar().maximum()
        )
        selected_text = workspace.reader.textCursor().selectedText()
        scroll_value = workspace.reader.verticalScrollBar().value()
        assert workspace.selected_ids == {3}

        bundle_reads.clear()
        workspace.refresh()
        assert workspace.reader.textCursor().selectedText() == selected_text
        assert workspace.reader.verticalScrollBar().value() == scroll_value
        assert bundle_reads == []
    finally:
        workspace.close()


def test_historical_sent_row_with_delayed_receipt_shows_unknown_time(
    tmp_path,
) -> None:
    _application()
    operations, workers, store = _operations(tmp_path)
    message = _mail(
        9,
        folder=Folder.SENT,
        status=Status.FORWARDED,
        created=1_700_000_000,
        subject="Delayed receipt",
    )
    store.add(message)
    assert operations._delivery_receipt_route(message.msg_id, "OK7PS") == ""
    stored = store.get(message.msg_id)
    assert stored is not None
    assert (stored.folder, stored.status) == (Folder.SENT, Status.DELIVERED)
    reloaded = MessageStore(tmp_path / "mail")
    assert reloaded.list(Folder.SENT)[0]["status"] == Status.DELIVERED
    assert "sent_at" not in reloaded.list(Folder.SENT)[0]
    runtime = SimpleNamespace(
        mailstore=store,
        config=SimpleNamespace(callsign="OK7PS"),
        refresh=lambda: None,
        events=SimpleNamespace(publish=lambda *args, **kwargs: None),
        operations=operations,
    )
    workspace = MailWorkspace(runtime)
    try:
        workspace.folders.setCurrentRow(2)
        assert workspace.messages.horizontalHeaderItem(5).text() == "Sent"
        assert workspace.messages.item(0, 5).text() == "Unknown"
        assert "2023" not in workspace.messages.item(0, 5).text()
    finally:
        workspace.close()
        operations.close()
        workers.close(wait=True)


def test_repeated_send_and_delete_are_blocked_during_compression(
    tmp_path, monkeypatch
) -> None:
    operations, workers, store = _operations(
        tmp_path, guardian_compression=True
    )
    message = _mail(10, folder=Folder.OUTBOX, status=Status.QUEUED)
    store.add(message)
    started = threading.Event()
    release = threading.Event()
    announced = []
    original_compress = MailMessage.to_guardian_bundle

    def slow_compress(self, baseline=None):
        started.set()
        assert release.wait(2.0)
        return original_compress(self, baseline)

    monkeypatch.setattr(MailMessage, "to_guardian_bundle", slow_compress)
    monkeypatch.setattr(
        operations.net,
        "send_message",
        lambda **kwargs: announced.append(kwargs),
    )
    operations.audio_transport = SimpleNamespace()
    try:
        assert operations.send_queued(message.msg_id)
        assert started.wait(2.0)
        assert not operations.send_queued(message.msg_id)
        with pytest.raises(RuntimeError, match="being prepared"):
            operations.delete_mail((message.msg_id,), folder=Folder.OUTBOX)
        assert store.get(message.msg_id).status == Status.SENDING
        release.set()
        deadline = time.monotonic() + 2.0
        while (
            workers.is_active(f"mail-compress-{message.msg_id}")
            and time.monotonic() < deadline
        ):
            time.sleep(0.001)
        assert not workers.is_active(f"mail-compress-{message.msg_id}")
        workers.drain()
        assert len(announced) == 1
    finally:
        release.set()
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_bulk_delete_prompt_is_explicit_yes_no_with_safe_default(
    tmp_path, monkeypatch
) -> None:
    _application()
    store = MessageStore(tmp_path / "mail")
    message = _mail(11, folder=Folder.OUTBOX, status=Status.QUEUED)
    store.add(message)
    runtime = SimpleNamespace(
        mailstore=store,
        config=SimpleNamespace(callsign="OK7PS"),
        refresh=lambda: None,
        events=SimpleNamespace(publish=lambda *args, **kwargs: None),
        workers=WorkerPool(max_workers=1),
        operations=SimpleNamespace(),
    )
    workspace = MailWorkspace(runtime)
    asked = {}

    def question(*args, **kwargs):
        asked["args"] = args
        asked["kwargs"] = kwargs
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    try:
        workspace.folders.setCurrentRow(1)
        workspace.messages.selectRow(0)
        workspace.delete_selected()
        assert asked["args"][3] == (
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        assert asked["args"][4] == QMessageBox.StandardButton.No
    finally:
        workspace.close()
        runtime.workers.close(wait=True)
