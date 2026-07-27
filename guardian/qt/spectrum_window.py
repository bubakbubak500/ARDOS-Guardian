"""Floating RX audio spectrum and waterfall for VARA P2P operation."""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
import threading

import numpy as np
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QCloseEvent, QImage, QPainter, QPen, QPolygonF
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..assets import get_ico_path
from ..i18n import dual
from ..modem.audio import resolve_device
from .theme import DARK_TOKENS, ThemeTokens


def spectrum_db(
    samples: np.ndarray,
    sample_rate: int,
    max_frequency: float,
    bins: int,
) -> np.ndarray:
    """Return display-width FFT magnitudes normalized to a stable dB range."""
    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    if values.size < 32 or bins < 2:
        return np.full(max(2, bins), -100.0, dtype=np.float32)
    fft_size = min(8192, 1 << (values.size.bit_length() - 1))
    values = values[-fft_size:]
    windowed = values * np.hanning(fft_size)
    magnitude = np.abs(np.fft.rfft(windowed)) / max(1.0, fft_size / 2)
    frequencies = np.fft.rfftfreq(fft_size, 1.0 / sample_rate)
    limit = min(float(max_frequency), sample_rate / 2)
    targets = np.linspace(0.0, limit, bins)
    interpolated = np.interp(targets, frequencies, magnitude)
    return np.maximum(20.0 * np.log10(interpolated + 1e-8), -100.0).astype(
        np.float32
    )


def waterfall_colors(db: np.ndarray) -> np.ndarray:
    """Map -100..-20 dB to a high-contrast radio waterfall palette."""
    level = np.clip((np.asarray(db) + 100.0) / 80.0, 0.0, 1.0)
    stops = np.asarray(
        [
            [3, 8, 24],
            [8, 35, 92],
            [0, 120, 170],
            [30, 205, 120],
            [245, 220, 45],
            [245, 80, 24],
            [255, 245, 225],
        ],
        dtype=np.float32,
    )
    scaled = level * (len(stops) - 1)
    low = np.floor(scaled).astype(np.int32)
    high = np.minimum(low + 1, len(stops) - 1)
    blend = (scaled - low)[:, None]
    return (stops[low] * (1.0 - blend) + stops[high] * blend).astype(np.uint8)


class AudioMonitor:
    """Small input-only PortAudio monitor; it never opens an output or keys PTT."""

    def __init__(self, device_name: str, sample_rate: int = 48_000):
        self.device_name = device_name
        self.sample_rate = sample_rate
        self.error: str | None = None
        self.actual_device_name = ""
        self.actual_device_index: int | None = None
        self.running = False
        self._stream = None
        self._blocks: deque[np.ndarray] = deque(maxlen=24)
        self._lock = threading.Lock()

    def start(self) -> None:
        if self.running:
            return
        if not self.device_name.strip():
            self.error = dual(
                "select the radio RX audio input in Station settings",
                "vyberte zvukový vstup RX rádia v nastavení stanice",
            )
            return
        try:
            import sounddevice as sd

            device = (
                resolve_device(self.device_name, "input")
                if self.device_name
                else None
            )
            if not isinstance(device, int):
                raise RuntimeError(
                    dual(
                        "the configured RX audio input is not available",
                        "nastavený zvukový vstup RX není dostupný",
                    )
                )
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                device=device,
                blocksize=2048,
                callback=self._callback,
            )
            self._stream.start()
            opened_index = getattr(self._stream, "device", device)
            if isinstance(opened_index, (tuple, list)):
                opened_index = opened_index[0]
            if int(opened_index) != device:
                raise RuntimeError(
                    f"PortAudio opened input #{opened_index}, expected #{device}"
                )
            self.actual_device_index = device
            self.actual_device_name = str(sd.query_devices(device, "input")["name"])
            self.running = True
            self.error = None
        except Exception as exc:  # audio availability is environment-specific
            stream, self._stream = self._stream, None
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
            self.error = str(exc)
            self.running = False

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        self.running = False
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def take_samples(self, minimum: int = 4096) -> np.ndarray | None:
        with self._lock:
            if not self._blocks:
                return None
            blocks: list[np.ndarray] = []
            count = 0
            while self._blocks and count < minimum:
                block = self._blocks.popleft()
                blocks.append(block)
                count += block.size
        return np.concatenate(blocks) if blocks else None

    def _callback(self, indata, _frames, _time_info, status) -> None:
        if status:
            self.error = str(status)
        block = np.asarray(indata[:, 0], dtype=np.float32).copy()
        with self._lock:
            self._blocks.append(block)


