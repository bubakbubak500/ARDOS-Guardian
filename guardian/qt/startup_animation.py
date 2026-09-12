"""Native, non-skippable Guardian startup animation."""

from __future__ import annotations

import math

from PySide6.QtCore import QByteArray, QElapsedTimer, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath
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

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("Guardian")
        self.setAccessibleName("Guardian startup animation")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        screen = self.screen().availableGeometry()
        self.setFixedSize(min(560, screen.width()), min(460, screen.height()))
        self._finished = False
        self._time = 0.0
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._advance)
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

    def showEvent(self, event):
        super().showEvent(event)
        anchor = self.parentWidget().frameGeometry() if self.parentWidget() else self.screen().availableGeometry()
        frame = self.frameGeometry()
        frame.moveCenter(anchor.center())
        self.move(frame.topLeft())
        self._clock.start()
        self._timer.start()

    def _advance(self):
        elapsed = self._clock.elapsed()
        self._time = min(6.5, elapsed / 1000 * 1.3)
        self.update()
        if elapsed >= self.DURATION_MS:
            self._timer.stop()
            self._finished = True
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
