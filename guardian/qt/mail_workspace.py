"""Native mailbox workspace backed by Guardian's existing MessageStore."""

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
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..i18n import language, tr
from ..message import Attachment, Folder, MailMessage, Status
from ..message.forms import FORMS
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
        self.field_widgets: dict[str, QLineEdit | QPlainTextEdit] = {}
        self.setWindowTitle(tr("compose.title"))
        self.setMinimumSize(720, 640)

        outer = QVBoxLayout(self)
        header = QFormLayout()
        self.destination = QLineEdit()
        self.destination.setPlaceholderText("OK1AAA")
        self.template = QComboBox()
        self.template.addItem(tr("compose.template_plain"), "Plain")
        for code, message_form in FORMS.items():
            self.template.addItem(
                message_form.display_name(language().value),
                code,
            )
        self.template.currentIndexChanged.connect(self._render_template)
        self.priority = QComboBox()
        for priority in Priority:
            self.priority.addItem(
                tr(f"priority.{priority.name.lower()}"),
                int(priority),
            )
        header.addRow(tr("compose.to"), self.destination)
        header.addRow(tr("compose.template"), self.template)
        header.addRow(tr("compose.priority"), self.priority)
        outer.addLayout(header)

        self.template_description = QLabel()
        self.template_description.setObjectName("Metadata")
        self.template_description.setWordWrap(True)
        outer.addWidget(self.template_description)

        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.form_host = QWidget()
        self.form_layout = QFormLayout(self.form_host)
        self.form_layout.setContentsMargins(2, 4, 2, 4)
        self.form_layout.setVerticalSpacing(8)
        self.form_layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        self.form_scroll.setWidget(self.form_host)
        outer.addWidget(self.form_scroll, 1)

        attachment_bar = QHBoxLayout()
        self.attachment_summary = QLabel(tr("compose.no_attachments"))
        self.attachment_summary.setObjectName("Metadata")
        attach = QPushButton(tr("compose.attach"))
        attach.clicked.connect(self._attach)
        attachment_bar.addWidget(self.attachment_summary, 1)
        attachment_bar.addWidget(attach)
        outer.addLayout(attachment_bar)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(
            tr("compose.queue")
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(
            tr("common.cancel")
        )
        buttons.accepted.connect(self._queue)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._render_template()
        self.subject = self.field_widgets["subject"]
        self.body = self.field_widgets["body"]
        if reply_to is not None:
            self.destination.setText(reply_to.source)
            subject = self.field_widgets["subject"]
            body = self.field_widgets["body"]
            assert isinstance(subject, QLineEdit)
            assert isinstance(body, QPlainTextEdit)
            subject.setText(f"Re: {reply_to.subject}")
            quoted = "\n".join(f"> {line}" for line in reply_to.body.splitlines())
            body.setPlainText(
                tr(
                    "compose.reply_quote",
                    source=reply_to.source,
                    quoted=quoted,
                )
            )

    def _clear_form(self) -> None:
        while self.form_layout.count():
            item = self.form_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.field_widgets.clear()

    def _render_template(self) -> None:
        self._clear_form()
        code = self.template.currentData()
        if code == "Plain":
            self.template_description.setText(tr("compose.structured_hint"))
            subject = QLineEdit()
            body = QPlainTextEdit()
            body.setMinimumHeight(260)
            self.form_layout.addRow(tr("mail.subject"), subject)
            self.form_layout.addRow(tr("compose.message"), body)
            self.field_widgets["subject"] = subject
            self.field_widgets["body"] = body
            return

        message_form = FORMS[str(code)]
        self.template_description.setText(
            message_form.display_description(language().value)
            + "\n"
            + tr("compose.structured_hint")
        )
        for field in message_form.fields:
            if field.multiline:
                widget: QLineEdit | QPlainTextEdit = QPlainTextEdit()
                widget.setMinimumHeight(90)
            else:
                widget = QLineEdit()
            self.form_layout.addRow(
                field.display_label(language().value),
                widget,
            )
            self.field_widgets[field.key] = widget

    @staticmethod
    def _widget_value(widget: QLineEdit | QPlainTextEdit) -> str:
        if isinstance(widget, QPlainTextEdit):
            return widget.toPlainText().rstrip()
        return widget.text().strip()

    def _attach(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, tr("compose.attach"))
        for value in paths:
            path = Path(value)
            try:
                self.attachments.append(Attachment(path.name, path.read_bytes()))
            except OSError as exc:
                QMessageBox.warning(self, tr("compose.attach_error"), str(exc))
        total = sum(item.size for item in self.attachments)
        warning = tr("compose.large_rf") if total > 50_000 else ""
        self.attachment_summary.setText(
            tr(
                "compose.attachment_summary",
                count=len(self.attachments),
                size=total,
                warning=warning,
            )
        )

    def _queue(self) -> None:
        destination = self.destination.text().strip().upper()
        if not destination:
            QMessageBox.warning(
                self,
                tr("compose.title"),
                tr("compose.destination_required"),
            )
            return
        code = self.template.currentData()
        if code == "Plain":
            subject = self._widget_value(self.field_widgets["subject"])
            body = self._widget_value(self.field_widgets["body"])
        else:
            message_form = FORMS[str(code)]
            values = {
                key: self._widget_value(widget)
                for key, widget in self.field_widgets.items()
            }
            subject = message_form.subject(values) or message_form.display_name("en")
            body = message_form.render(values)
        message = MailMessage(
            msg_id=self.runtime.mailstore.next_id(self.runtime.config.callsign),
            source=self.runtime.config.callsign,
            final_dest=destination,
            subject=subject,
            body=body,
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
            tr("event.mail_queued", id=message.msg_id, destination=destination),
            source="mail",
        )
        self.queued.emit(message.msg_id)
        self.accept()


class MailWorkspace(QWidget):
    FOLDER_KEYS = (
        ("mail.inbox", Folder.INBOX),
        ("mail.outbox", Folder.OUTBOX),
        ("mail.sent", Folder.SENT),
        ("mail.transit", Folder.TRANSIT),
        ("mail.drafts", Folder.DRAFT),
    )

    def __init__(self, runtime: ShellRuntime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.folder = Folder.INBOX
        self.selected_id: int | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        top = QHBoxLayout()
        self.title_label = QLabel()
        self.title_label.setObjectName("PanelHeader")
        self.compose_button = QPushButton()
        self.compose_button.setObjectName("primaryAction")
        self.compose_button.clicked.connect(self.compose)
        self.refresh_button = QPushButton()
        self.refresh_button.clicked.connect(self.refresh)
        top.addWidget(self.title_label)
        top.addStretch()
        top.addWidget(self.refresh_button)
        top.addWidget(self.compose_button)
        outer.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.folders = QListWidget()
        self.folders.setMaximumWidth(190)
        for _key, _folder in self.FOLDER_KEYS:
            self.folders.addItem("")
        self.folders.currentRowChanged.connect(self._select_folder)
        splitter.addWidget(self.folders)

        content = QSplitter(Qt.Orientation.Vertical)
        self.messages = QTableWidget(0, 5)
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
        reader_layout.addWidget(self.reader, 1)
        actions = QHBoxLayout()
        self.reply_button = QPushButton()
        self.reply_button.clicked.connect(self.reply)
        self.send_button = QPushButton()
        self.send_button.setObjectName("primaryAction")
        self.send_button.clicked.connect(self.send_selected)
        self.delete_button = QPushButton()
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
        self.retranslate_ui()
        self.folders.setCurrentRow(0)

    def retranslate_ui(self) -> None:
        self.title_label.setText(tr("mail.title"))
        self.compose_button.setText(tr("mail.compose"))
        self.refresh_button.setText(tr("mail.refresh"))
        self.messages.setHorizontalHeaderLabels(
            [
                tr("mail.peer"),
                tr("mail.subject"),
                tr("mail.status"),
                tr("mail.attachments"),
                tr("mail.size"),
            ]
        )
        self.reader.setPlaceholderText(tr("mail.select"))
        self.reply_button.setText(tr("mail.reply"))
        self.send_button.setText(tr("mail.send_queued"))
        self.delete_button.setText(tr("mail.delete"))
        self.refresh()

    def _select_folder(self, row: int) -> None:
        if 0 <= row < len(self.FOLDER_KEYS):
            self.folder = self.FOLDER_KEYS[row][1]
            self.refresh()

    def refresh(self) -> None:
        counts = self.runtime.mailstore.counts()
        for index, (key, folder) in enumerate(self.FOLDER_KEYS):
            unread = self.runtime.mailstore.unread(folder)
            suffix = tr("mail.new_count", count=unread) if unread else ""
            self.folders.item(index).setText(
                f"{tr(key)} ({counts.get(folder, 0)}{suffix})"
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
                metadata.get("subject") or tr("mail.no_subject"),
                tr(f"status.{metadata.get('status', '')}"),
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
            self.reader.setPlainText(tr("mail.not_found"))
            return
        route = " -> ".join(message.hops) or "-"
        attachments = "\n".join(
            f"  {item.name} ({item.size} B)" for item in message.attachments
        ) or f"  {tr('mail.none')}"
        self.reader.setPlainText(
            tr(
                "mail.reader",
                source=message.source,
                dest=message.final_dest,
                subject=message.subject,
                status=tr(f"status.{message.status}"),
                route=route,
                attachments=attachments,
                line="-" * 52,
                body=message.body,
            )
        )
        self.reply_button.setEnabled(message.folder == Folder.INBOX)
        self.send_button.setEnabled(
            message.folder in (Folder.OUTBOX, Folder.TRANSIT)
        )
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
            tr("mail.delete"),
            tr("mail.delete_confirm", id=self.selected_id),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.runtime.mailstore.delete(self.selected_id)
        self.runtime.events.publish(
            tr("event.mail_deleted", id=self.selected_id),
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
                tr("mail.send_queued"),
                tr("mail.send_requires_control"),
            )
            return
        self.runtime.refresh()
        self.refresh()
