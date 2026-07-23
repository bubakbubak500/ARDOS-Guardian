"""Fast native mailbox workspace backed by Guardian's existing MessageStore."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..message import Attachment, Folder, MailMessage, Status
from ..protocol import Priority
from .runtime import ShellRuntime


class ComposeDialog(QDialog):
    queued = Signal(int)

    def __init__(
        self,
        runtime: ShellRuntime,
        parent=None,
        *,
        reply_to: MailMessage | None = None,
    ) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.attachments: list[Attachment] = []
        self.setWindowTitle("Compose message")
        self.setMinimumSize(660, 560)
        outer = QVBoxLayout(self)
        form = QFormLayout()
        self.destination = QLineEdit()
        self.destination.setPlaceholderText("OK1AAA")
        self.subject = QLineEdit()
        self.priority = QComboBox()
        for priority in Priority:
            self.priority.addItem(priority.name.title(), int(priority))
        form.addRow("To", self.destination)
        form.addRow("Subject", self.subject)
        form.addRow("Priority", self.priority)
        outer.addLayout(form)
        self.body = QPlainTextEdit()
        self.body.setPlaceholderText("Message text")
        outer.addWidget(self.body, 1)
        attachment_bar = QHBoxLayout()
        self.attachment_summary = QLabel("No attachments")
        self.attachment_summary.setObjectName("Metadata")
        attach = QPushButton("Attach files…")
        attach.clicked.connect(self._attach)
        attachment_bar.addWidget(self.attachment_summary, 1)
        attachment_bar.addWidget(attach)
        outer.addLayout(attachment_bar)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(
            "Queue in Outbox"
        )
        buttons.accepted.connect(self._queue)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        if reply_to is not None:
            self.destination.setText(reply_to.source)
            self.subject.setText(f"Re: {reply_to.subject}")
            quoted = "\n".join(f"> {line}" for line in reply_to.body.splitlines())
            self.body.setPlainText(
                f"\n\n--- {reply_to.source} wrote ---\n{quoted}"
            )

    def _attach(self) -> None:
        for value in QFileDialog.getOpenFileNames(
            self, "Attach files"
        )[0]:
            path = Path(value)
            try:
                self.attachments.append(Attachment(path.name, path.read_bytes()))
            except OSError as exc:
                QMessageBox.warning(self, "Attachment", str(exc))
        total = sum(item.size for item in self.attachments)
        warning = " · large for RF" if total > 50_000 else ""
        self.attachment_summary.setText(
            f"{len(self.attachments)} file(s), {total} bytes{warning}"
        )

    def _queue(self) -> None:
        destination = self.destination.text().strip().upper()
        if not destination:
            QMessageBox.warning(self, "Compose message", "Enter a destination.")
            return
        message = MailMessage(
            msg_id=self.runtime.mailstore.next_id(self.runtime.config.callsign),
            source=self.runtime.config.callsign,
            final_dest=destination,
            subject=self.subject.text().strip(),
            body=self.body.toPlainText(),
            attachments=list(self.attachments),
            priority=int(self.priority.currentData()),
            created=time.time(),
            hops=[self.runtime.config.callsign],
            folder=Folder.OUTBOX,
            status=Status.QUEUED,
        )
        self.runtime.mailstore.add(message)
        self.runtime.refresh()
        self.runtime.events.publish(
            f"Message #{message.msg_id} queued for {destination}.",
            source="mail",
        )
        self.queued.emit(message.msg_id)
        self.accept()


class MailWorkspace(QWidget):
    FOLDERS = (
        ("Inbox", Folder.INBOX),
        ("Outbox", Folder.OUTBOX),
        ("Sent", Folder.SENT),
        ("Transit", Folder.TRANSIT),
        ("Drafts", Folder.DRAFT),
    )

    def __init__(self, runtime: ShellRuntime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.folder = Folder.INBOX
        self.selected_id: int | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        top = QHBoxLayout()
        title = QLabel("Mail")
        title.setObjectName("PanelHeader")
        compose = QPushButton("Compose")
        compose.setObjectName("primaryAction")
        compose.clicked.connect(self.compose)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        top.addWidget(title)
        top.addStretch()
        top.addWidget(refresh)
        top.addWidget(compose)
        outer.addLayout(top)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.folders = QListWidget()
        self.folders.setMaximumWidth(175)
        for label, _folder in self.FOLDERS:
            self.folders.addItem(label)
        self.folders.currentRowChanged.connect(self._select_folder)
        splitter.addWidget(self.folders)
        content = QSplitter(Qt.Orientation.Vertical)
        self.messages = QTableWidget(0, 5)
        self.messages.setHorizontalHeaderLabels(
            ["From / To", "Subject", "Status", "Attachments", "Size"]
        )
        self.messages.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.messages.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.messages.verticalHeader().hide()
        self.messages.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.messages.itemSelectionChanged.connect(self._open_selected)
        content.addWidget(self.messages)
        reader = QWidget()
        reader_layout = QVBoxLayout(reader)
        reader_layout.setContentsMargins(0, 4, 0, 0)
        self.reader = QPlainTextEdit()
        self.reader.setReadOnly(True)
        self.reader.setPlaceholderText("Select a message")
        reader_layout.addWidget(self.reader, 1)
        actions = QHBoxLayout()
        self.reply_button = QPushButton("Reply")
        self.reply_button.clicked.connect(self.reply)
        self.send_button = QPushButton("Send queued message")
        self.send_button.setObjectName("primaryAction")
        self.send_button.clicked.connect(self.send_selected)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self.delete_selected)
        actions.addWidget(self.reply_button)
        actions.addWidget(self.send_button)
        actions.addStretch()
        actions.addWidget(self.delete_button)
        reader_layout.addLayout(actions)
        content.addWidget(reader)
        content.setSizes([300, 280])
        splitter.addWidget(content)
        splitter.setStretchFactor(1, 1)
        outer.addWidget(splitter, 1)
        self.folders.setCurrentRow(0)

    def _select_folder(self, row: int) -> None:
        if 0 <= row < len(self.FOLDERS):
            self.folder = self.FOLDERS[row][1]
            self.refresh()

    def refresh(self) -> None:
        counts = self.runtime.mailstore.counts()
        for index, (label, folder) in enumerate(self.FOLDERS):
            unread = self.runtime.mailstore.unread(folder)
            suffix = f", {unread} new" if unread else ""
            self.folders.item(index).setText(
                f"{label} ({counts.get(folder, 0)}{suffix})"
            )
        rows = self.runtime.mailstore.list(self.folder)
        self.messages.setRowCount(len(rows))
        for row, metadata in enumerate(rows):
            peer = (
                metadata["source"]
                if self.folder == Folder.INBOX
                else metadata["final_dest"]
            )
            values = (
                peer,
                metadata.get("subject") or "(no subject)",
                metadata.get("status", ""),
                str(metadata.get("att", 0)),
                f"{metadata.get('size', 0)} B",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, metadata["msg_id"])
                if not metadata.get("read", True):
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.messages.setItem(row, column, item)

    def _open_selected(self) -> None:
        selected = self.messages.selectedItems()
        if not selected:
            return
        self.selected_id = int(selected[0].data(Qt.ItemDataRole.UserRole))
        self.runtime.mailstore.mark_read(self.selected_id)
        message = self.runtime.mailstore.get(self.selected_id)
        if message is None:
            self.reader.setPlainText("(message not found)")
            return
        route = " -> ".join(message.hops) or "-"
        attachments = "\n".join(
            f"  {item.name} ({item.size} B)" for item in message.attachments
        ) or "  none"
        self.reader.setPlainText(
            f"From: {message.source}\nTo: {message.final_dest}\n"
            f"Subject: {message.subject}\nStatus: {message.status}\n"
            f"Route: {route}\nAttachments:\n{attachments}\n"
            f"{'-' * 52}\n{message.body}"
        )
        self.reply_button.setEnabled(message.folder == Folder.INBOX)
        self.send_button.setEnabled(message.folder in (Folder.OUTBOX, Folder.TRANSIT))
        self.refresh()

    def compose(self, *, reply_to: MailMessage | None = None) -> None:
        dialog = ComposeDialog(self.runtime, self, reply_to=reply_to)
        dialog.queued.connect(lambda _message_id: self.refresh())
        dialog.exec()

    def reply(self) -> None:
        if self.selected_id is None:
            return
        message = self.runtime.mailstore.get(self.selected_id)
        if message is not None:
            self.compose(reply_to=message)

    def delete_selected(self) -> None:
        if self.selected_id is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete message",
            f"Delete message #{self.selected_id} from this station?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.runtime.mailstore.delete(self.selected_id)
        self.runtime.events.publish(
            f"Message #{self.selected_id} deleted.",
            source="mail",
        )
        self.selected_id = None
        self.reader.clear()
        self.runtime.refresh()
        self.refresh()

    def send_selected(self) -> None:
        if self.selected_id is None:
            return
        if not self.runtime.operations.send_queued(self.selected_id):
            QMessageBox.information(
                self,
                "Send message",
                "The message remains queued. Start the live control channel "
                "before sending.",
            )
            return
        self.runtime.refresh()
        self.refresh()
