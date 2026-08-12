"""Constellations specific to Guardian's experimental waveform families.

The established OFDM mapper intentionally remains limited to its four measured
modulations.  New single-carrier and SEFDM experiments use this separate mapper
so adding 256-QAM or APSK cannot silently change what the OFDM modem accepts.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np

from ..ofdm.constellation import gray_to_binary
from .shaping import (PAS64_INPUT_BITS, PAS64_SYMBOLS, demap_pas64,
                      map_pas64)

BITS_PER_SYMBOL: dict[str, int] = {
    "bpsk": 1,
    "qpsk": 2,
    "psk8": 3,
    "qam16": 4,
    "qam32": 5,
    "qam64": 6,
    "qam128": 7,
    "qam256": 8,
    "qam512": 9,
    "qam1024": 10,
    "apsk16": 4,
    "apsk32": 5,
    "apsk64": 6,
    "apsk128": 7,
    "apsk256": 8,
    "apsk512": 9,
    "gqam16": 4,
    "gqam64": 6,
    "gqam256": 8,
    "gqam1024": 10,
}


def bits_per_symbol(modulation: str) -> int:
    if modulation == "pas64":
        # Nominal constellation order; framing uses `coded_capacity` for the
        # matcher's exact 86/16 rate.
        return 6
    try:
        return BITS_PER_SYMBOL[modulation]
    except KeyError:
        raise ValueError(f"unknown experimental modulation {modulation!r}") from None


def _pam_levels(nbits: int) -> np.ndarray:
    count = 1 << nbits
    natural = gray_to_binary(np.arange(count))
    return (2 * natural - (count - 1)).astype(np.float64)


def _rectangular_qam(m: int) -> np.ndarray:
    """Gray-labelled rectangular QAM, including odd-bit 32/128/512 orders."""
    qbits = m // 2
    ibits = m - qbits
    ilevels = _pam_levels(ibits)
    qlevels = _pam_levels(qbits)
    index = np.arange(1 << m)
    return ilevels[index >> qbits] + 1j * qlevels[index & ((1 << qbits) - 1)]


def _geometric_qam(m: int, exponent: float) -> np.ndarray:
    """Non-equidistant Gray QAM candidate for a compressed FM/audio path.

    The sign and Gray ordering are unchanged, but large I/Q amplitudes are
    pulled inward by a fixed power law before RMS normalisation.  This lowers
    crest factor and gives the radio's nonlinear outer region less leverage.
    It is deliberately a separate on-air MCS: measurements, not an AWGN claim,
    decide whether the lower PAPR is worth the reduced outer-point spacing.
    """
    points = _rectangular_qam(m)
    real = np.sign(points.real) * np.abs(points.real) ** exponent
    imag = np.sign(points.imag) * np.abs(points.imag) ** exponent
    return real + 1j * imag


def _apsk(ring_counts: tuple[int, ...], ring_radii: tuple[float, ...]) -> np.ndarray:
    """Deterministic multi-ring APSK candidate for measured radio comparison.

    The profiles are intentionally versioned fixed geometries, not a standards
    claim.  A half-step rotation on alternating rings keeps radial neighbours
    apart and the common RMS normalisation below makes fair drive tests possible.
    """
    rings = []
    for ring, (count, radius) in enumerate(zip(ring_counts, ring_radii, strict=True)):
        offset = (ring & 1) * np.pi / count
        rings.append(radius * np.exp(1j * (offset + 2.0 * np.pi * np.arange(count) / count)))
    return np.concatenate(rings)


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
    elif modulation == "apsk64":
        points = _apsk((4, 12, 20, 28), (1.0, 2.25, 3.55, 4.90))
    elif modulation == "apsk128":
        points = _apsk(
            (4, 12, 20, 28, 32, 32),
            (1.0, 2.20, 3.35, 4.50, 5.65, 6.80),
        )
    elif modulation == "apsk256":
        points = _apsk(
            (4, 12, 20, 28, 36, 44, 52, 60),
            (1.0, 2.10, 3.15, 4.20, 5.25, 6.30, 7.35, 8.40),
        )
    elif modulation == "apsk512":
        points = _apsk(
            (4, 12, 20, 28, 36, 44, 52, 60, 68, 76, 52, 60),
            (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0),
        )
    elif modulation.startswith("gqam"):
        points = _geometric_qam(m, 0.86)
    elif modulation == "psk8":
        # Binary indices are converted from Gray to natural phase order so
        # nearest angular neighbours differ by one bit.
        natural = gray_to_binary(np.arange(8))
        points = np.exp(2j * np.pi * natural / 8.0)
    elif m == 1:
        points = np.array([-1.0, 1.0], dtype=np.complex128)
    else:
        points = _rectangular_qam(m)
    if len(points) != 1 << m:
        raise ValueError(f"{modulation} geometry has {len(points)} rather than {1 << m} points")
    return points / np.sqrt(np.mean(np.abs(points) ** 2))


@lru_cache(maxsize=None)
def _bit_masks(modulation: str) -> tuple[np.ndarray, ...]:
    m = bits_per_symbol(modulation)
    index = np.arange(1 << m)
    return tuple(((index >> (m - 1 - j)) & 1).astype(bool) for j in range(m))


def map_bits(bits, modulation: str) -> np.ndarray:
    if modulation == "pas64":
        return map_pas64(bits)
    m = bits_per_symbol(modulation)
    flat = np.asarray(bits, dtype=np.int64).reshape(-1)
    if len(flat) % m:
        raise ValueError(f"{len(flat)} bits do not fill {modulation} symbols")
    weights = 1 << np.arange(m - 1, -1, -1)
    indices = (flat.reshape(-1, m) * weights).sum(axis=1)
    return constellation(modulation)[indices]


def demap_llr(symbols, modulation: str, noise_var=1.0) -> np.ndarray:
    if modulation == "pas64":
        return demap_pas64(symbols, noise_var)
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


def reliability_gated_refine(symbols, modulation: str,
                             iterations: int = 1) -> np.ndarray:
    """Bounded decision-directed complex gain/phase correction.

    Only the half of symbols with the clearest nearest-vs-second-nearest margin
    participate in the least-squares estimate.  Ambiguous decisions therefore
    cannot drag a fading burst toward the wrong constellation.  The correction
    is deliberately limited; it complements, rather than replaces, the trained
    channel equalizer and is safe to disable by passing zero iterations.
    """
    values = np.asarray(symbols, dtype=np.complex128).copy()
    shape = values.shape
    values = values.reshape(-1)
    if modulation == "pas64":
        return values.reshape(shape)
    points = constellation(modulation)
    if len(values) < 8:
        return values.reshape(shape)
    for _ in range(max(0, min(4, int(iterations)))):
        distance = np.abs(values[:, None] - points[None, :]) ** 2
        nearest_index = distance.argmin(axis=1)
        nearest_distance = distance[np.arange(len(values)), nearest_index]
        second_distance = np.partition(distance, 1, axis=1)[:, 1]
        margin = second_distance - nearest_distance
        threshold = float(np.quantile(margin, 0.50))
        reliable = margin >= threshold
        reference = points[nearest_index]
        denominator = np.vdot(reference[reliable], reference[reliable]).real
        if denominator <= np.finfo(np.float64).tiny:
            break
        gain = np.vdot(reference[reliable], values[reliable]) / denominator
        magnitude = abs(gain)
        phase = abs(float(np.angle(gain)))
        if not np.isfinite(magnitude) or not 0.70 <= magnitude <= 1.40 or phase > 0.35:
            break
        values /= gain
    return values.reshape(shape)


def evm(symbols, modulation: str) -> float:
    values = np.asarray(symbols, dtype=np.complex128).reshape(-1)
    if not len(values):
        return float("nan")
    points = constellation(modulation)
    nearest = points[np.abs(values[:, None] - points[None, :]).argmin(axis=1)]
    return float(np.sqrt(np.mean(np.abs(values - nearest) ** 2)
                         / np.mean(np.abs(nearest) ** 2)))


def coded_capacity(points: int, modulation: str) -> int:
    if modulation == "pas64":
        return (max(0, int(points)) // PAS64_SYMBOLS) * PAS64_INPUT_BITS
    return max(0, int(points)) * bits_per_symbol(modulation)


def mapped_symbol_count(bit_count: int, modulation: str) -> int:
    if modulation == "pas64":
        return int(np.ceil(max(0, int(bit_count)) / PAS64_INPUT_BITS)) * PAS64_SYMBOLS
    width = bits_per_symbol(modulation)
    return int(np.ceil(max(0, int(bit_count)) / width))
