"""Shared SC-FTN MCS and profile compatibility contracts.

The package name is retained because the payload and stored configuration still
refer to ``guardian.ofdm``.  Physical profiles are defined in
``guardian.waveforms.config``; this module only keeps the established wire MCS
lookups and the historical import names used by the shared framing/link code.
There is deliberately no FFT profile class or OFDM profile registry here.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from ..waveforms.config import (
    PROFILES,
    SC_FTN_2K7,
    WaveformProfile,
    profile_names,
    profile_or_default as _waveform_profile_or_default,
)


class OfdmConfigError(ValueError):
    """A profile or MCS selection that cannot describe a working waveform."""


@dataclass(frozen=True)
class Mcs:
    """One wire-compatible modulation and coding scheme."""

    index: int
    modulation: str
    bits_per_symbol: int | Fraction
    code_rate: Fraction
    min_snr_db: float = 0.0

    @property
    def label(self) -> str:
        pretty = {
            "bpsk": "BPSK", "qpsk": "QPSK", "qam16": "16-QAM",
            "psk8": "8-PSK", "qam32": "32-QAM", "qam64": "64-QAM",
            "qam128": "128-QAM", "qam256": "256-QAM",
            "qam512": "512-QAM", "qam1024": "1024-QAM",
            "apsk16": "16-APSK", "apsk32": "32-APSK",
            "apsk64": "64-APSK", "apsk128": "128-APSK",
            "apsk256": "256-APSK", "apsk512": "512-APSK",
            "gqam16": "16-GQAM", "gqam64": "64-GQAM",
            "gqam256": "256-GQAM", "gqam1024": "1024-GQAM",
            "pas64": "64-QAM PAS",
        }
        rate = f"{self.code_rate.numerator}/{self.code_rate.denominator}"
        return f"MCS{self.index} {pretty[self.modulation]} r={rate}"

# MCS0..3 are the established four-entry table.  Keep it unchanged for wire
# compatibility with older control/backend callers.
MCS_TABLE: tuple[Mcs, ...] = (
    Mcs(0, "bpsk", 1, Fraction(1, 2), min_snr_db=3.0),
    Mcs(1, "qpsk", 2, Fraction(1, 2), min_snr_db=3.0),
    Mcs(2, "qam16", 4, Fraction(1, 2), min_snr_db=10.5),
    Mcs(3, "qam64", 6, Fraction(1, 2), min_snr_db=15.5),
)

# The five-bit header field has always had room for these SC payload modes.  The
# complete table is used by the SC decoder and adaptation controller.
SC_MCS_TABLE: tuple[Mcs, ...] = (
    *MCS_TABLE,
    Mcs(4, "qam256", 8, Fraction(1, 2), min_snr_db=24.0),
    Mcs(5, "apsk16", 4, Fraction(1, 2), min_snr_db=11.0),
    Mcs(6, "apsk32", 5, Fraction(1, 2), min_snr_db=15.0),
    Mcs(7, "psk8", 3, Fraction(1, 2), min_snr_db=7.0),
    Mcs(8, "qam32", 5, Fraction(1, 2), min_snr_db=14.0),
    Mcs(9, "apsk64", 6, Fraction(1, 2), min_snr_db=17.0),
    Mcs(10, "qam128", 7, Fraction(1, 2), min_snr_db=20.5),
    Mcs(11, "apsk128", 7, Fraction(1, 2), min_snr_db=21.0),
    Mcs(12, "apsk256", 8, Fraction(1, 2), min_snr_db=25.0),
    Mcs(13, "qam512", 9, Fraction(1, 2), min_snr_db=29.0),
    Mcs(14, "apsk512", 9, Fraction(1, 2), min_snr_db=30.0),
    Mcs(15, "qam1024", 10, Fraction(1, 2), min_snr_db=34.0),
    Mcs(16, "gqam16", 4, Fraction(1, 2), min_snr_db=10.5),
    Mcs(17, "gqam64", 6, Fraction(1, 2), min_snr_db=15.5),
    Mcs(18, "gqam256", 8, Fraction(1, 2), min_snr_db=24.0),
    Mcs(19, "gqam1024", 10, Fraction(1, 2), min_snr_db=34.0),
    Mcs(20, "pas64", Fraction(43, 8), Fraction(1, 2), min_snr_db=15.0),
)

HEADER_MCS = MCS_TABLE[0]
DEFAULT_MCS_INDEX = 1


def _lookup(table: tuple[Mcs, ...], index: int, label: str) -> Mcs:
    try:
        wanted = int(index)
    except (TypeError, ValueError):
        raise OfdmConfigError(f"unknown {label} index {index!r}") from None
    for entry in table:
        if entry.index == wanted:
            return entry
    raise OfdmConfigError(f"unknown {label} index {index}")


def mcs(index: int) -> Mcs:
    """Look up one of the four historical wire MCS entries."""
    return _lookup(MCS_TABLE, index, "MCS")


def sc_mcs(index: int) -> Mcs:
    """Look up one of the complete SC-FTN MCS entries."""
    return _lookup(SC_MCS_TABLE, index, "SC MCS")


def best_mcs_for(snr_db: float | None) -> Mcs | None:
    """Return the fastest legacy MCS proven at ``snr_db``.

    SC adaptation uses its explicit full table through ``sc_mcs``.  This helper
    remains the conservative four-entry API expected by older callers.
    """
    if snr_db is None:
        return None
    usable = [entry for entry in MCS_TABLE if snr_db >= entry.min_snr_db]
    return max(usable, key=lambda entry: entry.index) if usable else None


# Historical type/import name.  It now names the SC profile object, whose
# ``__post_init__`` rejects every non-SC family and whose retained FFT-looking
# fields are compatibility metadata only.
OfdmProfile = WaveformProfile

# Historical BENCH/default names point to the production 2K7 SC profile.  The
# mapping contains only the six SC-FTN profiles defined by waveforms.config.
BENCH = SC_FTN_2K7
DEFAULT_PROFILE_NAME = BENCH.name


def profile(name: str) -> WaveformProfile:
    """Look up a production SC-FTN profile by name."""
    key = str(name or "").strip().upper()
    if key == "BENCH":
        key = DEFAULT_PROFILE_NAME
    try:
        return PROFILES[key]
    except KeyError:
        raise OfdmConfigError(
            f"unknown SC-FTN profile {name!r}; known: {', '.join(profile_names())}"
        ) from None


def profile_or_default(name: str | None) -> WaveformProfile:
    """Resolve an SC-FTN profile, using 2K7 for legacy/unknown stored names."""
    key = str(name or "").strip().upper()
    if key == "BENCH":
        key = DEFAULT_PROFILE_NAME
    try:
        return profile(key)
    except OfdmConfigError:
        return _waveform_profile_or_default(DEFAULT_PROFILE_NAME)


__all__ = [
    "BENCH", "DEFAULT_MCS_INDEX", "DEFAULT_PROFILE_NAME", "HEADER_MCS",
    "MCS_TABLE", "SC_MCS_TABLE", "Mcs", "OfdmConfigError", "OfdmProfile",
    "PROFILES", "WaveformProfile", "best_mcs_for", "mcs", "profile",
    "profile_names", "profile_or_default", "sc_mcs",
]
