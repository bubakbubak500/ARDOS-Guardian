"""Small DSP utilities shared by the SC-FTN audio path.

The former G2 module also contained an OFDM modulator and receiver.  Those
waveform-specific classes are intentionally absent from the G1 port; the
single-carrier implementation lives in :mod:`guardian.waveforms.phy`.
"""

from __future__ import annotations

import numpy as np


def analytic(x) -> np.ndarray:
    """Return the analytic signal ``x + j*hilbert(x)`` using NumPy's FFT."""
    values = np.asarray(x, dtype=np.float64)
    count = len(values)
    if count == 0:
        return np.zeros(0, dtype=np.complex128)
    weight = np.zeros(count, dtype=np.float64)
    weight[0] = 1.0
    if count % 2 == 0:
        weight[1:count // 2] = 2.0
        weight[count // 2] = 1.0
    else:
        weight[1:(count + 1) // 2] = 2.0
    return np.fft.ifft(np.fft.fft(values) * weight)


__all__ = ["analytic"]
