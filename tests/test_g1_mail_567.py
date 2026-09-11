import json
import os
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QApplication

from guardian.message import Folder, MailMessage, MessageStore, Status
from guardian.qt.mail_workspace import MailWorkspace
from guardian.qt.runtime import ShellRuntime


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _mail(msg_id: int, *, folder: str, status: str) -> MailMessage:
    return MailMessage(
        msg_id=msg_id,
        source="OK2IPW",
        final_dest="OK7PS",
        subject=f"Message {msg_id}",
        body="Body",
        created=1_700_000_000 + msg_id,
        folder=folder,
        status=status,
    )


def test_local_mail_times_stay_in_index_and_survive_repeated_storage(
    tmp_path, monkeypatch
) -> None:
    store = MessageStore(tmp_path / "mail")
    incoming = _mail(1, folder=Folder.INBOX, status=Status.RECEIVED)
    clock = iter((1_700_000_123.5, 1_700_000_456.5))
    monkeypatch.setattr(
        "guardian.message.store.time",
        SimpleNamespace(time=lambda: next(clock)),
    )

    stored = store.store_incoming(incoming.to_bundle(), "OK7PS", via="OK1ABC")
    assert store.list(Folder.INBOX)[0]["received_at"] == 1_700_000_123.5

    # A duplicate relay/storage event keeps the first local receipt time.
    store.store_incoming(stored.to_bundle(), "OK7PS", via="OK1ABC")
    assert store.list(Folder.INBOX)[0]["received_at"] == 1_700_000_123.5

    outbound = _mail(2, folder=Folder.OUTBOX, status=Status.QUEUED)
    store.add(outbound)
    store.mark_sent(outbound.msg_id, at=789.0)
    store.add(outbound)
    assert store.list(Folder.OUTBOX)[0]["sent_at"] == 789.0

    with zipfile.ZipFile(BytesIO(outbound.to_bundle())) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert "received_at" not in manifest
    assert "sent_at" not in manifest


def test_delete_keeps_index_entry_when_bundle_cannot_be_unlinked(tmp_path, monkeypatch):
    store = MessageStore(tmp_path / "mail")
    message = _mail(7, folder=Folder.INBOX, status=Status.RECEIVED)
    store.add(message)
    bundle = tmp_path / "mail" / "7.bundle"
    original_unlink = Path.unlink

    def fail_unlink(path, *args, **kwargs):
        if path == bundle:
            raise PermissionError("locked")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    try:
        store.delete(message.msg_id)
    except PermissionError:
        pass
    else:
        raise AssertionError("locked bundle should make delete fail")
    assert store.list(Folder.INBOX)[0]["msg_id"] == message.msg_id
    assert bundle.exists()


def test_mail_selection_and_peer_header_follow_folder_and_refresh(tmp_path) -> None:
    _application()
    runtime = ShellRuntime()
    runtime.mailstore = MessageStore(tmp_path / "mail")
    runtime.config.callsign = "OK7PS"
    for msg_id in (1, 2):
        mail = _mail(msg_id, folder=Folder.INBOX, status=Status.RECEIVED)
        runtime.mailstore.add(mail, received_at=1_700_000_000 + msg_id)
    workspace = MailWorkspace(runtime)
    try:
        workspace.refresh()
        workspace.messages.selectRow(0)
        row = workspace.messages.model().index(1, 0)
        workspace.messages.selectionModel().select(
            row,
            QItemSelectionModel.SelectionFlag.Select
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        assert workspace.selected_ids == {1, 2}
        workspace.refresh()
        assert workspace.selected_ids == {1, 2}
        assert workspace.messages.columnCount() == 6
        assert workspace.messages.horizontalHeaderItem(0).text() == "From"
        assert workspace.messages.horizontalHeaderItem(5).text() == "Received"
        assert workspace.messages.item(0, 5).text().startswith("2023-")
        workspace.messages.sortItems(5, Qt.SortOrder.AscendingOrder)
        assert int(workspace.messages.item(0, 0).data(Qt.ItemDataRole.UserRole)) == 1
        runtime.mailstore.delete(2)
        workspace.refresh()
        assert workspace.selected_ids == {1}
        assert workspace.selected_id == 1
        assert "Message 1" in workspace.reader.toPlainText()

        workspace.folders.setCurrentRow(1)
        assert workspace.messages.horizontalHeaderItem(0).text() == "To"
        assert workspace.selected_ids == set()
    finally:
        workspace.close()
        runtime.close()
