"""Waveform profiles and modulation/coding schemes for the OFDM payload modem.

Every number that describes the waveform lives here. The rest of the package
reads it from an `OfdmProfile` and never hard-codes a size, a bin index or a
bandwidth, because the occupied bandwidth that VHF radios actually pass is not
known yet: it will come out of two-radio measurements, and when it does, the
answer is a new profile entry rather than a modem change.

The distinction that matters most:

    sample rate  is how fast the soundcard runs (48 kHz, as the rest of
                 Guardian already does).
    occupied bandwidth  is how much spectrum the burst actually uses, and it is
                 decided by which FFT bins carry energy. For BENCH that is
                 about 2.4 kHz inside a 24 kHz audio band.

These are not the same quantity and nothing here treats them as such.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

import numpy as np


class OfdmConfigError(ValueError):
    """A profile or MCS selection that cannot describe a working waveform."""


@dataclass(frozen=True)
class Mcs:
    """One modulation-and-coding scheme.

    Phase 1 carries a single code rate because that is what
    `guardian.modem.fec` provides; `code_rate` is stored explicitly anyway so a
    punctured or LDPC rate can join the table without changing its shape.
    """

    index: int
    modulation: str
    bits_per_symbol: int
    code_rate: Fraction

    @property
    def label(self) -> str:
        pretty = {"bpsk": "BPSK", "qpsk": "QPSK", "qam16": "16-QAM", "qam64": "64-QAM"}
        rate = f"{self.code_rate.numerator}/{self.code_rate.denominator}"
        return f"MCS{self.index} {pretty[self.modulation]} r={rate}"


# MCS0 is the bootstrap mode: the PHY header and every ACK/NACK ride on it, so
# a receiver never has to know the payload modulation in advance. MCS1 is the
# default for data. MCS2/MCS3 are implemented and tested in simulation but are
# not defaults -- no on-air measurement justifies them yet.
MCS_TABLE: tuple[Mcs, ...] = (
    Mcs(0, "bpsk", 1, Fraction(1, 2)),
    Mcs(1, "qpsk", 2, Fraction(1, 2)),
    Mcs(2, "qam16", 4, Fraction(1, 2)),
    Mcs(3, "qam64", 6, Fraction(1, 2)),
)

#: The mode used for the PHY header and for ACK/NACK bursts.
HEADER_MCS = MCS_TABLE[0]

#: What a fresh station transmits data with until adaptation exists.
DEFAULT_MCS_INDEX = 1


def mcs(index: int) -> Mcs:
    """Look up an MCS by index."""
    for entry in MCS_TABLE:
        if entry.index == int(index):
            return entry
    raise OfdmConfigError(f"unknown MCS index {index}")


@dataclass(frozen=True)
class OfdmProfile:
    """A complete description of one OFDM waveform.

    Active subcarriers are the contiguous bin range
    ``[first_carrier, first_carrier + num_carriers)`` of a real FFT. Everything
    guard- and DC-related is expressed by which bins are *absent* from that
    range: bin 0 (DC) and the Nyquist bin are never active, and the bins below
    and above the range are the guard region. There is no separate guard-carrier
    count to keep consistent with anything.
    """

    name: str
    sample_rate: int
    fft_size: int
    cp_length: int
    first_carrier: int
    num_carriers: int
    pilot_spacing: int
    preamble_symbols: int
    training_symbols: int
    block_size: int
    #: Schmidl-Cox plateau height below which a candidate is not a burst.
    #:
    #: The metric is a normalised correlation between the two halves of a
    #: repeating preamble symbol, so a burst produces about (SNR/(1+SNR))^2
    #: measured in the occupied band: 0.38 at 2 dB, 0.58 at 5 dB, 0.75 at 8 dB.
    #: Noise produces a peak of its own over any long buffer -- measured over
    #: forty 3-second buffers of band-limited noise, the largest was 0.34 and the
    #: median 0.25. A threshold of 0.25 therefore accepted noise as a burst in
    #: 40% of buffers; 0.35 accepted none of them.
    #:
    #: 0.40 keeps a margin above that and still detects about 2.5 dB below the
    #: point where the decoder gives up anyway (measured cliff: 5-6 dB in-band),
    #: so nothing decodable is being turned away. A false alarm is not fatal --
    #: the header CRC rejects it and `framing.decode_burst` moves on to the next
    #: candidate -- but each one wastes a receive window.
    detection_threshold: float = 0.40
    #: Burst RMS as a fraction of full scale. OFDM with tens of carriers peaks
    #: 9-11 dB above its RMS, so 0.15 leaves headroom for the peaks plus any
    #: gain the radio path adds before something clips.
    tx_rms: float = 0.15

    def __post_init__(self) -> None:
        self.validate()

    # -- validation ---------------------------------------------------------

    def validate(self) -> None:
        """Raise `OfdmConfigError` unless this profile describes a real waveform."""
        n = self.fft_size
        if n < 16 or n & (n - 1):
            raise OfdmConfigError(f"fft_size must be a power of two >= 16, got {n}")
        if not 0 < self.cp_length < n:
            raise OfdmConfigError(f"cp_length must be in (0, {n}), got {self.cp_length}")
        if self.first_carrier < 1:
            raise OfdmConfigError("first_carrier must be >= 1: bin 0 is DC and stays empty")
        if self.num_carriers < 4:
            raise OfdmConfigError(f"need at least 4 active carriers, got {self.num_carriers}")
        if self.first_carrier + self.num_carriers > n // 2:
            raise OfdmConfigError(
                f"active carriers {self.first_carrier}..{self.first_carrier + self.num_carriers - 1} "
                f"reach the Nyquist bin {n // 2}; the top bin must stay empty"
            )
        if self.pilot_spacing < 2:
            raise OfdmConfigError("pilot_spacing must be >= 2")
        if self.num_pilots < 2:
            raise OfdmConfigError(
                "need at least 2 pilots to fit a phase slope across the band; "
                f"spacing {self.pilot_spacing} over {self.num_carriers} carriers gives "
                f"{self.num_pilots}"
            )
        if self.num_data_carriers < 1:
            raise OfdmConfigError("pilots consume every active carrier; nothing left for data")
        if self.preamble_symbols < 1:
            raise OfdmConfigError("preamble_symbols must be >= 1")
        if self.training_symbols < 2:
            # Two identical training symbols give both a 3 dB better channel
            # estimate (they average) and an honest noise estimate (they
            # differ only by noise). With one there is nothing to subtract and
            # every SNR the receiver reported would be a guess.
            raise OfdmConfigError("training_symbols must be >= 2 to measure noise")
        if len(self.preamble_carriers) < 2:
            raise OfdmConfigError("preamble needs at least 2 even-indexed active bins")
        if self.block_size < 1:
            raise OfdmConfigError("block_size must be >= 1")
        if not 0.0 < self.detection_threshold < 1.0:
            raise OfdmConfigError("detection_threshold must be in (0, 1)")
        if not 0.0 < self.tx_rms <= 1.0:
            raise OfdmConfigError("tx_rms must be in (0, 1]")

    # -- carrier geometry ---------------------------------------------------

    @property
    def carriers(self) -> np.ndarray:
        """Active FFT bin indices, ascending."""
        return np.arange(self.first_carrier, self.first_carrier + self.num_carriers)

    @property
    def pilot_positions(self) -> np.ndarray:
        """Indices *within the active set* that carry pilots."""
        return np.arange(0, self.num_carriers, self.pilot_spacing)

    @property
    def data_positions(self) -> np.ndarray:
        """Indices within the active set that carry payload symbols."""
        mask = np.ones(self.num_carriers, dtype=bool)
        mask[self.pilot_positions] = False
        return np.flatnonzero(mask)

    @property
    def pilot_carriers(self) -> np.ndarray:
        """FFT bin indices of the pilots."""
        return self.carriers[self.pilot_positions]

    @property
    def data_carriers(self) -> np.ndarray:
        """FFT bin indices carrying payload symbols."""
        return self.carriers[self.data_positions]

    @property
    def preamble_carriers(self) -> np.ndarray:
        """Even-numbered active bins.

        Filling only even bins makes the time-domain symbol repeat after
        fft_size/2 samples, which is what the Schmidl-Cox detector correlates
        against (see `sync.py`).
        """
        active = self.carriers
        return active[active % 2 == 0]

    @property
    def num_pilots(self) -> int:
        return len(self.pilot_positions)

    @property
    def num_data_carriers(self) -> int:
        return self.num_carriers - self.num_pilots

    # -- timing -------------------------------------------------------------

    @property
    def subcarrier_spacing(self) -> float:
        """Hz between adjacent subcarriers."""
        return self.sample_rate / self.fft_size

    @property
    def symbol_samples(self) -> int:
        """Samples in one OFDM symbol, cyclic prefix included."""
        return self.fft_size + self.cp_length

    @property
    def symbol_duration(self) -> float:
        """Seconds per OFDM symbol, cyclic prefix included."""
        return self.symbol_samples / self.sample_rate

    @property
    def cp_duration(self) -> float:
        """Seconds of cyclic prefix, i.e. the delay spread the guard absorbs."""
        return self.cp_length / self.sample_rate

    # -- bandwidth ----------------------------------------------------------

    @property
    def occupied_band(self) -> tuple[float, float]:
        """(low, high) edge of the occupied baseband spectrum, in Hz.

        Bin k spans roughly [k-0.5, k+0.5] subcarrier widths, so the edges sit
        half a spacing outside the outermost carriers.
        """
        df = self.subcarrier_spacing
        return ((self.first_carrier - 0.5) * df,
                (self.first_carrier + self.num_carriers - 0.5) * df)

    @property
    def occupied_bandwidth(self) -> float:
        """Occupied baseband bandwidth in Hz. NOT the sample rate, NOT an RF claim."""
        return self.num_carriers * self.subcarrier_spacing

    @property
    def bandwidth_fraction(self) -> float:
        """Occupied share of the 0..fs/2 audio band.

        The channel simulator needs this to place a stated in-band SNR: white
        noise spreads over the whole audio band, but only the part landing on
        the active carriers reaches the demodulator.
        """
        return self.occupied_bandwidth / (self.sample_rate / 2)

    # -- payload geometry ---------------------------------------------------

    def coded_bits_per_symbol(self, bits_per_carrier: int) -> int:
        """Coded bits one OFDM symbol carries at the given constellation order."""
        return self.num_data_carriers * int(bits_per_carrier)

    def describe(self) -> dict[str, object]:
        """Flat, printable summary -- used by the bench tool and the settings UI."""
        low, high = self.occupied_band
        return {
            "profile": self.name,
            "sample_rate_hz": self.sample_rate,
            "fft_size": self.fft_size,
            "cp_length": self.cp_length,
            "subcarrier_spacing_hz": round(self.subcarrier_spacing, 4),
            "active_carriers": self.num_carriers,
            "pilot_carriers": self.num_pilots,
            "data_carriers": self.num_data_carriers,
            "occupied_band_hz": (round(low, 1), round(high, 1)),
            "occupied_bandwidth_hz": round(self.occupied_bandwidth, 1),
            "symbol_duration_ms": round(self.symbol_duration * 1000, 3),
            "cp_duration_ms": round(self.cp_duration * 1000, 3),
            "block_size_bytes": self.block_size,
        }


# The one profile that exists today. It is a SIMULATION AND BENCH profile: its
# numbers were chosen to make tests deterministic and conservative, not from any
# measurement of what a VHF radio passes. Nothing outside this module may depend
# on these values.
#
#   48000 Hz / 1024      -> 46.875 Hz subcarrier spacing
#   cp 128               -> 2.667 ms guard, far longer than any audio-path
#                           delay spread, and long enough that a plateau of
#                           that width is easy to find in noise
#   bins 12..63          -> 539 Hz .. 2977 Hz, about 2.44 kHz occupied, which
#                           fits inside a stock 2.5-3 kHz FM voice channel
#   pilot spacing 7      -> 8 pilots, 44 data carriers
#   block 512 bytes      -> one Guardian attachment spans many blocks
BENCH = OfdmProfile(
    name="BENCH",
    sample_rate=48000,
    fft_size=1024,
    cp_length=128,
    first_carrier=12,
    num_carriers=52,
    pilot_spacing=7,
    preamble_symbols=2,
    training_symbols=2,
    block_size=512,
)

PROFILES: dict[str, OfdmProfile] = {BENCH.name: BENCH}

#: What a station uses until hardware measurements produce an air profile.
DEFAULT_PROFILE_NAME = BENCH.name


def profile(name: str) -> OfdmProfile:
    """Look up a registered profile by name."""
    try:
        return PROFILES[str(name)]
    except KeyError:
        raise OfdmConfigError(
            f"unknown OFDM profile {name!r}; known: {', '.join(sorted(PROFILES))}"
        ) from None


def profile_names() -> list[str]:
    """Registered profile names, ascending."""
    return sorted(PROFILES)


def profile_or_default(name: str) -> OfdmProfile:
    """Look up a profile, falling back to the default if the name is unknown.

    For the paths where a stored name that no longer exists must not stop mail
    moving -- a config written by a build that had a profile this one does not.
    Readiness reports the mismatch instead, so the operator can see it and fix it.
    """
    try:
        return profile(name)
    except OfdmConfigError:
        return PROFILES[DEFAULT_PROFILE_NAME]