class SpectrumAnalyzer:
    """Keep FFT and palette calculations away from Qt's event thread."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="guardian-spectrum",
        )
        self._future: Future[tuple[np.ndarray, np.ndarray]] | None = None

    def submit(
        self,
        samples: np.ndarray,
        sample_rate: int,
        max_frequency: float,
        bins: int,
    ) -> None:
        if self._future is not None:
            return
        values = np.asarray(samples, dtype=np.float32).copy()

        def calculate() -> tuple[np.ndarray, np.ndarray]:
            db = spectrum_db(values, sample_rate, max_frequency, bins)
            return db, waterfall_colors(db)

        self._future = self._executor.submit(calculate)

    def take_ready(self) -> tuple[np.ndarray, np.ndarray] | None:
        if self._future is None or not self._future.done():
            return None
        future, self._future = self._future, None
        try:
            return future.result()
        except Exception:
            return None

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


class ValueCard(QFrame):
    def __init__(self, title: str, accent: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SpectrumValueCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(2)
        label = QLabel(title)
        label.setObjectName("Metadata")
        self.value = QLabel("—")
        self.value.setStyleSheet(f"font-size: 17px; font-weight: 700; color: {accent};")
        layout.addWidget(label)
        layout.addWidget(self.value)


class WaterfallScope(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumSize(680, 400)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.max_frequency = 6_000.0
        self._db = np.full(512, -100.0, dtype=np.float32)
        self._history = np.zeros((240, 512, 3), dtype=np.uint8)
        self._history[:] = (3, 8, 24)
        self.tokens: ThemeTokens = DARK_TOKENS

    def set_mode(self, mode: str) -> None:
        maximum = 3_000.0 if mode.upper() == "HF" else 6_000.0
        if maximum != self.max_frequency:
            self.max_frequency = maximum
            self.clear()

    def set_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        self.update()

    def clear(self) -> None:
        self._history[:] = (3, 8, 24)
        self._db.fill(-100.0)
        self.update()

    def push(self, samples: np.ndarray, sample_rate: int) -> None:
        width = min(512, max(200, self.width() - 2))
        db = spectrum_db(samples, sample_rate, self.max_frequency, width)
        self.push_db(db, waterfall_colors(db))

    def push_db(self, db: np.ndarray, colors: np.ndarray) -> None:
        width = db.size
        if self._history.shape[1] != width:
            self._history = np.zeros((240, width, 3), dtype=np.uint8)
            self._history[:] = (3, 8, 24)
        self._history[1:] = self._history[:-1]
        self._history[0] = colors
        self._db = db
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = self.rect()
        painter.fillRect(bounds, QColor("#030818"))

        left, right, top = 46, 12, 10
        plot_width = max(1, bounds.width() - left - right)
        scope_height = max(100, int(bounds.height() * 0.28))
        axis_height = 28
        scope = QRectF(left, top, plot_width, scope_height)
        axis_y = top + scope_height
        waterfall = QRectF(
            left,
            axis_y + axis_height,
            plot_width,
            max(1, bounds.height() - axis_y - axis_height - 12),
        )

        grid_pen = QPen(QColor("#294258"))
        grid_pen.setWidthF(1.0)
        painter.setPen(grid_pen)
        for fraction in (0.25, 0.5, 0.75):
            y = scope.top() + scope.height() * fraction
            painter.drawLine(QPointF(scope.left(), y), QPointF(scope.right(), y))
        ticks = 6
        for index in range(ticks + 1):
            x = scope.left() + scope.width() * index / ticks
            painter.drawLine(QPointF(x, scope.top()), QPointF(x, waterfall.bottom()))

        if self._db.size > 1:
            normalized = np.clip((self._db + 100.0) / 80.0, 0.0, 1.0)
            x_values = np.linspace(scope.left(), scope.right(), self._db.size)
            y_values = scope.bottom() - normalized * scope.height()
            points = QPolygonF(
                [
                    QPointF(float(x), float(y))
                    for x, y in zip(x_values, y_values, strict=True)
                ]
            )
            painter.setPen(QPen(QColor("#70e2a2"), 1.4))
            painter.drawPolyline(points)

        history = np.ascontiguousarray(self._history)
        image = QImage(
            history.data,
            history.shape[1],
            history.shape[0],
            history.strides[0],
            QImage.Format.Format_RGB888,
        )
        painter.drawImage(waterfall, image)

        painter.setPen(QColor("#b9c8d5"))
        font = painter.font()
        font.setPixelSize(10)
        painter.setFont(font)
        painter.drawText(4, int(scope.top() + 10), "−20")
        painter.drawText(4, int(scope.center().y() + 4), "−60")
        painter.drawText(4, int(scope.bottom()), "−100 dB")
        for index in range(ticks + 1):
            x = scope.left() + scope.width() * index / ticks
            hz = int(self.max_frequency * index / ticks)
            label = f"{hz // 1000}k" if hz and hz % 1000 == 0 else str(hz)
            painter.drawText(
                QRectF(x - 25, axis_y + 5, 50, 18),
                Qt.AlignmentFlag.AlignHCenter,
                label,
            )
        painter.drawText(
            QRectF(scope.right() - 90, axis_y + 5, 90, 18),
            Qt.AlignmentFlag.AlignRight,
            "audio Hz",
        )


class SpectrumWindow(QMainWindow):
    """Persistent floating monitor associated with the Guardian main window."""

    def __init__(
        self,
        runtime,
        settings: QSettings,
        parent: QWidget | None = None,
        *,
        auto_start_audio: bool = True,
    ):
        super().__init__(parent, Qt.WindowType.Window)
        self.runtime = runtime
        self.settings = settings
        self.auto_start_audio = auto_start_audio
        self.monitor = AudioMonitor(runtime.config.audio_input)
        self.analyzer = SpectrumAnalyzer()
        self._really_closing = False
        self._paused = False

        self.setWindowTitle(dual("Guardian — VARA spectrum", "Guardian — spektrum VARA"))
        from PySide6.QtGui import QIcon

        self.setWindowIcon(QIcon(str(get_ico_path())))
        self.setMinimumSize(720, 500)
        self.resize(980, 640)
        self._build_ui()
        self._restore_geometry()

        self.timer = QTimer(self)
        self.timer.setInterval(160)
        self.timer.timeout.connect(self.refresh)

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)
        self.setCentralWidget(root)

        header = QHBoxLayout()
        self.rx_card = ValueCard(dual("RECEIVE", "PŘÍJEM"), "#45d6db")
        self.tx_card = ValueCard(dual("TRANSMIT", "VYSÍLÁNÍ"), "#ff8a65")
        self.mode_card = ValueCard(dual("VARA MODE", "REŽIM VARA"), "#dfc45b")
        self.link_card = ValueCard(dual("LINK", "SPOJENÍ"), "#70e2a2")
        for card in (self.rx_card, self.tx_card, self.mode_card, self.link_card):
            header.addWidget(card, 1)
        outer.addLayout(header)

        self.scope = WaterfallScope()
        outer.addWidget(self.scope, 1)
        footer = QHBoxLayout()
        self.status = QLabel()
        self.status.setObjectName("Metadata")
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        self.pause_button = QPushButton(dual("Pause", "Pozastavit"))
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(self._set_paused)
        clear_button = QPushButton(dual("Clear", "Vymazat"))
        clear_button.clicked.connect(self.scope.clear)
        footer.addWidget(self.pause_button)
        footer.addWidget(clear_button)
        outer.addLayout(footer)

    def set_tokens(self, tokens: ThemeTokens) -> None:
        self.scope.set_tokens(tokens)

    def refresh(self) -> None:
        snapshot = self.runtime.snapshots.read()
        config = self.runtime.config
        if (
            self.isVisible()
            and self.auto_start_audio
            and config.audio_input != self.monitor.device_name
        ):
            self.monitor.stop()
            self.monitor = AudioMonitor(config.audio_input)
            self.monitor.start()
        self.scope.set_mode(config.vara_mode)
        frequency = snapshot.radio.freq_mhz()
        self.rx_card.value.setText(frequency)
        self.tx_card.value.setText(
            f"● {frequency}" if snapshot.radio.ptt else frequency
        )
        self.tx_card.setProperty("active", bool(snapshot.radio.ptt))
        self.mode_card.value.setText(f"{config.vara_mode.upper()} · P2P")
        self.link_card.value.setText(snapshot.vara.link_state)

        ready = self.analyzer.take_ready()
        if ready is not None and not self._paused:
            self.scope.push_db(*ready)
        samples = self.monitor.take_samples()
        if samples is not None and not self._paused:
            bins = min(512, max(200, self.scope.width() - 2))
            self.analyzer.submit(
                samples,
                self.monitor.sample_rate,
                self.scope.max_frequency,
                bins,
            )
        if self.monitor.running:
            device = self.monitor.actual_device_name
            status = (
                dual(
                    f"Live RX audio · {device} · input-only monitor",
                    f"Živý RX zvuk · {device} · monitor pouze pro vstup",
                )
            )
            if self._paused:
                status += dual(" · display paused", " · zobrazení pozastaveno")
            self.status.setText(status)
        else:
            detail = self.monitor.error or dual(
                "audio monitor is stopped", "zvukový monitor je zastaven"
            )
            self.status.setText(
                dual(
                    f"No live spectrum: {detail}. Radio and VARA operation are unaffected.",
                    f"Živé spektrum není dostupné: {detail}. Provoz rádia a VARA není ovlivněn.",
                )
            )

    def _set_paused(self, paused: bool) -> None:
        self._paused = paused
        self.pause_button.setText(
            dual("Resume", "Pokračovat") if paused else dual("Pause", "Pozastavit")
        )
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.timer.start()
        if self.auto_start_audio:
            current_device = self.runtime.config.audio_input
            if current_device != self.monitor.device_name:
                self.monitor.stop()
                self.monitor = AudioMonitor(current_device)
            self.monitor.start()
        self.refresh()

    def hideEvent(self, event) -> None:
        self.timer.stop()
        self.monitor.stop()
        super().hideEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.settings.setValue("ui/spectrum_geometry", self.saveGeometry())
        self.settings.sync()
        self.monitor.stop()
        if self._really_closing:
            super().closeEvent(event)
        else:
            event.ignore()
            self.hide()

    def shutdown(self) -> None:
        self._really_closing = True
        self.analyzer.shutdown()
        self.close()

    def _restore_geometry(self) -> None:
        geometry = self.settings.value("ui/spectrum_geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
