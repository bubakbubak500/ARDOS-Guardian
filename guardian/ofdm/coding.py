"""Explicit shared convolutional/LDPC code profiles and puncturing.

The modem keeps the proven K=7, 171/133 rate-1/2 mother code.  Faster profiles
remove selected coded bits on transmit and put zero-confidence erasures back on
receive before the same soft Viterbi decoder runs.  The receiver always obtains
the profile identifier from the robust PHY header; it never guesses a rate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from fractions import Fraction
from functools import lru_cache

import numpy as np

from ..modem.fec import K as FEC_K
from ..modem.fec import conv_encode, viterbi_decode_soft


class FecProfile(IntEnum):
    FEC_1_2 = 0
    FEC_2_3 = 1
    FEC_3_4 = 2
    FEC_5_6 = 3
    FEC_7_8 = 4
    LDPC_1_2 = 5
    LDPC_3_4 = 6
    LDPC_9_10 = 7
    LDPC_7_8 = 8
    LDPC_4_5 = 9
    LDPC_2_3 = 10
    LDPC_7_10 = 11


@dataclass(frozen=True)
class FecSpec:
    profile: FecProfile
    rate: Fraction
    puncture_pattern: tuple[int, ...]
    family: str = "conv"

    @property
    def label(self) -> str:
        rate = f"{self.rate.numerator}/{self.rate.denominator}"
        return rate if self.family == "conv" else f"LDPC-{rate}"


# Patterns are applied to the serial output of the 171/133 mother encoder.  A
# one is transmitted and a zero is an erasure.  Every period spans a whole
# number of input trellis steps and retains the nominal denominator bits.
FEC_SPECS: tuple[FecSpec, ...] = (
    FecSpec(FecProfile.FEC_1_2, Fraction(1, 2), (1, 1)),
    FecSpec(FecProfile.FEC_2_3, Fraction(2, 3), (1, 1, 1, 0)),
    FecSpec(FecProfile.FEC_3_4, Fraction(3, 4), (1, 1, 1, 0, 0, 1)),
    FecSpec(FecProfile.FEC_5_6, Fraction(5, 6), (1, 1, 1, 0, 0, 1, 1, 0, 0, 1)),
    FecSpec(
        FecProfile.FEC_7_8,
        Fraction(7, 8),
        (1, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1),
    ),
    # Systematic sparse accumulate codes. The three available on-air IDs fit
    # the existing three-bit coding field, so frame v2 remains decodable while
    # 2.3.3 peers can explicitly opt in to soft iterative decoding.
    FecSpec(FecProfile.LDPC_1_2, Fraction(1, 2), (), family="ldpc"),
    FecSpec(FecProfile.LDPC_2_3, Fraction(2, 3), (), family="ldpc"),
    FecSpec(FecProfile.LDPC_7_10, Fraction(7, 10), (), family="ldpc"),
    FecSpec(FecProfile.LDPC_3_4, Fraction(3, 4), (), family="ldpc"),
    FecSpec(FecProfile.LDPC_4_5, Fraction(4, 5), (), family="ldpc"),
    FecSpec(FecProfile.LDPC_7_8, Fraction(7, 8), (), family="ldpc"),
    FecSpec(FecProfile.LDPC_9_10, Fraction(9, 10), (), family="ldpc"),
)

_BY_ID = {spec.profile: spec for spec in FEC_SPECS}
_BY_LABEL = {spec.label: spec.profile for spec in FEC_SPECS}


def fec_profile(value: FecProfile | int | str) -> FecProfile:
    """Resolve a stored/header value to a supported explicit profile."""
    if isinstance(value, FecProfile):
        return value
    if isinstance(value, str):
        text = value.strip().upper().replace("FEC_", "").replace("_", "/")
        try:
            return _BY_LABEL[text]
        except KeyError:
            try:
                value = int(value)
            except ValueError:
                raise ValueError(f"unknown FEC profile {value!r}") from None
    try:
        return FecProfile(int(value))
    except (TypeError, ValueError):
        raise ValueError(f"unknown FEC profile {value!r}") from None


def fec_spec(value: FecProfile | int | str) -> FecSpec:
    return _BY_ID[fec_profile(value)]


def mother_bits(byte_count: int) -> int:
    """Rate-1/2 coded length including the K-1 terminating trellis steps."""
    if byte_count < 0:
        raise ValueError("byte_count must be >= 0")
    return 2 * (int(byte_count) * 8 + FEC_K - 1)


def _ldpc_dimensions(bit_count: int, rate: Fraction) -> tuple[int, int]:
    information = max(0, int(bit_count))
    parity = int(np.ceil(information * (rate.denominator - rate.numerator)
                         / rate.numerator))
    return information, max(1, parity)


@dataclass(frozen=True)
class _LdpcGraph:
    information: int
    parity: int
    edge_var: np.ndarray
    edge_check: np.ndarray
    check_starts: np.ndarray
    data_checks: tuple[tuple[int, ...], ...]

    @property
    def variables(self) -> int:
        return self.information + self.parity


@lru_cache(maxsize=64)
def _ldpc_graph(information: int, parity: int) -> _LdpcGraph:
    """Build a deterministic sparse systematic accumulate parity graph.

    Each information bit has degree three. The dual-diagonal parity part makes
    encoding linear-time while retaining an ordinary sparse Tanner graph for
    soft normalized-min-sum decoding.
    """
    information = int(information)
    parity = int(parity)
    data_checks: list[list[int]] = [[] for _ in range(information)]
    checks: list[list[int]] = [[] for _ in range(parity)]
    # Three affine strides produced a valid sparse code, but also repeated the
    # same short cycles throughout a long block.  That implementation had a
    # very high error floor: rate 3/4 needed roughly 26 dB residual SNR even
    # though 64-QAM supplied enough bit information around 19 dB.  Build three
    # independently shuffled, check-balanced edge lanes instead.  PCG64 and the
    # seed are explicit so every peer derives the identical graph from the
    # information/parity dimensions without sending a matrix over the air.
    seed = ((information * 0x9E3779B1) ^ (parity * 0x85EBCA77)
            ^ 0x4C445043) & 0xFFFFFFFFFFFFFFFF
    rng = np.random.Generator(np.random.PCG64(seed))
    for lane in range(3):
        variables = rng.permutation(information)
        offset = (lane * max(1, parity // 3) + lane) % parity
        for position, variable_value in enumerate(variables):
            variable = int(variable_value)
            check = (position + offset) % parity
            while check in data_checks[variable]:
                check = (check + 1) % parity
            data_checks[variable].append(check)
            checks[check].append(variable)
    for check in range(parity):
        checks[check].append(information + check)
        if check:
            checks[check].append(information + check - 1)
    starts = [0]
    edge_var: list[int] = []
    edge_check: list[int] = []
    for check, variables in enumerate(checks):
        edge_var.extend(variables)
        edge_check.extend([check] * len(variables))
        starts.append(len(edge_var))
    return _LdpcGraph(
        information, parity,
        np.asarray(edge_var, dtype=np.int32),
        np.asarray(edge_check, dtype=np.int32),
        np.asarray(starts, dtype=np.int32),
        tuple(tuple(items) for items in data_checks),
    )


def _ldpc_encode(bits: np.ndarray, rate: Fraction) -> np.ndarray:
    data = np.asarray(bits, dtype=np.int8).reshape(-1) & 1
    information, parity = _ldpc_dimensions(len(data), rate)
    graph = _ldpc_graph(information, parity)
    syndrome = np.zeros(parity, dtype=np.int8)
    for variable, checks in enumerate(graph.data_checks):
        if data[variable]:
            syndrome[np.asarray(checks, dtype=np.int32)] ^= 1
    parity_bits = np.empty(parity, dtype=np.int8)
    previous = 0
    for check in range(parity):
        parity_bits[check] = syndrome[check] ^ previous
        previous = int(parity_bits[check])
    return np.concatenate([data, parity_bits])


def _ldpc_decode(soft: np.ndarray, information: int, rate: Fraction,
                 max_iterations: int = 48) -> np.ndarray:
    information, parity = _ldpc_dimensions(information, rate)
    graph = _ldpc_graph(information, parity)
    values = np.asarray(soft, dtype=np.float64).reshape(-1)
    if len(values) != graph.variables:
        raise ValueError(
            f"LDPC section has {len(values)} bits, expected {graph.variables}"
        )
    # Guardian LLR is positive for bit one; normalized min-sum below uses the
    # conventional positive-for-zero sign.
    channel = -np.clip(values, -80.0, 80.0)
    variable_to_check = channel[graph.edge_var].copy()
    check_to_variable = np.zeros_like(variable_to_check)
    hard = np.zeros(graph.variables, dtype=np.int8)
    starts = graph.check_starts[:-1]
    for _ in range(max(1, int(max_iterations))):
        # Flooding normalized min-sum, evaluated for every Tanner edge in one
        # vector pass.  The old implementation performed one Python loop and a
        # small allocation per parity check; a noisy 1024-byte block could do
        # that tens of thousands of times before rejecting.  That made decoder
        # latency longer than the waveform itself on real-radio failures.
        magnitudes = np.abs(variable_to_check)
        minimum = np.minimum.reduceat(magnitudes, starts)
        minimum_at_edge = minimum[graph.edge_check]
        is_minimum = magnitudes == minimum_at_edge
        minimum_count = np.add.reduceat(is_minimum, starts)
        without_minimum = np.where(is_minimum, np.inf, magnitudes)
        second = np.minimum.reduceat(without_minimum, starts)
        # If a check has two equal minima, excluding either edge still leaves
        # the same minimum.  Checks are constructed with degree >= 2.
        use_second = is_minimum & (minimum_count[graph.edge_check] == 1)
        outgoing_magnitude = np.where(
            use_second, second[graph.edge_check], minimum_at_edge
        )
        negative = variable_to_check < 0.0
        check_negative = np.bitwise_xor.reduceat(negative, starts)
        outgoing_negative = check_negative[graph.edge_check] ^ negative
        check_to_variable = 0.80 * np.where(
            outgoing_negative, -outgoing_magnitude, outgoing_magnitude
        )
        sums = np.bincount(
            graph.edge_var, weights=check_to_variable,
            minlength=graph.variables,
        )
        posterior = channel + sums
        hard = (posterior < 0.0).astype(np.int8)
        syndrome = np.bitwise_xor.reduceat(hard[graph.edge_var], starts)
        if not np.any(syndrome):
            return hard[:information]
        variable_to_check = posterior[graph.edge_var] - check_to_variable
    return hard[:information]


def punctured_bits(mother_count: int, profile: FecProfile | int | str) -> int:
    """Exact transmitted length for a mother-code bit count."""
    if mother_count < 0:
        raise ValueError("mother_count must be >= 0")
    spec = fec_spec(profile)
    if spec.family != "conv":
        raise ValueError("punctured_bits applies only to convolutional profiles")
    pattern = spec.puncture_pattern
    periods, tail = divmod(int(mother_count), len(pattern))
    return periods * sum(pattern) + sum(pattern[:tail])


def encoded_bits(byte_count: int, profile: FecProfile | int | str) -> int:
    spec = fec_spec(profile)
    if spec.family == "ldpc":
        information, parity = _ldpc_dimensions(int(byte_count) * 8, spec.rate)
        return information + parity
    return punctured_bits(mother_bits(byte_count), profile)


def effective_rate(information_bits: int, transmitted_bits: int) -> float:
    if transmitted_bits <= 0:
        return 0.0
    return max(0, int(information_bits)) / int(transmitted_bits)


def puncture(coded, profile: FecProfile | int | str) -> np.ndarray:
    """Remove mother-code bits selected by the profile's periodic mask."""
    coded = np.asarray(coded)
    spec = fec_spec(profile)
    if spec.family != "conv":
        raise ValueError("puncture applies only to convolutional profiles")
    pattern = np.asarray(spec.puncture_pattern, dtype=bool)
    if len(coded) == 0:
        return coded.copy()
    mask = np.resize(pattern, len(coded))
    return coded[mask]


