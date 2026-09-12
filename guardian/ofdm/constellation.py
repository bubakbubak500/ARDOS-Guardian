"""Historical constellation import facade for SC-FTN.

Constellation generation and soft demapping live in
``guardian.waveforms.constellation``.  Keeping these aliases avoids breaking
shared framing/backend imports without retaining a second, incompatible
constellation implementation.
"""

from __future__ import annotations

import numpy as np

from ..waveforms.constellation import (
    BITS_PER_SYMBOL,
    bits_per_symbol,
    coded_capacity,
    constellation,
    demap_llr,
    evm,
    map_bits,
    mapped_symbol_count,
    reliability_gated_refine,
    gray_to_binary,
)

MODULATIONS = tuple(BITS_PER_SYMBOL)


def demap_hard(symbols, modulation: str) -> np.ndarray:
    """Return hard bits using the production SC-FTN constellation."""
    points = constellation(modulation)
    values = np.asarray(symbols, dtype=np.complex128).reshape(-1)
    indices = np.argmin(np.abs(values[:, None] - points[None, :]) ** 2, axis=1)
    bits = int(bits_per_symbol(modulation))
    return ((indices[:, None] >> np.arange(bits - 1, -1, -1)) & 1).astype(np.int8).reshape(-1)


__all__ = [
    "BITS_PER_SYMBOL", "MODULATIONS", "bits_per_symbol", "coded_capacity",
    "constellation", "demap_hard", "demap_llr", "evm", "gray_to_binary",
    "map_bits", "mapped_symbol_count", "reliability_gated_refine",
]
