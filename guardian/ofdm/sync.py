"""SC-FTN acquisition helpers kept at the historical import path.

The production receiver uses a matched correlation against its deterministic
single-carrier prefix.  These small wrappers preserve the old ``sync`` imports
for backend code while making the physical-layer contract explicit: every
profile must be a ``WaveformProfile`` with SC-FTN geometry.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..waveforms.config import WaveformProfile
from ..waveforms.phy import (
    SingleCarrierModulator,
    correlation_candidates,
)


@dataclass(frozen=True)
class SyncResult:
    """One candidate SC-FTN prefix in an audio capture."""

    burst_start: int
    confidence: float
    cfo_hz: float
    samples: np.ndarray
    audio_rms: float
    crest_factor_db: float


MAX_CANDIDATES = 4


def _validate(profile: WaveformProfile) -> None:
    if not profile.is_single_carrier or profile.family != "sc_ftn":
        raise ValueError("SC-FTN is the only supported waveform family")


def burst_head(profile: WaveformProfile,
               modulator: SingleCarrierModulator | None = None) -> np.ndarray:
    """Return the known SC-FTN prefix used for matched acquisition."""
    _validate(profile)
    return (modulator or SingleCarrierModulator(profile)).reference()


def repetition_metric(samples, profile: WaveformProfile
                      ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return matched-prefix score, correlation and window energy.

    The tuple retains the historical detector shape.  Its first element is a
    normalized SC-FTN prefix correlation rather than an OFDM repetition metric.
    """
    reference = burst_head(profile)
    signal = np.asarray(samples, dtype=np.float64)
    count = len(signal) - len(reference) + 1
    if count < 1:
        return (np.zeros(0), np.zeros(0), np.zeros(0))
    size = 1 << (len(signal) + len(reference) - 2).bit_length()
    convolution = np.fft.irfft(
        np.fft.rfft(signal, size) * np.fft.rfft(reference[::-1], size), size
    )
    correlation = convolution[len(reference) - 1:len(signal)]
    energy = np.cumsum(np.concatenate([[0.0], signal * signal]))
    windows = energy[len(reference):] - energy[:-len(reference)]
    ref_energy = float(np.dot(reference, reference))
    score = np.abs(correlation) / np.sqrt(
        np.maximum(windows * ref_energy, 1e-20)
    )
    return score, correlation, windows


def candidates(samples, profile: WaveformProfile,
               modulator: SingleCarrierModulator | None = None,
               limit: int = MAX_CANDIDATES) -> list[SyncResult]:
    """Return plausible SC-FTN prefix starts in time order."""
    _validate(profile)
    signal = np.asarray(samples, dtype=np.float64)
    modem = modulator or SingleCarrierModulator(profile)
    starts = correlation_candidates(
        signal, modem.reference(), threshold=0.20, limit=limit
    )
    scores, _, _ = repetition_metric(signal, profile)
    rms = float(np.sqrt(np.mean(signal * signal))) if len(signal) else 0.0
    peak = float(np.max(np.abs(signal))) if len(signal) else 0.0
    crest = (float(20.0 * np.log10(peak / rms))
             if rms > 0.0 and peak > 0.0 else float("nan"))
    return [SyncResult(
        burst_start=start,
        confidence=float(scores[start]) if start < len(scores) else 0.0,
        cfo_hz=0.0,
        samples=signal,
        audio_rms=rms,
        crest_factor_db=crest,
    ) for start in starts]


def detect(samples, profile: WaveformProfile,
           modulator: SingleCarrierModulator | None = None
           ) -> SyncResult | None:
    """Return the earliest plausible SC-FTN prefix, if one is present."""
    found = candidates(samples, profile, modulator, limit=1)
    return found[0] if found else None


__all__ = [
    "MAX_CANDIDATES", "SyncResult", "burst_head", "candidates", "detect",
    "repetition_metric",
]