def depuncture(soft, mother_count: int,
               profile: FecProfile | int | str) -> np.ndarray:
    """Restore punctured positions as zero-confidence soft erasures."""
    soft = np.asarray(soft, dtype=np.float64).reshape(-1)
    spec = fec_spec(profile)
    if spec.family != "conv":
        raise ValueError("depuncture applies only to convolutional profiles")
    pattern = np.asarray(spec.puncture_pattern, dtype=bool)
    mask = np.resize(pattern, int(mother_count))
    expected = int(np.count_nonzero(mask))
    if len(soft) != expected:
        raise ValueError(f"punctured section has {len(soft)} bits, expected {expected}")
    restored = np.zeros(int(mother_count), dtype=np.float64)
    restored[mask] = soft
    return restored


def encode_bits(bits, profile: FecProfile | int | str) -> np.ndarray:
    spec = fec_spec(profile)
    return (_ldpc_encode(np.asarray(bits), spec.rate) if spec.family == "ldpc"
            else puncture(conv_encode(bits), profile))


def decode_soft(soft, byte_count: int,
                profile: FecProfile | int | str) -> np.ndarray:
    spec = fec_spec(profile)
    if spec.family == "ldpc":
        return _ldpc_decode(soft, int(byte_count) * 8, spec.rate)
    restored = depuncture(soft, mother_bits(byte_count), profile)
    return viterbi_decode_soft(restored)


