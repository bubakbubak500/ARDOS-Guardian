"""Compatibility facade for the SC-FTN benchmark helpers.

Benchmarking belongs beside the production waveform in
``guardian.waveforms.bench``.  The historical module path remains importable for
the audio/UI callers that used it, but it contains no alternate modem or OFDM
simulation.
"""

from __future__ import annotations

import math

from ..waveforms.bench import (
    BurstResult,
    CaptureResult,
    TransferResult,
    WaveformFacts,
    decode_capture,
    describe,
    make_test_burst,
    read_wav,
    run_burst,
    run_transfer,
    write_wav,
)
from .config import OfdmProfile


def uncoded_ber(modulation: str, snr_db: float) -> float:
    """Return the SC benchmark's theory placeholder.

    FTN pulse interference makes the old orthogonal-constellation formula
    misleading.  Measured decoder results are reported by ``run_burst`` instead.
    """
    del modulation, snr_db
    return math.nan


__all__ = [
    "BurstResult", "CaptureResult", "OfdmProfile", "TransferResult",
    "WaveformFacts", "decode_capture", "describe", "make_test_burst",
    "read_wav", "run_burst", "run_transfer", "uncoded_ber", "write_wav",
]
