"""Shared rich-text rendering for the full log and Home activity panel."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtGui import (
    QColor,
    QFont,
    QPalette,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import QApplication, QPlainTextEdit

from ..services import LogEvent, LogLevel


def _event_colour(event: LogEvent) -> QColor | None:
    application = QApplication.instance()
    palette = application.palette() if application is not None else QPalette()
    if event.level == LogLevel.ERROR:
        return palette.color(QPalette.ColorRole.BrightText)
    if event.is_activity_important:
        # QPalette has no warning text role. Keep the yellow legible in both
        # themes while deriving the choice from the active text brightness.
        text = palette.color(QPalette.ColorRole.Text)
        return QColor("#d1a44b" if text.lightness() > 128 else "#8a5a00")
    if event.level == LogLevel.DEBUG:
        return palette.color(QPalette.ColorRole.PlaceholderText)
    return None


def _char_format(event: LogEvent) -> QTextCharFormat:
    fmt = QTextCharFormat()
    colour = _event_colour(event)
    if colour is not None:
        fmt.setForeground(colour)
    if event.is_activity_important:
        fmt.setFontWeight(QFont.Weight.Bold)
    return fmt


def _block_format(event: LogEvent) -> QTextBlockFormat:
    fmt = QTextBlockFormat()
    if event.is_activity_important:
        # Keep a paragraph margin for rich-text viewers that honor it. The
        # renderer also inserts an explicit separator block below because
        # QPlainTextEdit does not consistently paint this margin.
        fmt.setTopMargin(6)
    return fmt


def render_events(viewer: QPlainTextEdit, events: Iterable[LogEvent]) -> None:
    """Replace a viewer's contents while preserving per-event formatting."""
    items = tuple(events)
    viewer.setUpdatesEnabled(False)
    try:
        viewer.clear()
        if not items:
            return
        cursor = QTextCursor(viewer.document())
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.beginEditBlock()
        try:
            for index, event in enumerate(items):
                block = _block_format(event)
                if index:
                    if event.is_activity_important:
                        cursor.insertBlock()
                    cursor.insertBlock(block)
                else:
                    cursor.setBlockFormat(block)
                cursor.setCharFormat(_char_format(event))
                cursor.insertText(event.display_text)
        finally:
            cursor.endEditBlock()
        viewer.setTextCursor(cursor)
        viewer.moveCursor(QTextCursor.MoveOperation.End)
    finally:
        viewer.setUpdatesEnabled(True)