def combine_harq_soft(current, current_profile: FecProfile | int | str,
                      previous, previous_profile: FecProfile | int | str,
                      byte_count: int) -> np.ndarray:
    """Rate-compatible soft combining across Guardian retry FEC profiles.

    Convolutional profiles are projected onto their common rate-1/2 mother
    code, so a stronger retry contributes previously punctured parity positions
    while repeated positions add confidence. Sparse LDPC rates share systematic
    information bits; those LLRs combine across rates, while parity combines
    only when the graph/rate is identical. Crossing code families is unsafe and
    therefore returns the current observation unchanged.
    """
    now = np.asarray(current, dtype=np.float64).reshape(-1)
    old = np.asarray(previous, dtype=np.float64).reshape(-1)
    now_spec = fec_spec(current_profile)
    old_spec = fec_spec(previous_profile)
    if now_spec.family != old_spec.family:
        return now
    if now_spec.family == "conv":
        length = mother_bits(byte_count)
        now_mother = depuncture(now, length, current_profile)
        old_mother = depuncture(old, length, previous_profile)
        return puncture(
            np.clip(now_mother + old_mother, -1.0e4, 1.0e4), current_profile
        )
    information = int(byte_count) * 8
    combined = now.copy()
    shared = min(information, len(now), len(old))
    combined[:shared] = np.clip(
        combined[:shared] + old[:shared], -1.0e4, 1.0e4
    )
    if now_spec.profile == old_spec.profile and len(now) == len(old):
        combined[information:] = np.clip(
            combined[information:] + old[information:], -1.0e4, 1.0e4
        )
    return combined


