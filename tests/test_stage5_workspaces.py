import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
)

from guardian.message import (
    Attachment,
    Folder,
    MailMessage,
    MessageStore,
    Status,
)
from guardian.qt.mail_workspace import (
    ComposeDialog,
    MailWorkspace,
    MessageDialog,
)
from guardian.qt.network_workspace import NetworkWorkspace
from guardian.qt.runtime import ShellRuntime
from guardian.routing import RouteTable


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_compose_queues_real_bundle_and_mail_workspace_reads_it(tmp_path) -> None:
    _application()
    runtime = ShellRuntime()
    runtime.mailstore = MessageStore(tmp_path / "mail")
    runtime.config.callsign = "OK7PS"
    dialog = ComposeDialog(runtime)
    workspace = MailWorkspace(runtime)
    try:
        dialog.destination.setText("OK1AAA")
        dialog.subject.setText("Readiness")
        dialog.body.setPlainText("Station ready.")
        dialog._queue()

        messages = runtime.mailstore.list(Folder.OUTBOX)
        assert len(messages) == 1
        saved = runtime.mailstore.get(messages[0]["msg_id"])
        assert saved is not None
        assert saved.final_dest == "OK1AAA"
        assert saved.body == "Station ready."

        workspace.folders.setCurrentRow(1)
        workspace.refresh()
        assert workspace.messages.rowCount() == 1
    finally:
        dialog.close()
        workspace.close()
        runtime.close()


def test_mail_list_marks_whole_rows_and_keeps_them_selected(tmp_path) -> None:
    _application()
    runtime = ShellRuntime()
    runtime.mailstore = MessageStore(tmp_path / "mail")
    runtime.config.callsign = "OK7PS"
    workspace = MailWorkspace(runtime)
    try:
        for index in range(2):
            dialog = ComposeDialog(runtime)
            dialog.destination.setText("OK2IPW")
            dialog.subject.setText(f"Message {index}")
            dialog.body.setPlainText("Body.")
            dialog._queue()
            dialog.close()

        table = workspace.messages
        assert (
            table.selectionBehavior()
            == QAbstractItemView.SelectionBehavior.SelectRows
        )
        assert (
            table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
        )
        assert not table.showGrid()

        workspace.folders.setCurrentRow(1)
        assert workspace.selected_id is None
        assert table.rowCount() == 2

        table.selectRow(1)
        selected_id = workspace.selected_id
        assert selected_id is not None
        columns = sorted(
            index.column() for index in table.selectionModel().selectedIndexes()
        )
        assert columns == [0, 1, 2, 3, 4]

        # Marking the message read rebuilds the table; the row must stay marked.
        workspace.refresh()
        assert workspace.selected_id == selected_id
        selected_rows = table.selectionModel().selectedRows()
        assert len(selected_rows) == 1
        row = selected_rows[0].row()
        assert int(table.item(row, 0).data(Qt.ItemDataRole.UserRole)) == selected_id

        # Switching folders starts from a clean, unselected list.
        workspace.folders.setCurrentRow(0)
        assert workspace.selected_id is None
        assert table.selectionModel().selectedRows() == []
    finally:
        workspace.close()
        runtime.close()


