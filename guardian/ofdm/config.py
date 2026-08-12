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
    bits_per_symbol: int | Fraction
    code_rate: Fraction
    #: Post-equalisation SNR, in dB, at which 512-byte blocks decode reliably.
    #:
    #: Measured, not derived: a sweep of `guardian.ofdm.channel` at 1 dB steps,
    #: eight 512-byte blocks per point, reading the same `residual_snr_db` the
    #: receiver reports on air. The figure is the first step where all eight
    #: decoded; the step below is where it starts to be luck. See
    #: docs/OFDM_AIR_TEST.md.
    #:
    #: It is deliberately expressed against the *post-equalisation* SNR and not
    #: the training-symbol one, because those two differed by 8 dB on the first
    #: real radios and only the first predicted what actually happened.
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


# MCS0 is the bootstrap mode: the PHY header and every ACK/NACK ride on it, so
# a receiver never has to know the payload modulation in advance. MCS1 is the
# default for data, and the first two-radio tests found it has margin to spare
# -- both it and MCS0 carried 512-byte blocks at the bottom of the swept range.
#
# MCS2 and MCS3 are where the measurement bites. The 2026-08-09 IC-705 pair
# delivered 9.7-12.0 dB post-equalisation, which puts MCS2 exactly on its
# threshold and MCS3 more than 5 dB out of reach. They stay in the table because
# a cleaner path will reach them; they are not defaults because this one did not.
MCS_TABLE: tuple[Mcs, ...] = (
    Mcs(0, "bpsk", 1, Fraction(1, 2), min_snr_db=3.0),
    Mcs(1, "qpsk", 2, Fraction(1, 2), min_snr_db=3.0),
    Mcs(2, "qam16", 4, Fraction(1, 2), min_snr_db=10.5),
    Mcs(3, "qam64", 6, Fraction(1, 2), min_snr_db=15.5),
)

# High-speed single-carrier choices.  They are intentionally absent from
# MCS_TABLE so the established OFDM pickers and defaults do not change.  The
# five-bit on-air MCS field already has room for them.  A separate lookup keeps
# the original OFDM decoder strict when it sees an experimental index on air.
SC_MCS_TABLE: tuple[Mcs, ...] = (
    *MCS_TABLE,
    Mcs(4, "qam256", 8, Fraction(1, 2), min_snr_db=24.0),
    Mcs(5, "apsk16", 4, Fraction(1, 2), min_snr_db=11.0),
    Mcs(6, "apsk32", 5, Fraction(1, 2), min_snr_db=15.0),
    # 2.3.3 experimental ladder. Existing indices 0..6 stay wire-compatible;
    # new values occupy unused positions in the five-bit MCS field.
    Mcs(7, "psk8", 3, Fraction(1, 2), min_snr_db=7.0),
    Mcs(8, "qam32", 5, Fraction(1, 2), min_snr_db=14.0),
    Mcs(9, "apsk64", 6, Fraction(1, 2), min_snr_db=17.0),
    Mcs(10, "qam128", 7, Fraction(1, 2), min_snr_db=20.5),
    Mcs(11, "apsk128", 7, Fraction(1, 2), min_snr_db=21.0),
    Mcs(12, "apsk256", 8, Fraction(1, 2), min_snr_db=25.0),
    Mcs(13, "qam512", 9, Fraction(1, 2), min_snr_db=29.0),
    Mcs(14, "apsk512", 9, Fraction(1, 2), min_snr_db=30.0),
    Mcs(15, "qam1024", 10, Fraction(1, 2), min_snr_db=34.0),
    # Geometrically compressed QAM A/B candidates. These keep Gray labels and
    # bits/symbol but trade AWGN distance for lower crest factor/nonlinearity.
    Mcs(16, "gqam16", 4, Fraction(1, 2), min_snr_db=10.5),
    Mcs(17, "gqam64", 6, Fraction(1, 2), min_snr_db=15.5),
    Mcs(18, "gqam256", 8, Fraction(1, 2), min_snr_db=24.0),
    Mcs(19, "gqam1024", 10, Fraction(1, 2), min_snr_db=34.0),
    Mcs(20, "pas64", Fraction(43, 8), Fraction(1, 2), min_snr_db=15.0),
)

#: The mode used for the PHY header and for ACK/NACK bursts.
HEADER_MCS = MCS_TABLE[0]

#: What a fresh station transmits data with until adaptation exists.
DEFAULT_MCS_INDEX = 1


def mcs(index: int) -> Mcs:
    """Look up an established OFDM MCS by index."""
    for entry in MCS_TABLE:
        if entry.index == int(index):
            return entry
    raise OfdmConfigError(f"unknown MCS index {index}")


