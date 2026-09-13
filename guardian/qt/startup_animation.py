"""Native, non-skippable Guardian startup animation."""

from __future__ import annotations

import math

from PySide6.QtCore import (
    QByteArray,
    QElapsedTimer,
    QEvent,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QDialog

# Symbol from the user-supplied Guardian_sticker_white.svg; no wordmark/background.
LOGO_SVG = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="420 460 1170 1170">
<polygon points="512,521.5 1498,521.5 1498,1029 1237,1348 1005,1580 773,1348 512,1029" fill="#154360"/>
<polygon points="561.3,572.25 1448.7,572.25 1448.7,1029 1213.8,1316.1 1005,1524.9 796.2,1316.1 561.3,1029" fill="#2471A3"/>
<g fill="none" stroke="#EAF0F5" stroke-width="31.90" stroke-linecap="butt">
<path d="M854.31,1058 A174,174 0 0 1 1155.69,1058"/>
<path d="M766.41,1007.25 A275.5,275.5 0 0 1 1243.59,1007.25"/>
<path d="M678.51,956.5 A377,377 0 0 1 1331.49,956.5"/>
</g><circle cx="1005" cy="1145" r="50.75" fill="#F1C40F"/></svg>'''


def _smooth(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


def _random(seed: int) -> float:
    value = math.sin(seed * 127.1 + 311.7) * 43758.5453
    return value - math.floor(value)


# Tiny 5x7 ASCII glyphs stay legible without depending on platform font rasterization.
GLYPHS = (
    (14,17,19,21,25,17,14), (4,12,4,4,4,4,14),
    (2,4,8,16,8,4,2), (8,4,2,1,2,4,8),
    (1,2,2,4,8,8,16), (3,4,4,8,4,4,3),
    (24,4,4,2,4,4,24), (14,8,8,8,8,8,14),
    (14,2,2,2,2,2,14), (0,4,4,0,4,4,0),
    (10,10,31,10,31,10,10), (0,21,14,31,14,21,0),
    (0,4,4,31,4,4,0), (0,0,31,0,31,0,0),
    (0,0,0,0,0,4,4),
)


class StartupAnimation(QDialog):
    DURATION_MS = 5000  # Original 6500 ms played at 1.3x speed.
    MAXIMUM_SIZE = QSize(560, 460)

    _GEOMETRY_EVENTS = frozenset(
        {
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.WindowStateChange,
            QEvent.Type.ScreenChangeInternal,
        }
    )

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("Guardian")
        self.setAccessibleName("Guardian startup animation")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._finished = False
        self._animation_started = False
        self._time = 0.0
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._advance)
        self._recenter_timer = QTimer(self)
        self._recenter_timer.setSingleShot(True)
        self._recenter_timer.timeout.connect(self._recenter_if_visible)
        self._tracked_parent = None
        self._tracked_widgets = []
        self._tracked_window = None
        self._tracked_screen = None
        self._renderer = QSvgRenderer(QByteArray(LOGO_SVG), self)
        self._glyphs = []
        for rows in GLYPHS:
            path = QPainterPath()
            for gy, row in enumerate(rows):
                for gx in range(5):
                    if row & (1 << (4 - gx)):
                        path.addRect(gx - 2.5, gy - 3.5, .85, .85)
            self._glyphs.append(path)
        sample = QImage(512, 512, QImage.Format.Format_ARGB32)
        sample.fill(Qt.GlobalColor.transparent)
        painter = QPainter(sample)
        self._renderer.render(painter)
        painter.end()
        self._cells = []
        for y in range(4, 512, 8):
            for x in range(3, 512, 6):
                color = sample.pixelColor(x, y)
                if color.alpha() > 100 and color.green() > 30:
                    n = len(self._cells)
                    self._cells.append((x, y, color, n, _random(n) * .7,
                                        (_random(n + 12) - .5) * 950,
                                        (_random(n + 49) - .5) * 740))

        self._attach_geometry_tracking()
        self._set_size_for_screen(self._target_screen())

    def showEvent(self, event):
        super().showEvent(event)
        self._attach_geometry_tracking()
        self._recenter()
        if not self._animation_started and not self._finished:
            self._clock.start()
            self._timer.start()
            self._animation_started = True

    def _target_screen(self):
        """Return the screen that currently owns the parent window."""
        parent = self.parentWidget()
        window = parent.window() if parent is not None else None
        handle = window.windowHandle() if window is not None else None
        if handle is not None and handle.screen() is not None:
            return handle.screen()

        application = QGuiApplication.instance()
        if application is not None:
            if window is not None:
                frame = window.frameGeometry()
                if not frame.isNull():
                    screen = application.screenAt(frame.center())
                    if screen is not None:
                        return screen
            screen = self.screen()
            if screen is not None:
                return screen
            return application.primaryScreen()
        return None

    def _available_geometry(self, screen) -> QRect:
        if screen is None:
            return QRect(0, 0, self.MAXIMUM_SIZE.width(), self.MAXIMUM_SIZE.height())
        return screen.availableGeometry()

    def _set_size_for_screen(self, screen) -> None:
        """Keep the splash usable when the active screen is smaller or scaled."""
        available = self._available_geometry(screen)
        width = max(1, min(self.MAXIMUM_SIZE.width(), available.width()))
        height = max(1, min(self.MAXIMUM_SIZE.height(), available.height()))
        if self.size() != QSize(width, height):
            self.setFixedSize(width, height)

        # Frameless windows normally have no frame contribution, but a
        # platform plugin can still reserve a few pixels around a dialog.
        # Account for that contribution so the complete window remains in the
        # work area rather than only its client rect.
        frame_size = self.frameGeometry().size()
        frame_width = max(0, frame_size.width() - self.width())
        frame_height = max(0, frame_size.height() - self.height())
        width = max(1, min(width, available.width() - frame_width))
        height = max(1, min(height, available.height() - frame_height))
        if self.width() != width or self.height() != height:
            self.setFixedSize(width, height)

    def _recenter(self) -> None:
        screen = self._target_screen()
        available = self._available_geometry(screen)
        self._set_size_for_screen(screen)

        parent = self.parentWidget()
        window = parent.window() if parent is not None else None
        anchor = window.frameGeometry() if window is not None else QRect()
        if anchor.isNull():
            anchor = available

        frame = self.frameGeometry()
        frame.moveCenter(anchor.center())
        max_x = available.left() + max(0, available.width() - frame.width())
        max_y = available.top() + max(0, available.height() - frame.height())
        frame.moveTopLeft(
            QPoint(
                max(available.left(), min(frame.left(), max_x)),
                max(available.top(), min(frame.top(), max_y)),
            )
        )
        self.move(frame.topLeft())

    def _recenter_if_visible(self) -> None:
        if not self._finished and self.isVisible():
            self._recenter()

    def _queue_recenter(self) -> None:
        if not self._finished and self.isVisible() and not self._recenter_timer.isActive():
            self._recenter_timer.start(0)

    def _attach_geometry_tracking(self) -> None:
        parent = self.parentWidget()
        if parent is not self._tracked_parent:
            self._detach_geometry_tracking()
            self._tracked_parent = parent
            if parent is not None:
                for widget in (parent, parent.window()):
                    if widget is not None and widget not in self._tracked_widgets:
                        widget.installEventFilter(self)
                        self._tracked_widgets.append(widget)
                parent.destroyed.connect(self._on_parent_destroyed)

        window = parent.window() if parent is not None else None
        handle = window.windowHandle() if window is not None else None
        if handle is not self._tracked_window:
            if self._tracked_window is not None:
                try:
                    self._tracked_window.screenChanged.disconnect(
                        self._on_parent_screen_changed
                    )
                except (RuntimeError, TypeError):
                    pass
            self._tracked_window = handle
            if handle is not None:
                handle.screenChanged.connect(self._on_parent_screen_changed)

        self._connect_screen(self._target_screen())

    def _connect_screen(self, screen) -> None:
        if screen is self._tracked_screen:
            return
        if self._tracked_screen is not None:
            for signal in (
                self._tracked_screen.availableGeometryChanged,
                self._tracked_screen.geometryChanged,
                self._tracked_screen.logicalDotsPerInchChanged,
            ):
                try:
                    signal.disconnect(self._on_screen_geometry_changed)
                except (RuntimeError, TypeError):
                    pass
        self._tracked_screen = screen
        if screen is not None:
            for signal in (
                screen.availableGeometryChanged,
                screen.geometryChanged,
                screen.logicalDotsPerInchChanged,
            ):
                signal.connect(self._on_screen_geometry_changed)

    def _detach_geometry_tracking(self) -> None:
        for widget in self._tracked_widgets:
            try:
                widget.removeEventFilter(self)
            except RuntimeError:
                pass
        self._tracked_widgets.clear()
        if self._tracked_parent is not None:
            try:
                self._tracked_parent.destroyed.disconnect(self._on_parent_destroyed)
            except (RuntimeError, TypeError):
                pass
            self._tracked_parent = None
        if self._tracked_window is not None:
            try:
                self._tracked_window.screenChanged.disconnect(self._on_parent_screen_changed)
            except (RuntimeError, TypeError):
                pass
            self._tracked_window = None
        self._connect_screen(None)
        if self._recenter_timer.isActive():
            self._recenter_timer.stop()

    def _on_parent_destroyed(self, *_args) -> None:
        # Qt has already removed the event filter from a destroyed parent.
        self._tracked_parent = None
        self._tracked_widgets.clear()
        self._tracked_window = None
        self._connect_screen(None)
        if self._recenter_timer.isActive():
            self._recenter_timer.stop()

    def _on_parent_screen_changed(self, *_args) -> None:
        self._attach_geometry_tracking()
        self._recenter_if_visible()
        self._queue_recenter()

    def _on_screen_geometry_changed(self, *_args) -> None:
        self._recenter_if_visible()
        self._queue_recenter()

    def eventFilter(self, watched, event):
        if watched in self._tracked_widgets and event.type() in self._GEOMETRY_EVENTS:
            if event.type() in {
                QEvent.Type.Show,
                QEvent.Type.WindowStateChange,
                QEvent.Type.ScreenChangeInternal,
            }:
                self._attach_geometry_tracking()
            self._recenter_if_visible()
            self._queue_recenter()
        return super().eventFilter(watched, event)

    def _advance(self):
        elapsed = self._clock.elapsed()
        self._time = min(6.5, elapsed / 1000 * 1.3)
        self.update()
        if elapsed >= self.DURATION_MS:
            self._timer.stop()
            self._finished = True
            self._detach_geometry_tracking()
            super().done(QDialog.DialogCode.Accepted)

    def done(self, result):
        if self._finished:
            super().done(result)

    def reject(self):
        pass

    def accept(self):
        pass

    def closeEvent(self, event):
        if self._finished:
            self._detach_geometry_tracking()
            super().closeEvent(event)
        else:
            event.ignore()

    def keyPressEvent(self, event):
        event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#060e17"))
        size = min(self.width() * .64, self.height() * .76)
        painter.translate((self.width() - size) / 2, (self.height() - size) / 2 - 8)
        painter.scale(size / 512, size / 512)
        t = self._time
        chars = '01<>/{}[]:#*+=.'
        for x, y, color, n, delay, sx, sy in self._cells:
            form = _smooth((t - .15 - delay) / 1.9)
            blend = _smooth((t - 2.7 - y / 650) / 1.4)
            alpha = _smooth((t - delay) / .4) * (1 - _smooth((t - 4.05 - y / 750) / 1.2))
            if alpha < .005:
                continue
            rgb = [round(a + (b - a) * blend) for a, b in zip((137, 187, 192), color.getRgb()[:3])]
            index = int(t * 16 + n * 7) if form < .99 else n
            painter.save()
            painter.translate(x + sx * (1 - form), y + sy * (1 - form))
            painter.fillPath(self._glyphs[index % len(chars)], QColor(*rgb, round(alpha * 255)))
            painter.restore()
        painter.setOpacity(_smooth((t - 3.5) / 2.5))
        self._renderer.render(painter, QRectF(0, 0, 512, 512))
        painter.end()
