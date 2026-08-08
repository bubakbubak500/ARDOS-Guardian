"""Gray-mapped constellations with soft output.

Four square constellations (BPSK, QPSK, 16-QAM, 64-QAM), each normalised to
unit average symbol energy so the receiver's SNR and EVM figures mean the same
thing whichever one a burst used.

Square QAM is two independent PAM axes. Splitting the bits in half -- the first
half drives I, the second Q -- and Gray-coding each axis on its own gives the
property that matters for a coded link: physically adjacent constellation
points differ in exactly one bit, so the errors a noisy channel actually makes
are single-bit errors that the convolutional code can absorb.

Demapping produces max-log LLRs rather than hard bits, because
`guardian.modem.fec.viterbi_decode_soft` can use them and a hard slice throws
away most of what the equaliser knew.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np

#: Bits carried by one constellation symbol.
BITS_PER_SYMBOL: dict[str, int] = {"bpsk": 1, "qpsk": 2, "qam16": 4, "qam64": 6}

MODULATIONS = tuple(BITS_PER_SYMBOL)


def bits_per_symbol(modulation: str) -> int:
    """Bits one symbol of `modulation` carries."""
    try:
        return BITS_PER_SYMBOL[modulation]
    except KeyError:
        raise ValueError(f"unknown modulation {modulation!r}") from None


def gray_to_binary(gray: np.ndarray | int) -> np.ndarray:
    """Invert a reflected-binary (Gray) code."""
    value = np.asarray(gray, dtype=np.int64)
    out = value.copy()
    shift = 1
    while shift < 64:
        shifted = out >> shift
        if not shifted.any():
            break
        out = out ^ shifted
        shift <<= 1
    return out


def _pam_levels(nbits: int) -> np.ndarray:
    """Gray-indexed PAM levels: `levels[g]` is the amplitude for Gray code `g`.

    Amplitudes are the usual odd integers. Ordering them by the *binary* value
    the Gray code decodes to is what makes neighbouring amplitudes one bit
    apart.
    """
    count = 1 << nbits
    natural = gray_to_binary(np.arange(count))
    return (2 * natural - (count - 1)).astype(np.float64)


@lru_cache(maxsize=None)
def constellation(modulation: str) -> np.ndarray:
    """Unit-energy constellation points, indexed by their bit pattern.

    Index `v` is the integer whose most significant bit is the first bit of the
    symbol, so `constellation(m)[v]` is the point that `map_bits` emits for the
    bit group spelling `v`.
    """
    m = bits_per_symbol(modulation)
    if m == 1:
        points = np.array([-1.0, 1.0], dtype=np.complex128)
    else:
        half = m // 2
        levels = _pam_levels(half)
        index = np.arange(1 << m)
        points = (levels[index >> half] + 1j * levels[index & ((1 << half) - 1)])
    return points / np.sqrt(np.mean(np.abs(points) ** 2))


@lru_cache(maxsize=None)
def _bit_masks(modulation: str) -> tuple[np.ndarray, ...]:
    """For each bit position, which constellation indices carry a 1 there."""
    m = bits_per_symbol(modulation)
    index = np.arange(1 << m)
    return tuple(((index >> (m - 1 - j)) & 1).astype(bool) for j in range(m))


def map_bits(bits, modulation: str) -> np.ndarray:
    """Map a flat bit sequence to constellation symbols (first bit is the MSB)."""
    m = bits_per_symbol(modulation)
    bits = np.asarray(bits, dtype=np.int64).reshape(-1)
    if len(bits) % m:
        raise ValueError(
            f"{len(bits)} bits do not divide into {m}-bit {modulation} symbols"
        )
    weights = 1 << np.arange(m - 1, -1, -1)
    values = (bits.reshape(-1, m) * weights).sum(axis=1)
    return constellation(modulation)[values]


def demap_hard(symbols, modulation: str) -> np.ndarray:
    """Nearest-point (maximum-likelihood) hard decisions, as a flat bit array."""
    m = bits_per_symbol(modulation)
    symbols = np.asarray(symbols, dtype=np.complex128).reshape(-1)
    points = constellation(modulation)
    nearest = np.abs(symbols[:, None] - points[None, :]).argmin(axis=1)
    shifts = np.arange(m - 1, -1, -1)
    return ((nearest[:, None] >> shifts) & 1).astype(np.int8).reshape(-1)


def demap_llr(symbols, modulation: str, noise_var=1.0) -> np.ndarray:
    """Max-log LLRs, one per bit, flattened in the same order as `map_bits`.

    Positive means "probably a 1", which is the sign convention
    `viterbi_decode_soft` expects, and the magnitude is how sure the equaliser
    was.

    `noise_var` is the noise power on the equalised symbol. It may be a scalar
    or one value per symbol: after zero-forcing, a carrier the channel had
    notched out carries far more noise than its neighbours, and passing the
    per-carrier value is what lets the decoder discount it instead of trusting
    a confidently wrong decision.
    """
    m = bits_per_symbol(modulation)
    symbols = np.asarray(symbols, dtype=np.complex128).reshape(-1)
    variance = np.asarray(noise_var, dtype=np.float64)
    if variance.ndim:
        variance = variance.reshape(-1)
        if len(variance) != len(symbols):
            raise ValueError("noise_var must be scalar or one value per symbol")
    # Guard the division: a perfectly clean simulated channel has zero noise,
    # and an infinite LLR would poison the decoder's metrics.
    variance = np.maximum(variance, np.finfo(np.float64).tiny)

    distance = np.abs(symbols[:, None] - constellation(modulation)[None, :]) ** 2
    llr = np.empty((len(symbols), m), dtype=np.float64)
    for j, ones in enumerate(_bit_masks(modulation)):
        closest_one = distance[:, ones].min(axis=1)
        closest_zero = distance[:, ~ones].min(axis=1)
        llr[:, j] = closest_zero - closest_one
    if variance.ndim:
        llr /= variance[:, None]
    else:
        llr /= variance
    return llr.reshape(-1)


def evm(symbols, modulation: str) -> float:
    """Decision-directed RMS error-vector magnitude, as a fraction (not %).

    Each symbol is compared with the constellation point it would decode to, so
    this is what a constellation display would show. It flatters a bad link --
    once decisions start being wrong the error to the *wrong* point is small --
    which is why `LinkMetrics` records where its SNR came from separately.
    """
    symbols = np.asarray(symbols, dtype=np.complex128).reshape(-1)
    if not len(symbols):
        return float("nan")
    points = constellation(modulation)
    nearest = points[np.abs(symbols[:, None] - points[None, :]).argmin(axis=1)]
    return float(np.sqrt(np.mean(np.abs(symbols - nearest) ** 2)
                         / np.mean(np.abs(nearest) ** 2)))
