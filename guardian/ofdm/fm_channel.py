"""Deterministic end-to-end narrowband FM channel for modem experiments.

This model is intentionally separate from :mod:`guardian.ofdm.channel`: results
from a linear audio path and from an FM modulator/limiter/discriminator must
never be labelled as if they measured the same thing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..waveforms.config import WaveformProfile


@dataclass(frozen=True)
class FmChannelSpec:
    peak_deviation_hz: float = 2500.0
    rf_snr_db: float | None = 30.0
    tx_audio_low_hz: float = 250.0
    tx_audio_high_hz: float = 3000.0
    rx_audio_low_hz: float = 250.0
    rx_audio_high_hz: float = 3000.0
    limiter_level: float = 0.95
    preemphasis_tau_us: float = 0.0
    deemphasis_tau_us: float = 0.0
    multipath: tuple[tuple[int, complex], ...] = ()
    rx_agc_rms: float | None = 0.18
    rx_clip: float | None = None
    ppm: float = 0.0


@dataclass
class FmChannelMetrics:
    model: str = "end-to-end-fm"
    input_peak: float = 0.0
    limited_samples: int = 0
    limiter_fraction: float = 0.0
    achieved_peak_deviation_hz: float = 0.0
    rf_snr_db: float | None = None
    output_rms: float = 0.0
    output_peak: float = 0.0
    clipped_samples: int = 0


@dataclass
class FmChannel:
    profile: WaveformProfile
    spec: FmChannelSpec = field(default_factory=FmChannelSpec)
    seed: int = 0xF2
    last_metrics: FmChannelMetrics = field(default_factory=FmChannelMetrics)

    def __post_init__(self) -> None:
        self._rng = np.random.default_rng(self.seed)

    def reset(self) -> None:
        self._rng = np.random.default_rng(self.seed)

    def __call__(self, samples) -> np.ndarray:
        x = np.asarray(samples, dtype=np.float64).reshape(-1)
        fs = float(self.profile.sample_rate)
        metrics = FmChannelMetrics(rf_snr_db=self.spec.rf_snr_db)
        metrics.input_peak = float(np.max(np.abs(x))) if len(x) else 0.0
        if not len(x):
            self.last_metrics = metrics
            return x.copy()

        audio = _bandpass(
            x, fs, self.spec.tx_audio_low_hz, self.spec.tx_audio_high_hz
        )
        if self.spec.preemphasis_tau_us > 0.0:
            audio = _preemphasis(audio, fs, self.spec.preemphasis_tau_us * 1e-6)
        limit = max(1e-6, float(self.spec.limiter_level))
        metrics.limited_samples = int(np.count_nonzero(np.abs(audio) > limit))
        metrics.limiter_fraction = metrics.limited_samples / max(1, len(audio))
        audio = np.clip(audio, -limit, limit) / limit
        metrics.achieved_peak_deviation_hz = (
            float(np.max(np.abs(audio))) * self.spec.peak_deviation_hz
        )

        phase = np.cumsum(
            2.0 * np.pi * self.spec.peak_deviation_hz * audio / fs
        )
        rf = np.exp(1j * phase)
        if self.spec.multipath:
            span = max(delay for delay, _gain in self.spec.multipath)
            mixed = np.zeros(len(rf) + span, dtype=np.complex128)
            for delay, gain in self.spec.multipath:
                mixed[int(delay):int(delay) + len(rf)] += complex(gain) * rf
            rf = mixed
        if self.spec.rf_snr_db is not None:
            sigma = 10.0 ** (-float(self.spec.rf_snr_db) / 20.0) / np.sqrt(2.0)
            rf = rf + sigma * (
                self._rng.standard_normal(len(rf))
                + 1j * self._rng.standard_normal(len(rf))
            )

        angle = np.angle(rf[1:] * np.conj(rf[:-1]))
        recovered = np.concatenate([[0.0], angle]) * fs / (
            2.0 * np.pi * max(1e-6, self.spec.peak_deviation_hz)
        )
        if self.spec.deemphasis_tau_us > 0.0:
            recovered = _deemphasis(
                recovered, fs, self.spec.deemphasis_tau_us * 1e-6
            )
        recovered = _bandpass(
            recovered, fs, self.spec.rx_audio_low_hz, self.spec.rx_audio_high_hz
        )
        if self.spec.ppm:
            recovered = _clock_error(recovered, self.spec.ppm)
        if self.spec.rx_agc_rms is not None:
            rms = float(np.sqrt(np.mean(recovered ** 2)))
            if rms > 1e-12:
                recovered *= float(self.spec.rx_agc_rms) / rms
        if self.spec.rx_clip is not None:
            ceiling = max(1e-6, float(self.spec.rx_clip))
            metrics.clipped_samples = int(
                np.count_nonzero(np.abs(recovered) > ceiling)
            )
            recovered = np.clip(recovered, -ceiling, ceiling)
        metrics.output_rms = float(np.sqrt(np.mean(recovered ** 2)))
        metrics.output_peak = float(np.max(np.abs(recovered)))
        self.last_metrics = metrics
        return recovered.astype(np.float64)


def _bandpass(samples: np.ndarray, fs: float, low: float, high: float) -> np.ndarray:
    if len(samples) < 4:
        return samples.copy()
    spectrum = np.fft.rfft(samples)
    freq = np.fft.rfftfreq(len(samples), 1.0 / fs)
    low = max(0.0, float(low))
    high = min(fs / 2.0, max(low + 1.0, float(high)))
    transition = min(100.0, max(20.0, (high - low) * 0.08))
    gain = np.ones_like(freq)
    if low > 0.0:
        gain = np.clip((freq - max(0.0, low - transition)) / transition, 0.0, 1.0)
    gain *= np.clip((high + transition - freq) / transition, 0.0, 1.0)
    return np.fft.irfft(spectrum * gain, len(samples))


def _preemphasis(samples: np.ndarray, fs: float, tau: float) -> np.ndarray:
    alpha = np.exp(-1.0 / max(1.0, fs * tau))
    out = np.empty_like(samples)
    out[0] = samples[0]
    out[1:] = samples[1:] - alpha * samples[:-1]
    scale = max(float(np.max(np.abs(out))), 1e-12)
    return out / scale * max(float(np.max(np.abs(samples))), 1e-12)


def _deemphasis(samples: np.ndarray, fs: float, tau: float) -> np.ndarray:
    alpha = np.exp(-1.0 / max(1.0, fs * tau))
    out = np.empty_like(samples)
    out[0] = samples[0]
    for index in range(1, len(samples)):
        out[index] = alpha * out[index - 1] + (1.0 - alpha) * samples[index]
    return out


def _clock_error(samples: np.ndarray, ppm: float) -> np.ndarray:
    ratio = 1.0 + float(ppm) * 1e-6
    source = np.arange(len(samples), dtype=np.float64)
    target = np.arange(0.0, len(samples), ratio, dtype=np.float64)
    return np.interp(target, source, samples)


__all__ = ["FmChannel", "FmChannelMetrics", "FmChannelSpec"]
