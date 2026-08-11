"""Reviewed profiles for Guardian G2's non-OFDM waveform experiments."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WaveformProfile:
    """One complete, reproducible experimental audio waveform."""

    name: str
    family: str
    sample_rate: int = 48_000
    block_size: int = 512
    tx_rms: float = 0.18

    # Single-carrier parameters.  ``nyquist_symbol_rate`` determines the RRC
    # pulse bandwidth; ``symbol_rate`` determines how closely pulses are sent.
    center_hz: float = 1_700.0
    nyquist_symbol_rate: float = 2_400.0
    symbol_rate: float = 2_400.0
    rolloff: float = 0.125
    data_symbols_per_block: int = 60
    pilot_symbols_per_block: int = 4
    equalizer_taps: int = 31

    # SEFDM parameters.  Carrier spacing is alpha times the orthogonal spacing.
    fft_size: int = 1024
    cp_length: int = 128
    first_carrier_hz: float = 450.0
    num_carriers: int = 60
    pilot_spacing: int = 8
    sefdm_alpha: float = 0.95

    def __post_init__(self) -> None:
        if self.family not in {"sc_hs", "sc_ftn", "sefdm"}:
            raise ValueError(f"unknown waveform family {self.family!r}")
        if self.sample_rate < 8_000 or self.block_size < 1:
            raise ValueError("invalid sample rate or block size")
        if not 0.0 < self.tx_rms <= 1.0:
            raise ValueError("tx_rms must be in (0, 1]")
        if self.family.startswith("sc_"):
            spacing = self.sample_rate / self.symbol_rate
            nyquist = self.sample_rate / self.nyquist_symbol_rate
            if abs(spacing - round(spacing)) > 1e-9:
                raise ValueError("single-carrier symbol rate must divide sample rate")
            if abs(nyquist - round(nyquist)) > 1e-9:
                raise ValueError("Nyquist symbol rate must divide sample rate")
        elif not 0.5 <= self.sefdm_alpha <= 1.0:
            raise ValueError("SEFDM alpha must be in [0.5, 1.0]")

    @property
    def is_single_carrier(self) -> bool:
        return self.family.startswith("sc_")

    @property
    def ftn_tau(self) -> float:
        return self.nyquist_symbol_rate / self.symbol_rate

    @property
    def symbol_spacing_samples(self) -> int:
        return int(round(self.sample_rate / self.symbol_rate))

    @property
    def pulse_samples_per_symbol(self) -> int:
        return int(round(self.sample_rate / self.nyquist_symbol_rate))

    @property
    def num_pilots(self) -> int:
        if self.is_single_carrier:
            return self.pilot_symbols_per_block
        return len(range(0, self.num_carriers, self.pilot_spacing))

    @property
    def num_data_carriers(self) -> int:
        if self.is_single_carrier:
            return self.data_symbols_per_block
        return self.num_carriers - self.num_pilots

    @property
    def points_per_block(self) -> int:
        return self.num_data_carriers

    @property
    def physical_symbols_per_block(self) -> int:
        return self.data_symbols_per_block + self.pilot_symbols_per_block

    @property
    def orthogonal_spacing_hz(self) -> float:
        return self.sample_rate / self.fft_size

    @property
    def carrier_spacing_hz(self) -> float:
        return self.sefdm_alpha * self.orthogonal_spacing_hz

    @property
    def occupied_band(self) -> tuple[float, float]:
        if self.is_single_carrier:
            width = self.nyquist_symbol_rate * (1.0 + self.rolloff)
            return self.center_hz - width / 2.0, self.center_hz + width / 2.0
        half_lobe = self.orthogonal_spacing_hz / 2.0
        return (
            self.first_carrier_hz - half_lobe,
            self.first_carrier_hz
            + (self.num_carriers - 1) * self.carrier_spacing_hz
            + half_lobe,
        )

    @property
    def occupied_bandwidth(self) -> float:
        low, high = self.occupied_band
        return high - low

    @property
    def bandwidth_fraction(self) -> float:
        return self.occupied_bandwidth / (self.sample_rate / 2.0)

    @property
    def symbol_samples(self) -> int:
        """Representative physical block length used by the channel model."""
        if self.is_single_carrier:
            return self.physical_symbols_per_block * self.symbol_spacing_samples
        return self.fft_size + self.cp_length

    @property
    def block_duration(self) -> float:
        return self.symbol_samples / self.sample_rate

    def coded_bits_per_symbol(self, bits_per_point: int) -> int:
        return self.points_per_block * int(bits_per_point)

    def describe(self) -> dict[str, object]:
        low, high = self.occupied_band
        return {
            "profile": self.name,
            "family": self.family,
            "sample_rate_hz": self.sample_rate,
            "occupied_band_hz": (round(low, 1), round(high, 1)),
            "occupied_bandwidth_hz": round(self.occupied_bandwidth, 1),
            "points_per_block": self.points_per_block,
            "block_duration_ms": round(self.block_duration * 1000.0, 3),
            "symbol_rate": self.symbol_rate if self.is_single_carrier else None,
            "ftn_tau": self.ftn_tau if self.is_single_carrier else None,
            "sefdm_alpha": self.sefdm_alpha if self.family == "sefdm" else None,
        }


SC_HS_2K7 = WaveformProfile(name="SC_HS_2K7", family="sc_hs")
SC_FTN_2K7 = WaveformProfile(
    name="SC_FTN_2K7", family="sc_ftn", symbol_rate=48_000 / 18,
    equalizer_taps=81,
)
SEFDM_2K7 = WaveformProfile(
    name="SEFDM_2K7", family="sefdm", tx_rms=0.08,
)

PROFILE_LADDER = (SC_HS_2K7.name, SC_FTN_2K7.name, SEFDM_2K7.name)
PROFILES = {profile.name: profile for profile in (SC_HS_2K7, SC_FTN_2K7, SEFDM_2K7)}


def profile_names() -> tuple[str, ...]:
    return tuple(PROFILES)


def profile_or_default(name: str | None) -> WaveformProfile:
    return PROFILES.get(str(name or "").strip().upper(), SC_HS_2K7)