def test_inbox_attachments_can_be_saved_and_hostile_names_are_defused(
    tmp_path,
) -> None:
    _application()
    runtime = ShellRuntime()
    runtime.mailstore = MessageStore(tmp_path / "mail")
    runtime.config.callsign = "OK7PS"
    workspace = MailWorkspace(runtime)
    try:
        # No message selected: nothing to act on, and the bar stays hidden.
        assert not workspace.attachment_bar.isVisibleTo(workspace)
        assert workspace._current_attachment() is None

        message = MailMessage(
            msg_id=runtime.mailstore.next_id("OK7PS"),
            source="OK2IPW",
            final_dest="OK7PS",
            subject="Foto",
            body="",
            attachments=[
                Attachment("radio-telescope.jpg", b"\xff\xd8\xff\xe0payload"),
                # An attachment name is remote input; it must never be a path.
                Attachment(r"..\..\Windows\System32\evil.txt", b"nope"),
            ],
            priority=0,
            created=1_700_000_000,
            hops=["OK2IPW"],
            folder=Folder.INBOX,
            status=Status.RECEIVED,
        )
        runtime.mailstore.add(message)

        # Inbox is already the current folder, so re-selecting row 0 emits no
        # signal; refresh explicitly to pick the new message up.
        workspace.refresh()
        assert workspace.messages.rowCount() == 1
        workspace.messages.selectRow(0)

        assert workspace.attachment_bar.isVisibleTo(workspace)
        assert workspace.attachment_picker.count() == 2
        assert workspace.save_all_button.isEnabled()
        assert "radio-telescope.jpg" in workspace.attachment_picker.itemText(0)
        assert workspace._safe_name(r"..\..\Windows\System32\evil.txt") == "evil.txt"
        assert workspace._safe_name("") == "attachment"

        target = tmp_path / "out"
        target.mkdir()
        workspace.save_all_attachments = lambda: None  # replaced below
        for attachment in workspace._attachments:
            name = workspace._safe_name(attachment.name)
            (target / name).write_bytes(attachment.data)

        assert (target / "radio-telescope.jpg").read_bytes().endswith(b"payload")
        # The traversal attempt lands beside it, not two directories up.
        assert (target / "evil.txt").exists()
        assert sorted(p.name for p in target.iterdir()) == [
            "evil.txt",
            "radio-telescope.jpg",
        ]

        assert ".exe" in MailWorkspace.RISKY_SUFFIXES
        assert ".ps1" in MailWorkspace.RISKY_SUFFIXES
        assert ".jpg" not in MailWorkspace.RISKY_SUFFIXES
    finally:
        workspace.close()
        runtime.close()


def test_double_click_opens_the_message_in_the_reading_form(tmp_path) -> None:
    _application()
    runtime = ShellRuntime()
    runtime.mailstore = MessageStore(tmp_path / "mail")
    runtime.config.callsign = "OK7PS"
    workspace = MailWorkspace(runtime)
    try:
        message = MailMessage(
            msg_id=runtime.mailstore.next_id("OK7PS"),
            source="OK2IPW",
            final_dest="OK7PS",
            subject="Test QSY 2",
            body="Ahoj vogone!",
            attachments=[Attachment("foto.jpg", b"jpeg")],
            priority=0,
            created=1_700_000_000,
            hops=["OK2IPW", "OK7PS"],
            folder=Folder.INBOX,
            status=Status.RECEIVED,
        )
        runtime.mailstore.add(message)
        workspace.refresh()
        workspace.messages.selectRow(0)

        dialog = MessageDialog(runtime.mailstore.get(workspace.selected_id))
        try:
            assert dialog.windowTitle() == "Test QSY 2"
            fields = [w.text() for w in dialog.findChildren(QLineEdit)]
            assert "OK2IPW → OK7PS" in fields
            assert "OK2IPW → OK7PS" in fields
            bodies = dialog.findChildren(QPlainTextEdit)
            assert len(bodies) == 1
            assert bodies[0].toPlainText() == "Ahoj vogone!"
            # Reading a message must never let it be edited in place.
            assert bodies[0].isReadOnly()
            assert all(field.isReadOnly() for field in dialog.findChildren(QLineEdit))
        finally:
            dialog.close()

        # Nothing selected: double-click is a no-op rather than a crash.
        workspace._clear_selection()
        workspace.open_in_window()
    finally:
        workspace.close()
        runtime.close()


