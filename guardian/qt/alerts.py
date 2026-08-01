"""Net alert UI: the banner that shows one, and the dialog that sends one.

A code on the air becomes a sentence here, in whatever language the operator
runs -- which is the reason alerts travel as a byte and not as text.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..i18n import tr
from ..operations import AlertRecord
from ..protocol import ALERTS, Priority, alert_kind


def alert_headline(record: AlertRecord) -> str:
    """The sentence an alert code expands to for this operator."""
    kind = alert_kind(record.code)
    if kind is None:
        return tr("alert.banner_unknown", code=record.code)
    return tr(kind.key)


class AlertBanner(QFrame):
    """Shows the most recent alert until the operator dismisses it.

    Dismissal is remembered by arrival time rather than by index, so a new
    alert arriving behind a dismissed one still gets shown.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("AlertBanner")
        self._dismissed: float | None = None
        self._shown: AlertRecord | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.headline = QLabel()
        self.headline.setObjectName("AlertHeadline")
        self.headline.setWordWrap(True)
        self.note = QLabel()
        self.note.setObjectName("AlertNote")
        self.note.setWordWrap(True)
        self.origin = QLabel()
        self.origin.setObjectName("Metadata")
        text.addWidget(self.headline)
        text.addWidget(self.note)
        text.addWidget(self.origin)
        layout.addLayout(text, 1)
        self.dismiss_button = QPushButton(tr("alert.dismiss"))
        self.dismiss_button.clicked.connect(self.dismiss)
        layout.addWidget(self.dismiss_button, 0, Qt.AlignmentFlag.AlignTop)
        self.setVisible(False)

    def dismiss(self) -> None:
        if self._shown is not None:
            self._dismissed = self._shown.received
        self.setVisible(False)

    def show_latest(self, alerts: list[AlertRecord]) -> None:
        latest = alerts[0] if alerts else None
        if latest is None or (
            self._dismissed is not None and latest.received <= self._dismissed
        ):
            self.setVisible(False)
            return
        if latest is not self._shown:
            self._shown = latest
            self._render(latest)
        self.setVisible(True)

    def _render(self, record: AlertRecord) -> None:
        self.headline.setText(alert_headline(record))
        self.note.setText(record.note)
        self.note.setVisible(bool(record.note))
        origin = (
            tr("alert.banner_mine")
            if record.mine
            else tr("alert.banner_from", source=record.source)
        )
        stamp = time.strftime("%H:%M:%S", time.localtime(record.received))
        self.origin.setText(f"{stamp}  ·  {origin}")
        routine = record.priority is Priority.ROUTINE
        self.setProperty("alertRole", "routine" if routine else "urgent")
        # A property that participates in the stylesheet only takes effect
        # after the widget is repolished.
        for widget in (self, self.headline):
            widget.style().unpolish(widget)
            widget.style().polish(widget)


class AlertDialog(QDialog):
    """Pick a code, add a short note, confirm, broadcast."""

    def __init__(self, runtime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.setWindowTitle(tr("alert.dialog_title"))
        self.setMinimumWidth(420)

        outer = QVBoxLayout(self)
        intro = QLabel(tr("alert.dialog_intro"))
        intro.setObjectName("Metadata")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        form = QFormLayout()
        self.kind_picker = QComboBox()
        for kind in ALERTS:
            self.kind_picker.addItem(tr(kind.key), kind.code)
        self.kind_picker.currentIndexChanged.connect(self._kind_changed)
        form.addRow(tr("alert.dialog_kind"), self.kind_picker)

        self.note_edit = QLineEdit()
        self.note_edit.setMaxLength(runtime.operations.max_alert_note())
        self.note_edit.textChanged.connect(self._update_room)
        form.addRow(tr("alert.dialog_note"), self.note_edit)

        self.room_label = QLabel()
        self.room_label.setObjectName("Metadata")
        form.addRow("", self.room_label)

        # Reach beyond this channel: the route table is the only record
        # Guardian has of where the rest of the net listens.
        self.channels = runtime.operations.alert_sweep_channels()
        self.sweep_check = QCheckBox(
            tr("alert.dialog_sweep", count=len(self.channels))
            if self.channels
            else tr("alert.dialog_sweep_none")
        )
        self.sweep_check.setEnabled(bool(self.channels))
        self.sweep_check.setToolTip(tr("alert.dialog_sweep_hint"))
        form.addRow("", self.sweep_check)
        outer.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.send_button = buttons.addButton(
            tr("alert.dialog_send"),
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self.send_button.setObjectName("primaryAction")
        buttons.accepted.connect(self._broadcast)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._kind_changed()

    def _selected_code(self) -> int:
        return int(self.kind_picker.currentData())

    def _kind_changed(self) -> None:
        kind = alert_kind(self._selected_code())
        if kind is not None:
            self.note_edit.setPlaceholderText(tr(kind.hint_key))
            # Spraying a routine QRT across every channel in the table is
            # noise; an emergency is exactly what the sweep exists for. The
            # operator can still decide otherwise on either.
            self.sweep_check.setChecked(
                bool(self.channels) and kind.priority is not Priority.ROUTINE
            )
        self._update_room()

    def _update_room(self) -> None:
        total = self.runtime.operations.max_alert_note()
        self.room_label.setText(
            tr("alert.dialog_room", used=len(self.note_edit.text()), total=total)
        )

    def _broadcast(self) -> None:
        code = self._selected_code()
        note = self.note_edit.text().strip()
        kind = alert_kind(code)
        headline = tr(kind.key) if kind else f"0x{code:02X}"
        text = f"{headline} — {note}" if note else headline
        sweep = self.sweep_check.isChecked() and bool(self.channels)
        question = tr("alert.confirm_body", text=text)
        if sweep:
            question += "\n\n" + tr(
                "alert.confirm_sweep", count=len(self.channels)
            )
        confirm = QMessageBox.question(
            self,
            tr("alert.confirm_title"),
            question,
        )
        if confirm is not QMessageBox.StandardButton.Yes:
            return
        if self.runtime.operations.scanner is not None:
            QMessageBox.warning(
                self,
                tr("alert.dialog_title"),
                tr("alert.stop_scanner"),
            )
            return
        if not self.runtime.operations.send_alert(code, note, sweep=sweep):
            QMessageBox.warning(
                self,
                tr("alert.dialog_title"),
                tr("alert.no_control"),
            )
            return
        self.accept()
