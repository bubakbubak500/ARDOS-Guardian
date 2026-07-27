"""Small input widgets shared by Guardian's Qt workspaces."""

from __future__ import annotations

from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QLineEdit, QSpinBox


class UppercaseLineEdit(QLineEdit):
    """A line edit that normalises operator-entered identifiers immediately."""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.textChanged.connect(self._uppercase)
        self._uppercase(self.text())

    def _uppercase(self, value: str) -> None:
        upper = value.upper()
        if upper == value:
            return
        cursor = self.cursorPosition()
        self.blockSignals(True)
        self.setText(upper)
        self.setCursorPosition(cursor)
        self.blockSignals(False)


class FrequencySpinBox(QSpinBox):
    """Store integer hertz while presenting the operator-friendly MHz value."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setRange(0, 2_147_483_647)
        self.setSingleStep(1_000)
        self.setAccelerated(True)
        self.setToolTip("144.5200 MHz")

    def textFromValue(self, value: int) -> str:
        return f"{value / 1_000_000:.4f} MHz"

    def valueFromText(self, text: str) -> int:
        cleaned = text.strip().lower().replace("mhz", "").replace(",", ".")
        try:
            return round(float(cleaned) * 1_000_000)
        except ValueError:
            return 0

    def validate(self, text: str, pos: int):
        cleaned = text.strip().lower().replace("mhz", "").replace(",", ".")
        if not cleaned:
            return QValidator.State.Intermediate, text, pos
        try:
            value = float(cleaned)
        except ValueError:
            return QValidator.State.Invalid, text, pos
        if 0 <= value <= self.maximum() / 1_000_000:
            return QValidator.State.Acceptable, text, pos
        return QValidator.State.Invalid, text, pos