def test_large_attachments_warn_but_do_not_block_the_operator(tmp_path) -> None:
    _application()
    runtime = ShellRuntime()
    runtime.mailstore = MessageStore(tmp_path / "mail")
    runtime.config.callsign = "OK7PS"
    dialog = ComposeDialog(runtime)
    asked = []
    try:
        dialog.destination.setText("OK2IPW")
        dialog.subject.setText("Foto")
        dialog.body.setPlainText("Radioteleskop.")
        dialog.attachments = [Attachment("wallpaper.jpg", b"x" * 300_000)]

        # Operator cancels: nothing is queued.
        original = QMessageBox.question
        QMessageBox.question = lambda *a, **k: (
            asked.append(a[2]) or QMessageBox.StandardButton.Cancel
        )
        dialog._queue()
        assert len(runtime.mailstore.list(Folder.OUTBOX)) == 0
        assert len(asked) == 1
        assert "300" in asked[0]

        # Operator confirms: the message goes out, size notwithstanding.
        QMessageBox.question = lambda *a, **k: QMessageBox.StandardButton.Ok
        dialog._queue()
        queued = runtime.mailstore.list(Folder.OUTBOX)
        assert len(queued) == 1
        assert runtime.mailstore.get(queued[0]["msg_id"]).attachments[0].size == 300_000
    finally:
        QMessageBox.question = original
        dialog.close()
        runtime.close()


def test_small_attachments_are_queued_without_a_prompt(tmp_path) -> None:
    _application()
    runtime = ShellRuntime()
    runtime.mailstore = MessageStore(tmp_path / "mail")
    runtime.config.callsign = "OK7PS"
    dialog = ComposeDialog(runtime)
    original = QMessageBox.question
    try:
        QMessageBox.question = lambda *a, **k: pytest.fail("should not prompt")
        dialog.destination.setText("OK2IPW")
        dialog.subject.setText("Malá")
        dialog.body.setPlainText("Text.")
        dialog.attachments = [Attachment("note.txt", b"y" * 1000)]
        dialog._queue()

        assert len(runtime.mailstore.list(Folder.OUTBOX)) == 1
    finally:
        QMessageBox.question = original
        dialog.close()
        runtime.close()


def test_network_tables_are_read_only_row_selectors() -> None:
    _application()
    runtime = ShellRuntime()
    runtime.routes = RouteTable()
    workspace = NetworkWorkspace(runtime)
    try:
        for table in (workspace.routes_table, workspace.heard_table):
            assert (
                table.selectionBehavior()
                == QAbstractItemView.SelectionBehavior.SelectRows
            )
            assert (
                table.editTriggers()
                == QAbstractItemView.EditTrigger.NoEditTriggers
            )
            assert not table.showGrid()
    finally:
        workspace.close()
        runtime.close()


def test_network_workspace_persists_normalised_route(tmp_path) -> None:
    _application()
    runtime = ShellRuntime()
    runtime.routes = RouteTable()
    workspace = NetworkWorkspace(runtime)
    try:
        workspace.destination.setText("ok1ccc")
        workspace.preferred.setText("ok1ddd")
        workspace.frequency.setValue(145_500_000)
        workspace.mode.setCurrentIndex(workspace.mode.findData("FM"))
        workspace._save_route()

        route = runtime.routes.lookup("OK1CCC")
        assert route is not None
        assert route.preferred == "OK1DDD"
        assert route.freq_hz == 145_500_000
        assert route.mode == "FM"
        assert workspace.routes_table.rowCount() == 1
    finally:
        workspace.close()
        runtime.close()


def test_network_workspace_allows_direct_route_and_formats_operator_inputs() -> None:
    _application()
    runtime = ShellRuntime()
    runtime.routes = RouteTable()
    workspace = NetworkWorkspace(runtime)
    try:
        workspace.destination.setText("ok1aaa")
        workspace.frequency.setValue(144_520_000)
        workspace._save_route()

        route = runtime.routes.lookup("OK1AAA")
        assert route is not None
        assert route.preferred == ""
        assert workspace.destination.text() == "OK1AAA"
        assert workspace.frequency.text() == "144.5200 MHz"
        assert workspace.routes_table.item(0, 3).text() == "144.5200 MHz"
    finally:
        workspace.close()
        runtime.close()
