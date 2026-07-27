import numpy as np
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from guardian.qt.runtime import ShellRuntime
from guardian.qt.spectrum_window import (
    AudioMonitor,
    SpectrumWindow,
    spectrum_db,
    waterfall_colors,
)
from guardian.services import RadioSnapshot


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_spectrum_detects_audio_tone() -> None:
    sample_rate = 48_000
    frequency = 1_500
    time = np.arange(8192) / sample_rate
    samples = np.sin(2 * np.pi * frequency * time).astype(np.float32)
    values = spectrum_db(samples, sample_rate, 6_000, 601)
    peak_hz = int(np.argmax(values)) * 10
    assert abs(peak_hz - frequency) <= 20
    assert values.max() > -20


def test_waterfall_palette_gets_brighter_with_signal() -> None:
    colors = waterfall_colors(np.asarray([-100.0, -60.0, -20.0]))
    assert colors.shape == (3, 3)
    assert colors[-1].sum() > colors[0].sum()


def test_audio_monitor_requires_an_explicit_radio_input() -> None:
    monitor = AudioMonitor("")
    monitor.start()
    assert not monitor.running
    assert monitor.error


def test_window_reflects_radio_and_vara_state_without_audio(tmp_path) -> None:
    _application()
    settings = QSettings(str(tmp_path / "spectrum.ini"), QSettings.Format.IniFormat)
    runtime = ShellRuntime()
    runtime.config.vara_mode = "FM"
    window = SpectrumWindow(runtime, settings, auto_start_audio=False)
    try:
        window.refresh()
        assert "P2P" in window.mode_card.value.text()
        assert window.scope.max_frequency == 6_000
        assert "No live spectrum" in window.status.text()
        runtime.snapshots.update(
            radio=RadioSnapshot(frequency_hz=145_500_000, ptt=True)
        )
        window.refresh()
        assert window.rx_card.value.text() == "145.50000 MHz"
        assert window.tx_card.value.text() == "● 145.50000 MHz"
        window.pause_button.setChecked(True)
        assert window._paused
        assert window.pause_button.text()
    finally:
        window.shutdown()
        runtime.close()
