"""Searchable application event log."""

from __future__ import annotations

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .runtime import ShellRuntime


class LogWorkspace(QWidget):
    def __init__(self, runtime: ShellRuntime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        top = QHBoxLayout()
        title = QLabel("Log")
        title.setObjectName("PanelHeader")
        self.level = QComboBox()
        self.level.addItems(["All", "Info", "Warning", "Error"])
        self.level.currentTextChanged.connect(self.refresh)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter events")
        self.search.textChanged.connect(self.refresh)
        copy = QPushButton("Copy visible")
        copy.clicked.connect(lambda: self.viewer.selectAll())
        copy.clicked.connect(self._copy)
        top.addWidget(title)
        top.addStretch()
        top.addWidget(self.level)
        top.addWidget(self.search)
        top.addWidget(copy)
        outer.addLayout(top)
        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        outer.addWidget(self.viewer, 1)
        self.refresh()

    def _copy(self) -> None:
        self.viewer.copy()
        self.viewer.moveCursor(QTextCursor.MoveOperation.End)

    def refresh(self) -> None:
        level = self.level.currentText().lower()
        needle = self.search.text().strip().lower()
        lines: list[str] = []
        for event in self.runtime.events.history():
            if level != "all" and event.level.value != level:
                continue
            text = event.display_text
            if needle and needle not in text.lower():
                continue
            lines.append(text)
        value = "\n".join(lines)
        if value != self.viewer.toPlainText():
            self.viewer.setPlainText(value)
            self.viewer.moveCursor(QTextCursor.MoveOperation.End)
