"""Forward error correction for the HF (MFSK) modem.

Rate-1/2, constraint-length K=7 convolutional code (the classic 171/133 octal
polynomials) with a hard-decision Viterbi decoder. This is what lets a control
burst survive the low-SNR, fading HF/SSB channel where simple detection fails.

Encoder and decoder share one transition function, so they are guaranteed
consistent regardless of bit-ordering conventions.
"""

from __future__ import annotations

from itertools import chain

import numpy as np

K = 7
G1 = 0o171  # 0b1111001
G2 = 0o133  # 0b1011011
NUM_STATES = 1 << (K - 1)  # 64
_INF = 1 << 30


def _parity(x: int) -> int:
    return bin(x).count("1") & 1


def _outputs(prev_state: int, bit: int) -> tuple[int, int, int]:
    """Return (next_state, out1, out2) for a trellis step."""
    sr = (prev_state << 1) | bit            # K-bit shift register
    o1 = _parity(sr & G1)
    o2 = _parity(sr & G2)
    next_state = sr & (NUM_STATES - 1)      # low K-1 bits persist
    return next_state, o1, o2


# Precompute the trellis once.
_TRANS = [[_outputs(s, b) for b in (0, 1)] for s in range(NUM_STATES)]


def conv_encode(bits) -> np.ndarray:
    """Encode a bit sequence; appends K-1 flush bits (returns 2*(n+K-1) bits)."""
    state = 0
    out: list[int] = []
    for b in chain((int(x) for x in bits), [0] * (K - 1)):
        state, o1, o2 = _TRANS[state][b]
        out.append(o1)
        out.append(o2)
    return np.array(out, dtype=np.int8)


def viterbi_decode_soft(soft) -> np.ndarray:
    """Viterbi decode from per-bit confidences instead of hard 0/1 bits.

    `soft` holds one value per coded bit: positive means "probably 1", negative
    "probably 0", and the magnitude is how sure the demodulator was. Hard
    slicing throws that away, which is exactly what cost us on air -- an MFSK
    symbol decided 1.13:1 was handed to the decoder as a certainty, while its
    neighbours were sure at 30:1 and could have resolved it.
    """
    soft = np.asarray(soft, dtype=np.float64)
    n_steps = len(soft) // 2
    if n_steps == 0:
        return np.array([], dtype=np.int8)

    metrics = np.full(NUM_STATES, np.inf)
    metrics[0] = 0.0
    back = np.zeros((n_steps, NUM_STATES), dtype=np.int8)
    prev = np.zeros((n_steps, NUM_STATES), dtype=np.int16)

    for t in range(n_steps):
        r1, r2 = soft[2 * t], soft[2 * t + 1]
        new_metrics = np.full(NUM_STATES, np.inf)
        for s in range(NUM_STATES):
            m = metrics[s]
            if not np.isfinite(m):
                continue
            for b in (0, 1):
                ns, o1, o2 = _TRANS[s][b]
                # Cost = disagreement weighted by how sure the demodulator was.
                cost = m - (r1 if o1 else -r1) - (r2 if o2 else -r2)
                if cost < new_metrics[ns]:
                    new_metrics[ns] = cost
                    back[t, ns] = b
                    prev[t, ns] = s
        metrics = new_metrics

    state = int(np.argmin(metrics))
    bits_rev: list[int] = []
    for t in range(n_steps - 1, -1, -1):
        bits_rev.append(int(back[t, state]))
        state = int(prev[t, state])
    info = np.array(bits_rev[::-1], dtype=np.int8)
    return info[: max(0, len(info) - (K - 1))]


def viterbi_decode(coded) -> np.ndarray:
    """Hard-decision Viterbi decode; returns the recovered info bits (flush removed)."""
    coded = np.asarray(coded, dtype=np.int8)
    n_steps = len(coded) // 2
    if n_steps == 0:
        return np.array([], dtype=np.int8)

    metrics = np.full(NUM_STATES, _INF, dtype=np.int64)
    metrics[0] = 0
    back = np.zeros((n_steps, NUM_STATES), dtype=np.int8)      # which input bit
    prev = np.zeros((n_steps, NUM_STATES), dtype=np.int16)     # predecessor state

    for t in range(n_steps):
        r1, r2 = int(coded[2 * t]), int(coded[2 * t + 1])
        new_metrics = np.full(NUM_STATES, _INF, dtype=np.int64)
        for s in range(NUM_STATES):
            m = metrics[s]
            if m >= _INF:
                continue
            for b in (0, 1):
                ns, o1, o2 = _TRANS[s][b]
                cost = m + (o1 != r1) + (o2 != r2)
                if cost < new_metrics[ns]:
                    new_metrics[ns] = cost
                    back[t, ns] = b
                    prev[t, ns] = s
        metrics = new_metrics

    # Traceback from the most likely final state (the true frame flushes to 0,
    # but trailing noise may not, so pick the minimum-metric end state).
    state = int(np.argmin(metrics))
    bits_rev: list[int] = []
    for t in range(n_steps - 1, -1, -1):
        bits_rev.append(int(back[t, state]))
        state = int(prev[t, state])
    info = np.array(bits_rev[::-1], dtype=np.int8)
    return info[: max(0, len(info) - (K - 1))]  # drop flush bits
