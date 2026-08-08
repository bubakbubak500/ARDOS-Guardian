"""Forward error correction for the HF (MFSK) modem and the OFDM payload modem.

Rate-1/2, constraint-length K=7 convolutional code (the classic 171/133 octal
polynomials) with hard- and soft-decision Viterbi decoders. This is what lets a
control burst survive the low-SNR, fading HF/SSB channel where simple detection
fails.

Encoder and decoder share one transition function, so they are guaranteed
consistent regardless of bit-ordering conventions.

The decoders walk the trellis one step at a time but evaluate all 64 states of
a step at once with numpy. A control burst is a few hundred coded bits, where a
per-state Python loop was unnoticeable; an OFDM data block is a few thousand,
and the same loop turned into the dominant cost of a bench run. The vectorised
form keeps the identical survivor rule -- including which predecessor wins a
tie -- so both modems decode bit-for-bit as before.
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

# The same trellis indexed the other way round: for each *next* state, its two
# possible predecessors and the code bits each of them emits.
#
# A step shifts one input bit into a K-bit register, sr = (state << 1) | bit,
# and keeps its low K-1 bits as the next state. So a next state `ns` pins the
# whole low end of the register: sr is either `ns` or `ns | 64`, the input bit
# is always `ns & 1` (which is why the traceback below can read the recovered
# bit straight off the state), and the predecessor is sr >> 1. Enumerating
# predecessors instead of successors is what makes a step expressible as two
# whole-array candidate costs and one comparison.
_PRED_A = np.array([ns >> 1 for ns in range(NUM_STATES)], dtype=np.intp)
_PRED_B = _PRED_A | (NUM_STATES >> 1)
_OUT_A = np.array([(_parity(ns & G1), _parity(ns & G2)) for ns in range(NUM_STATES)],
                  dtype=np.int8)
_OUT_B = np.array([(_parity((ns | NUM_STATES) & G1), _parity((ns | NUM_STATES) & G2))
                   for ns in range(NUM_STATES)], dtype=np.int8)
# Soft costs reward agreement, so the code bits become signs: +1 for a 1 bit.
_SIGN_A = (2 * _OUT_A - 1).astype(np.float64)
_SIGN_B = (2 * _OUT_B - 1).astype(np.float64)


def _traceback(prev: np.ndarray, end_state: int) -> np.ndarray:
    """Walk survivors back from `end_state` and drop the flush bits."""
    state = end_state
    bits_rev: list[int] = []
    for t in range(prev.shape[0] - 1, -1, -1):
        bits_rev.append(state & 1)
        state = int(prev[t, state])
    info = np.array(bits_rev[::-1], dtype=np.int8)
    return info[: max(0, len(info) - (K - 1))]


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
    prev = np.zeros((n_steps, NUM_STATES), dtype=np.int16)

    pairs = soft[: 2 * n_steps].reshape(n_steps, 2)
    for t in range(n_steps):
        r1, r2 = pairs[t, 0], pairs[t, 1]
        # Cost = disagreement weighted by how sure the demodulator was.
        cost_a = metrics[_PRED_A] - _SIGN_A[:, 0] * r1 - _SIGN_A[:, 1] * r2
        cost_b = metrics[_PRED_B] - _SIGN_B[:, 0] * r1 - _SIGN_B[:, 1] * r2
        # Strictly less, so an equal-cost tie stays with predecessor A. That is
        # not arbitrary: A is the lower-numbered state, the scalar loop this
        # replaced reached it first, and its own comparison was strictly less.
        take_b = cost_b < cost_a
        metrics = np.where(take_b, cost_b, cost_a)
        prev[t] = np.where(take_b, _PRED_B, _PRED_A)

    return _traceback(prev, int(np.argmin(metrics)))


def viterbi_decode(coded) -> np.ndarray:
    """Hard-decision Viterbi decode; returns the recovered info bits (flush removed)."""
    coded = np.asarray(coded, dtype=np.int8)
    n_steps = len(coded) // 2
    if n_steps == 0:
        return np.array([], dtype=np.int8)

    metrics = np.full(NUM_STATES, _INF, dtype=np.int64)
    metrics[0] = 0
    prev = np.zeros((n_steps, NUM_STATES), dtype=np.int16)     # predecessor state

    pairs = coded[: 2 * n_steps].astype(np.int64).reshape(n_steps, 2)
    for t in range(n_steps):
        r1, r2 = pairs[t, 0], pairs[t, 1]
        cost_a = metrics[_PRED_A] + (_OUT_A[:, 0] != r1) + (_OUT_A[:, 1] != r2)
        cost_b = metrics[_PRED_B] + (_OUT_B[:, 0] != r1) + (_OUT_B[:, 1] != r2)
        # Ties go to A, as above -- and integer costs make them commonplace.
        take_b = cost_b < cost_a
        # Clamping keeps a state no path has reached yet pinned at _INF rather
        # than letting it drift up step by step, so it can never win an argmin.
        metrics = np.minimum(np.where(take_b, cost_b, cost_a), _INF)
        prev[t] = np.where(take_b, _PRED_B, _PRED_A)

    # Traceback from the most likely final state (the true frame flushes to 0,
    # but trailing noise may not, so pick the minimum-metric end state).
    return _traceback(prev, int(np.argmin(metrics)))  # drops flush bits
