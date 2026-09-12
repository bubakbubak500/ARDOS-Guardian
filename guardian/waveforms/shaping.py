"""Bounded constant-composition probabilistic amplitude shaping for 64-QAM.

This is an experimental, fully reversible distribution matcher rather than a
cosmetic rescaling of QAM points.  Every 16 complex symbols use 32 real axes
with the fixed amplitude composition 12×1, 8×3, 6×5 and 6×7.  Fifty-four input
bits select one multiset permutation and 32 further bits select signs, yielding
86 information bits per 16 symbols (5.375 bit/symbol before FEC).
"""

from __future__ import annotations

import math

import numpy as np

PAS64_SYMBOLS = 16
PAS64_COMPOSITION = (12, 8, 6, 6)
PAS64_RANK_BITS = 54
PAS64_SIGN_BITS = 32
PAS64_INPUT_BITS = PAS64_RANK_BITS + PAS64_SIGN_BITS
_AMPLITUDES = np.asarray((1.0, 3.0, 5.0, 7.0))
_NORMALISATION = math.sqrt(
    2.0 * sum(count * amplitude * amplitude
              for count, amplitude in zip(PAS64_COMPOSITION, _AMPLITUDES))
    / sum(PAS64_COMPOSITION)
)


def _permutations(counts: tuple[int, ...]) -> int:
    total = sum(counts)
    value = math.factorial(total)
    for count in counts:
        value //= math.factorial(count)
    return value


def _unrank(rank: int) -> np.ndarray:
    counts = list(PAS64_COMPOSITION)
    output: list[int] = []
    for _ in range(sum(counts)):
        for symbol, count in enumerate(counts):
            if not count:
                continue
            counts[symbol] -= 1
            ways = _permutations(tuple(counts))
            if rank < ways:
                output.append(symbol)
                break
            rank -= ways
            counts[symbol] += 1
        else:  # pragma: no cover - protected by the rank bound
            raise ValueError("PAS rank exceeds the composition")
    return np.asarray(output, dtype=np.int8)


def _rank(sequence) -> int:
    counts = list(PAS64_COMPOSITION)
    rank = 0
    for selected in np.asarray(sequence, dtype=np.int8).reshape(-1):
        for symbol in range(int(selected)):
            if counts[symbol]:
                counts[symbol] -= 1
                rank += _permutations(tuple(counts))
                counts[symbol] += 1
        if selected < 0 or selected >= len(counts) or not counts[int(selected)]:
            raise ValueError("sequence is outside the PAS composition")
        counts[int(selected)] -= 1
    return rank


def _bits_to_int(bits) -> int:
    value = 0
    for bit in np.asarray(bits, dtype=np.int8).reshape(-1):
        value = (value << 1) | (int(bit) & 1)
    return value


def _int_to_bits(value: int, width: int) -> np.ndarray:
    return np.asarray([(value >> shift) & 1 for shift in range(width - 1, -1, -1)],
                      dtype=np.int8)


def map_pas64(bits) -> np.ndarray:
    raw = np.asarray(bits, dtype=np.int8).reshape(-1)
    if len(raw) % PAS64_INPUT_BITS:
        raise ValueError("PAS64 input must contain whole 86-bit matcher blocks")
    output: list[np.ndarray] = []
    for start in range(0, len(raw), PAS64_INPUT_BITS):
        block = raw[start:start + PAS64_INPUT_BITS]
        amplitudes = _AMPLITUDES[_unrank(_bits_to_int(block[:PAS64_RANK_BITS]))]
        signs = np.where(block[PAS64_RANK_BITS:] != 0, 1.0, -1.0)
        axes = amplitudes * signs / _NORMALISATION
        output.append(axes[0::2] + 1j * axes[1::2])
    return np.concatenate(output) if output else np.zeros(0, dtype=np.complex128)


def demap_pas64(symbols, noise_var=1.0) -> np.ndarray:
    values = np.asarray(symbols, dtype=np.complex128).reshape(-1)
    if len(values) % PAS64_SYMBOLS:
        raise ValueError("PAS64 symbols must contain whole 16-symbol matcher blocks")
    variance = max(float(np.mean(np.asarray(noise_var, dtype=np.float64))), 1e-9)
    output: list[np.ndarray] = []
    for start in range(0, len(values), PAS64_SYMBOLS):
        block = values[start:start + PAS64_SYMBOLS] * _NORMALISATION
        axes = np.empty(PAS64_SIGN_BITS, dtype=np.float64)
        axes[0::2], axes[1::2] = block.real, block.imag
        order = np.argsort(np.abs(axes), kind="stable")
        labels = np.empty(PAS64_SIGN_BITS, dtype=np.int8)
        cursor = 0
        for label, count in enumerate(PAS64_COMPOSITION):
            labels[order[cursor:cursor + count]] = label
            cursor += count
        rank = _rank(labels) & ((1 << PAS64_RANK_BITS) - 1)
        rank_bits = _int_to_bits(rank, PAS64_RANK_BITS)
        sign_bits = (axes >= 0.0).astype(np.int8)
        hard = np.concatenate([rank_bits, sign_bits])
        distances = np.sort(
            (np.abs(axes)[:, None] - _AMPLITUDES[None, :]) ** 2, axis=1
        )
        reliability = max(0.5, float(np.median(distances[:, 1] - distances[:, 0])))
        magnitude = min(40.0, reliability / variance)
        output.append((2.0 * hard - 1.0) * magnitude)
    return np.concatenate(output) if output else np.zeros(0, dtype=np.float64)


__all__ = [
    "PAS64_COMPOSITION", "PAS64_INPUT_BITS", "PAS64_SYMBOLS",
    "demap_pas64", "map_pas64",
]