def iterative_decode_candidates(soft, byte_count: int,
                                profile: FecProfile | int | str,
                                iterations: int = 3,
                                accept=None) -> list[np.ndarray]:
    """Bounded reliability-gated decoder feedback candidates.

    The first candidate is the ordinary soft decode. For LDPC, reliable coded
    positions then feed a small extrinsic term back into the next iteration.
    Framing still accepts only a candidate whose payload CRC passes, preventing
    positive feedback from turning a plausible wrong codeword into delivered
    data.
    """
    observations = np.asarray(soft, dtype=np.float64).reshape(-1)
    candidates = [decode_soft(observations, byte_count, profile)]
    if accept is not None and accept(candidates[-1]):
        return candidates
    if fec_spec(profile).family != "ldpc":
        return candidates
    working = observations.copy()
    for _ in range(1, max(1, min(5, int(iterations)))):
        recoded = encode_bits(candidates[-1][:int(byte_count) * 8], profile)
        if len(recoded) != len(working):
            break
        confidence = np.abs(working)
        threshold = float(np.quantile(confidence, 0.60))
        reliable = confidence >= threshold
        strength = min(1.5, max(0.05, float(np.median(confidence)) * 0.12))
        extrinsic = np.zeros_like(working)
        extrinsic[reliable] = (2.0 * recoded[reliable] - 1.0) * strength
        working = np.clip(observations + extrinsic, -1.0e4, 1.0e4)
        candidate = decode_soft(working, byte_count, profile)
        candidates.append(candidate)
        if accept is not None and accept(candidate):
            break
        if np.array_equal(candidate, candidates[-2]):
            break
    return candidates
