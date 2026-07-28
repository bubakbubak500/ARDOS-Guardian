"""Native mailbox workspace backed by Guardian's existing MessageStore."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
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
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..i18n import language, tr
from ..message import (
    Attachment,
    Folder,
    MailMessage,
    Status,
    safe_attachment_name,
)
from ..message.forms import FORMS
from ..payload.vara_p2p import airtime_for
from ..protocol import Priority
from .inputs import RowTable, UppercaseLineEdit
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
        self.destination = UppercaseLineEdit()
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

    # Attachments above this are worth a word before they occupy the channel
    # for minutes. It is a heads-up, not a limit: the operator decides.
    ATTACHMENT_WARN_BYTES = 200_000

    def _queue(self) -> None:
        destination = self.destination.text().strip().upper()
        if not destination:
            QMessageBox.warning(
                self,
                tr("compose.title"),
                tr("compose.destination_required"),
            )
            return
        attached = sum(item.size for item in self.attachments)
        if attached > self.ATTACHMENT_WARN_BYTES:
            answer = QMessageBox.question(
                self,
                tr("compose.large_attachments_title"),
                tr(
                    "compose.large_attachments",
                    size=round(attached / 1000),
                    minutes=max(1, round(airtime_for(attached) / 60)),
                ),
                QMessageBox.StandardButton.Ok
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Ok,
            )
            if answer != QMessageBox.StandardButton.Ok:
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


class MessageDialog(QDialog):
    """Read a received message in the same laid-out form used to write one."""

    def __init__(self, message: MailMessage, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(message.subject or tr("mail.no_subject"))
        self.setMinimumSize(720, 640)

        outer = QVBoxLayout(self)
        header = QFormLayout()
        for label, value in (
            (tr("mail.peer"), f"{message.source} → {message.final_dest}"),
            (tr("mail.subject"), message.subject or tr("mail.no_subject")),
            (tr("mail.status"), tr(f"status.{message.status}")),
            (tr("mail.route"), " → ".join(message.hops) or "-"),
        ):
            field = QLineEdit(value)
            field.setReadOnly(True)
            header.addRow(label, field)
        outer.addLayout(header)

        if message.attachments:
            summary = QLabel(
                ", ".join(
                    f"{safe_attachment_name(a.name)} ({a.size} B)"
                    for a in message.attachments
                )
            )
            summary.setObjectName("Metadata")
            summary.setWordWrap(True)
            header.addRow(tr("mail.attachments"), summary)

        body = QPlainTextEdit(message.body)
        body.setReadOnly(True)
        outer.addWidget(body, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(
            tr("common.close")
        )
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)


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
        self._attachments: list[Attachment] = []
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
        self.messages = RowTable(0, 5)
        self.messages.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.messages.itemSelectionChanged.connect(self._open_selected)
        self.messages.doubleClicked.connect(self.open_in_window)
        content.addWidget(self.messages)

        reader = QWidget()
        reader_layout = QVBoxLayout(reader)
        reader_layout.setContentsMargins(0, 4, 0, 0)
        self.reader = QPlainTextEdit()
        self.reader.setReadOnly(True)
        reader_layout.addWidget(self.reader, 1)

        self.attachment_bar = QWidget()
        attachment_row = QHBoxLayout(self.attachment_bar)
        attachment_row.setContentsMargins(0, 4, 0, 0)
        self.attachment_label = QLabel()
        self.attachment_label.setObjectName("Metadata")
        self.attachment_picker = QComboBox()
        self.attachment_picker.setMinimumWidth(220)
        self.open_attachment_button = QPushButton()
        self.open_attachment_button.clicked.connect(self.open_attachment)
        self.save_attachment_button = QPushButton()
        self.save_attachment_button.clicked.connect(self.save_attachment)
        self.save_all_button = QPushButton()
        self.save_all_button.clicked.connect(self.save_all_attachments)
        attachment_row.addWidget(self.attachment_label)
        attachment_row.addWidget(self.attachment_picker, 1)
        attachment_row.addWidget(self.open_attachment_button)
        attachment_row.addWidget(self.save_attachment_button)
        attachment_row.addWidget(self.save_all_button)
        self.attachment_bar.setVisible(False)
        reader_layout.addWidget(self.attachment_bar)

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
        self.attachment_label.setText(tr("mail.attachments"))
        self.open_attachment_button.setText(tr("mail.attachment_open"))
        self.save_attachment_button.setText(tr("mail.attachment_save"))
        self.save_all_button.setText(tr("mail.attachment_save_all"))
        self.reply_button.setText(tr("mail.reply"))
        self.send_button.setText(tr("mail.send_queued"))
        self.delete_button.setText(tr("mail.delete"))
        self.refresh()

    def _select_folder(self, row: int) -> None:
        if 0 <= row < len(self.FOLDER_KEYS):
            self.folder = self.FOLDER_KEYS[row][1]
            self._clear_selection()
            self.refresh()

    def _clear_selection(self) -> None:
        self.selected_id = None
        self.reader.clear()
        self.reply_button.setEnabled(False)
        self.send_button.setEnabled(False)
        self._show_attachments(None)

    # ---------------------------- attachments ------------------------- #
    # Attachment names arrive over the air from another station, so they are
    # never used as a path: only the bare filename is kept, and the operator
    # always picks the destination.
    RISKY_SUFFIXES = frozenset({
        ".bat", ".cmd", ".com", ".cpl", ".dll", ".exe", ".hta", ".inf", ".jar",
        ".js", ".jse", ".lnk", ".msc", ".msi", ".msp", ".pif", ".ps1", ".reg",
        ".scr", ".sct", ".vb", ".vbe", ".vbs", ".wsf", ".wsh",
    })

    _safe_name = staticmethod(safe_attachment_name)

    def _show_attachments(self, message: MailMessage | None) -> None:
        self._attachments = list(message.attachments) if message else []
        self.attachment_picker.clear()
        for item in self._attachments:
            self.attachment_picker.addItem(
                f"{self._safe_name(item.name)}  ({item.size} B)"
            )
        has_any = bool(self._attachments)
        self.attachment_bar.setVisible(has_any)
        self.open_attachment_button.setEnabled(has_any)
        self.save_attachment_button.setEnabled(has_any)
        self.save_all_button.setEnabled(len(self._attachments) > 1)

    def _current_attachment(self) -> Attachment | None:
        index = self.attachment_picker.currentIndex()
        if 0 <= index < len(self._attachments):
            return self._attachments[index]
        return None

    def open_attachment(self) -> None:
        attachment = self._current_attachment()
        if attachment is None:
            return
        name = self._safe_name(attachment.name)
        if Path(name).suffix.lower() in self.RISKY_SUFFIXES:
            source = "?"
            if self.selected_id is not None:
                message = self.runtime.mailstore.get(self.selected_id)
                source = message.source if message else "?"
            answer = QMessageBox.warning(
                self,
                tr("mail.attachment_risky_title"),
                tr("mail.attachment_risky", name=name, source=source),
                QMessageBox.StandardButton.Open
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Open:
                return
        target = Path(tempfile.mkdtemp(prefix="guardian-attachment-")) / name
        try:
            target.write_bytes(attachment.data)
        except OSError as exc:
            QMessageBox.warning(
                self,
                tr("mail.attachments"),
                tr("mail.attachment_save_error", name=name, error=str(exc)),
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            QMessageBox.information(
                self,
                tr("mail.attachments"),
                tr("mail.attachment_open_error", name=name),
            )

    def save_attachment(self) -> None:
        attachment = self._current_attachment()
        if attachment is None:
            return
        name = self._safe_name(attachment.name)
        chosen, _ = QFileDialog.getSaveFileName(
            self, tr("mail.attachment_save"), name
        )
        if not chosen:
            return
        try:
            Path(chosen).write_bytes(attachment.data)
        except OSError as exc:
            QMessageBox.warning(
                self,
                tr("mail.attachments"),
                tr("mail.attachment_save_error", name=name, error=str(exc)),
            )
            return
        self.runtime.events.publish(
            tr("mail.attachment_saved", name=name, path=chosen),
            source="mail",
        )

    def save_all_attachments(self) -> None:
        if not self._attachments:
            return
        folder = QFileDialog.getExistingDirectory(
            self, tr("mail.attachment_choose_folder")
        )
        if not folder:
            return
        saved = 0
        for attachment in self._attachments:
            name = self._safe_name(attachment.name)
            try:
                (Path(folder) / name).write_bytes(attachment.data)
            except OSError as exc:
                QMessageBox.warning(
                    self,
                    tr("mail.attachments"),
                    tr("mail.attachment_save_error", name=name, error=str(exc)),
                )
                continue
            saved += 1
        if saved:
            self.runtime.events.publish(
                tr("mail.attachment_saved_all", count=saved, path=folder),
                source="mail",
            )

    def refresh(self) -> None:
        counts = self.runtime.mailstore.counts()
        for index, (key, folder) in enumerate(self.FOLDER_KEYS):
            unread = self.runtime.mailstore.unread(folder)
            suffix = tr("mail.new_count", count=unread) if unread else ""
            self.folders.item(index).setText(
                f"{tr(key)} ({counts.get(folder, 0)}{suffix})"
            )
        rows = self.runtime.mailstore.list(self.folder)
        # Rebuilding the rows drops the selection, so restore it afterwards
        # instead of leaving the operator with a bare focus rectangle.
        selected_row = -1
        self.messages.blockSignals(True)
        try:
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
                if metadata["msg_id"] == self.selected_id:
                    selected_row = row
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, metadata["msg_id"])
                    if not metadata.get("read", True):
                        font = item.font()
                        font.setBold(True)
                        item.setFont(font)
                    self.messages.setItem(row, column, item)
            if selected_row >= 0:
                self.messages.selectRow(selected_row)
            else:
                self.messages.clearSelection()
        finally:
            self.messages.blockSignals(False)

    def _open_selected(self) -> None:
        selected = self.messages.selectedItems()
        if not selected:
            return
        self.selected_id = int(selected[0].data(Qt.ItemDataRole.UserRole))
        self.runtime.mailstore.mark_read(self.selected_id)
        message = self.runtime.mailstore.get(self.selected_id)
        if message is None:
            self.reader.setPlainText(tr("mail.not_found"))
            self._show_attachments(None)
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
        self._show_attachments(message)
        self.refresh()

    def open_in_window(self, *_args) -> None:
        """Double-click: read the message in the roomier form layout."""
        if self.selected_id is None:
            return
        message = self.runtime.mailstore.get(self.selected_id)
        if message is None:
            return
        MessageDialog(message, self).exec()

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