def sc_mcs(index: int) -> Mcs:
    """Look up an MCS supported by the experimental single-carrier PHYs."""
    for entry in SC_MCS_TABLE:
        if entry.index == int(index):
            return entry
    raise OfdmConfigError(f"unknown experimental MCS index {index}")


def best_mcs_for(snr_db: float | None) -> Mcs | None:
    """The fastest MCS a link of this post-equalisation SNR carries reliably.

    None when there is no measurement to judge by, which is not the same as
    "use the slowest" -- an unmeasured link has not been shown to be bad.
    """
    if snr_db is None:
        return None
    usable = [entry for entry in MCS_TABLE if snr_db >= entry.min_snr_db]
    return max(usable, key=lambda entry: entry.index) if usable else None


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


# ---------------------------------------------------------------------------- #
#  The profile ladder
# ---------------------------------------------------------------------------- #
#
# None of these is a proven air profile. They exist so the question "how much
# bandwidth does this radio actually pass?" can be answered by trying them,
# which is the only way it can be answered -- what matters is not the radio's
# channel spacing but the audio bandwidth its receive path passes, and that
# differs between two taps on the same radio.
#
# Everything in the ladder shares one deliberate choice: **46.875 Hz subcarrier
# spacing**, from 48000/1024 and equally from 96000/2048. Frequency-offset
# tolerance is set by the spacing, so a burst that survives a given dial error
# on one profile survives it on all of them, and moving up the ladder changes
# only the bandwidth. The 2.667 ms guard is likewise held constant, so the
# multipath the link tolerates does not change either.
#
# Widening costs SNR: the same transmit level spread over twice the carriers is
# 3 dB less per carrier. Measured across this ladder, 2.4 kHz -> 18.8 kHz costs
# about 9 dB and buys about eight times the throughput. That trade is the
# operator's to make with a real signal in front of them, which is why the
# ladder is exposed rather than one value being guessed here.

def _at_48k(name: str, first: int, count: int) -> OfdmProfile:
    """A 48 kHz profile: 46.875 Hz spacing, 2.667 ms guard, 24 ms symbols."""
    return OfdmProfile(
        name=name, sample_rate=48000, fft_size=1024, cp_length=128,
        first_carrier=first, num_carriers=count, pilot_spacing=7,
        preamble_symbols=2, training_symbols=2, block_size=512,
    )


#: The simulation and bench reference. Its numbers were chosen to make tests
#: deterministic and conservative, not from any measurement of a radio, and the
#: whole test suite is written against them -- so it is the profile to compare
#: everything else with, and the one to leave alone. Bins 12..63 put it inside a
#: stock 2.5-3 kHz FM voice channel.
BENCH = _at_48k("BENCH", 12, 52)

#: For a radio whose audio is narrower than expected, or a badly crowded channel.
#: The header costs 13 of the symbols in a 512-byte burst here, so the overhead
#: is severe -- this is a fallback, not a starting point.
NARROW_1K2 = _at_48k("NARROW_1K2", 12, 26)

#: The rungs to try on air, in order. Each roughly doubles the previous one.
WIDE_5K = _at_48k("WIDE_5K", 12, 104)
WIDE_10K = _at_48k("WIDE_10K", 12, 208)

#: As wide as 48 kHz sampling allows with room to spare below the Nyquist bin.
WIDE_20K = _at_48k("WIDE_20K", 12, 400)

#: Past 24 kHz of audio the sound card has to run faster, so this one samples at
#: 96 kHz with the FFT and guard scaled to match. It needs a sound card that will
#: open at 96 kHz; if one will not, the ladder stops at WIDE_20K.
WIDE_40K = OfdmProfile(
    name="WIDE_40K", sample_rate=96000, fft_size=2048, cp_length=256,
    first_carrier=24, num_carriers=854, pilot_spacing=7,
    preamble_symbols=2, training_symbols=2, block_size=512,
)

PROFILES: dict[str, OfdmProfile] = {
    profile.name: profile
    for profile in (NARROW_1K2, BENCH, WIDE_5K, WIDE_10K, WIDE_20K, WIDE_40K)
}

#: Ascending by occupied bandwidth -- the order to try them in on air, and the
#: order to list them in. `profile_names()` sorts alphabetically, which would put
#: NARROW between the WIDEs and read as nonsense in a picker.
PROFILE_LADDER: tuple[str, ...] = tuple(
    entry.name for entry in sorted(PROFILES.values(),
                                   key=lambda item: item.occupied_bandwidth)
)

#: What a station uses until measurements on a real radio say otherwise.
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
