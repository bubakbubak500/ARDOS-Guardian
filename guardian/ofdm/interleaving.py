"""Bit interleaving for the OFDM payload modem.

A convolutional code repairs errors that are spread out and fails on errors
that arrive in a clump. An OFDM burst produces exactly the clumped kind: one
subcarrier sitting in a notch is wrong in *every* symbol of the burst, and one
damaged symbol is wrong on every carrier at once. Interleaving is what turns
those clumps back into the isolated bit errors the K=7 code was designed for.

The permutation is a single multiplicative stride, `out[(i * s) % n] = in[i]`,
with `s` coprime to the block length `n` so the map is a bijection and in fact
one long cycle. Two properties follow, and both are the reason for choosing
this over a plain write-rows/read-columns block interleaver:

*   Consecutive coded bits land `s` apart in the transmitted stream, and `s` is
    picked near `n/phi` (the golden ratio), so neighbouring code bits end up
    roughly six tenths of a block away from each other -- a different OFDM
    symbol *and* a different carrier, for every constellation order.

*   A dead carrier occupies `b` fixed residues modulo the symbol length `C`, one
    per bit it carries. Because `C` divides `n`, and `s` is coprime to `n` and
    therefore to `C`, each of those maps back to a single residue class modulo
    `C` in the coded stream -- so the bits a notch destroys arrive as `b`
    arithmetic progressions of step `C` rather than as a clump. Measured on the
    BENCH profile, the closest two destroyed code bits ever get is 44 apart at
    BPSK, 39 at QPSK and 16-QAM, and 9 at 64-QAM; the code's memory is 6 bits, so
    every one of those is an isolated error as far as the decoder is concerned.
    A destroyed OFDM *symbol* spreads the same way, worst case 21 apart. These
    follow from the construction rather than from luck with one profile;
    `tests/test_ofdm_constellation.py` measures them and
    `tests/test_ofdm_channel.py` proves the end-to-end consequence.

Deinterleaving needs no modular inverse: the same index array reads the
permutation back the other way.
"""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

_GOLDEN = (1.0 + 5.0 ** 0.5) / 2.0


@lru_cache(maxsize=None)
def stride_for(length: int) -> int:
    """The interleaving stride for a block of `length` bits.

    Starts at `length / phi` and steps up to the first value coprime with the
    block length. Both ends of a link compute it from the block length alone,
    so there is nothing to negotiate.
    """
    if length <= 2:
        return 1
    stride = max(2, int(round(length / _GOLDEN)))
    while math.gcd(stride, length) != 1:
        stride += 1
        if stride >= length:  # pragma: no cover - unreachable for length > 2
            return 1
    return stride


@lru_cache(maxsize=None)
def _permutation(length: int) -> np.ndarray:
    """Destination index for each source index."""
    return (np.arange(length, dtype=np.int64) * stride_for(length)) % length


def interleave(values):
    """Spread `values` across the block. Works on bits or on soft values."""
    values = np.asarray(values)
    out = np.empty_like(values)
    out[_permutation(len(values))] = values
    return out


def deinterleave(values):
    """Undo `interleave` on a block of the same length."""
    values = np.asarray(values)
    return values[_permutation(len(values))]
