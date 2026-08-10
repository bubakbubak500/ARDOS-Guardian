"""Explicit OFDM convolutional-code profiles and puncturing.

The modem keeps the proven K=7, 171/133 rate-1/2 mother code.  Faster profiles
remove selected coded bits on transmit and put zero-confidence erasures back on
receive before the same soft Viterbi decoder runs.  The receiver always obtains
the profile identifier from the robust PHY header; it never guesses a rate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from fractions import Fraction

import numpy as np

from ..modem.fec import K as FEC_K
from ..modem.fec import conv_encode, viterbi_decode_soft


class FecProfile(IntEnum):
    FEC_1_2 = 0
    FEC_2_3 = 1
    FEC_3_4 = 2
    FEC_5_6 = 3
    FEC_7_8 = 4


@dataclass(frozen=True)
class FecSpec:
    profile: FecProfile
    rate: Fraction
    puncture_pattern: tuple[int, ...]

    @property
    def label(self) -> str:
        return f"{self.rate.numerator}/{self.rate.denominator}"


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


def punctured_bits(mother_count: int, profile: FecProfile | int | str) -> int:
    """Exact transmitted length for a mother-code bit count."""
    if mother_count < 0:
        raise ValueError("mother_count must be >= 0")
    pattern = fec_spec(profile).puncture_pattern
    periods, tail = divmod(int(mother_count), len(pattern))
    return periods * sum(pattern) + sum(pattern[:tail])


def encoded_bits(byte_count: int, profile: FecProfile | int | str) -> int:
    return punctured_bits(mother_bits(byte_count), profile)


def effective_rate(information_bits: int, transmitted_bits: int) -> float:
    if transmitted_bits <= 0:
        return 0.0
    return max(0, int(information_bits)) / int(transmitted_bits)


def puncture(coded, profile: FecProfile | int | str) -> np.ndarray:
    """Remove mother-code bits selected by the profile's periodic mask."""
    coded = np.asarray(coded)
    pattern = np.asarray(fec_spec(profile).puncture_pattern, dtype=bool)
    if len(coded) == 0:
        return coded.copy()
    mask = np.resize(pattern, len(coded))
    return coded[mask]


def depuncture(soft, mother_count: int,
               profile: FecProfile | int | str) -> np.ndarray:
    """Restore punctured positions as zero-confidence soft erasures."""
    soft = np.asarray(soft, dtype=np.float64).reshape(-1)
    pattern = np.asarray(fec_spec(profile).puncture_pattern, dtype=bool)
    mask = np.resize(pattern, int(mother_count))
    expected = int(np.count_nonzero(mask))
    if len(soft) != expected:
        raise ValueError(f"punctured section has {len(soft)} bits, expected {expected}")
    restored = np.zeros(int(mother_count), dtype=np.float64)
    restored[mask] = soft
    return restored


def encode_bits(bits, profile: FecProfile | int | str) -> np.ndarray:
    return puncture(conv_encode(bits), profile)


def decode_soft(soft, byte_count: int,
                profile: FecProfile | int | str) -> np.ndarray:
    restored = depuncture(soft, mother_bits(byte_count), profile)
    return viterbi_decode_soft(restored)
