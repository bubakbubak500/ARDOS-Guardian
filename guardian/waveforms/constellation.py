"""Constellations specific to Guardian's experimental waveform families.

The established OFDM mapper intentionally remains limited to its four measured
modulations.  New single-carrier and SEFDM experiments use this separate mapper
so adding 256-QAM or APSK cannot silently change what the OFDM modem accepts.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np

from ..ofdm.constellation import gray_to_binary

BITS_PER_SYMBOL: dict[str, int] = {
    "bpsk": 1,
    "qpsk": 2,
    "qam16": 4,
    "qam64": 6,
    "qam256": 8,
    "apsk16": 4,
    "apsk32": 5,
}


def bits_per_symbol(modulation: str) -> int:
    try:
        return BITS_PER_SYMBOL[modulation]
    except KeyError:
        raise ValueError(f"unknown experimental modulation {modulation!r}") from None


def _pam_levels(nbits: int) -> np.ndarray:
    count = 1 << nbits
    natural = gray_to_binary(np.arange(count))
    return (2 * natural - (count - 1)).astype(np.float64)


@lru_cache(maxsize=None)
def constellation(modulation: str) -> np.ndarray:
    m = bits_per_symbol(modulation)
    if modulation == "apsk16":
        inner = np.exp(1j * (np.pi / 4 + np.arange(4) * np.pi / 2))
        outer = 2.85 * np.exp(1j * (np.pi / 12 + np.arange(12) * np.pi / 6))
        points = np.concatenate([inner, outer])
    elif modulation == "apsk32":
        inner = np.exp(1j * (np.pi / 4 + np.arange(4) * np.pi / 2))
        middle = 2.54 * np.exp(1j * (np.pi / 12 + np.arange(12) * np.pi / 6))
        outer = 4.33 * np.exp(1j * (np.pi / 16 + np.arange(16) * np.pi / 8))
        points = np.concatenate([inner, middle, outer])
    elif m == 1:
        points = np.array([-1.0, 1.0], dtype=np.complex128)
    else:
        half = m // 2
        levels = _pam_levels(half)
        index = np.arange(1 << m)
        points = levels[index >> half] + 1j * levels[index & ((1 << half) - 1)]
    return points / np.sqrt(np.mean(np.abs(points) ** 2))


@lru_cache(maxsize=None)
def _bit_masks(modulation: str) -> tuple[np.ndarray, ...]:
    m = bits_per_symbol(modulation)
    index = np.arange(1 << m)
    return tuple(((index >> (m - 1 - j)) & 1).astype(bool) for j in range(m))


def map_bits(bits, modulation: str) -> np.ndarray:
    m = bits_per_symbol(modulation)
    flat = np.asarray(bits, dtype=np.int64).reshape(-1)
    if len(flat) % m:
        raise ValueError(f"{len(flat)} bits do not fill {modulation} symbols")
    weights = 1 << np.arange(m - 1, -1, -1)
    indices = (flat.reshape(-1, m) * weights).sum(axis=1)
    return constellation(modulation)[indices]


def demap_llr(symbols, modulation: str, noise_var=1.0) -> np.ndarray:
    m = bits_per_symbol(modulation)
    values = np.asarray(symbols, dtype=np.complex128).reshape(-1)
    variance = np.asarray(noise_var, dtype=np.float64)
    if variance.ndim:
        variance = variance.reshape(-1)
        if len(variance) != len(values):
            raise ValueError("noise_var must be scalar or one value per symbol")
    variance = np.maximum(variance, np.finfo(np.float64).tiny)
    distance = np.abs(values[:, None] - constellation(modulation)[None, :]) ** 2
    llr = np.empty((len(values), m), dtype=np.float64)
    for bit, ones in enumerate(_bit_masks(modulation)):
        llr[:, bit] = distance[:, ~ones].min(axis=1) - distance[:, ones].min(axis=1)
    llr = llr / (variance[:, None] if variance.ndim else variance)
    return llr.reshape(-1)


def evm(symbols, modulation: str) -> float:
    values = np.asarray(symbols, dtype=np.complex128).reshape(-1)
    if not len(values):
        return float("nan")
    points = constellation(modulation)
    nearest = points[np.abs(values[:, None] - points[None, :]).argmin(axis=1)]
    return float(np.sqrt(np.mean(np.abs(values - nearest) ** 2)
                         / np.mean(np.abs(nearest) ** 2)))
