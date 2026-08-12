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
    # Rates no longer have to divide the device sample rate.  The PHY uses a
    # bounded fractional-delay pulse renderer/sampler for the wide profiles.
    center_hz: float = 1_700.0
    nyquist_symbol_rate: float = 2_400.0
    symbol_rate: float = 2_400.0
    rolloff: float = 0.125
    data_symbols_per_block: int = 60
    pilot_symbols_per_block: int = 4
    equalizer_taps: int = 31
    equalizer_mode: str = "mmse"
    equalizer_iterations: int = 1
    equalizer_ridge_floor: float = 1e-7
    noise_whitening: bool = False

    # SEFDM parameters.  Carrier spacing is alpha times the orthogonal spacing.
    fft_size: int = 1024
    cp_length: int = 128
    first_carrier_hz: float = 450.0
    num_carriers: int = 60
    pilot_spacing: int = 8
    sefdm_alpha: float = 0.95

    def __post_init__(self) -> None:
        if self.family not in {"sc_hs", "sc_ftn", "sc_fde_ftn", "sefdm"}:
            raise ValueError(f"unknown waveform family {self.family!r}")
        if self.sample_rate < 8_000 or self.block_size < 1:
            raise ValueError("invalid sample rate or block size")
        if not 0.0 < self.tx_rms <= 1.0:
            raise ValueError("tx_rms must be in (0, 1]")
        if self.family.startswith("sc_"):
            if self.symbol_rate <= 0.0 or self.nyquist_symbol_rate <= 0.0:
                raise ValueError("single-carrier symbol rates must be positive")
            if self.sample_rate / self.nyquist_symbol_rate < 2.2:
                raise ValueError("single-carrier pulse needs at least 2.2 samples/symbol")
            if self.equalizer_taps < 3 or self.equalizer_taps % 2 == 0:
                raise ValueError("single-carrier equalizer_taps must be odd and >= 3")
            if self.equalizer_mode not in {"lmmse", "mmse", "sc_fde"}:
                raise ValueError("unknown single-carrier equalizer mode")
            if not 1 <= self.equalizer_iterations <= 8:
                raise ValueError("equalizer_iterations must be in [1, 8]")
            if self.equalizer_ridge_floor <= 0.0:
                raise ValueError("equalizer_ridge_floor must be positive")
        elif not 0.5 <= self.sefdm_alpha <= 1.0:
            raise ValueError("SEFDM alpha must be in [0.5, 1.0]")

    @property
    def is_single_carrier(self) -> bool:
        return self.family.startswith("sc_")

    @property
    def ftn_tau(self) -> float:
        return self.nyquist_symbol_rate / self.symbol_rate

    @property
    def symbol_spacing_samples(self) -> float:
        return self.sample_rate / self.symbol_rate

    @property
    def pulse_samples_per_symbol(self) -> float:
        return self.sample_rate / self.nyquist_symbol_rate

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
            return int(round(
                self.physical_symbols_per_block * self.symbol_spacing_samples
            ))
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
            "equalizer_mode": self.equalizer_mode if self.is_single_carrier else None,
            "equalizer_iterations": (
                self.equalizer_iterations if self.is_single_carrier else None
            ),
            "sefdm_alpha": self.sefdm_alpha if self.family == "sefdm" else None,
        }


_BANDWIDTHS: tuple[tuple[str, float], ...] = (
    ("1K2", 1_200.0),
    ("2K7", 2_700.0),
    ("5K", 5_000.0),
    ("10K", 10_000.0),
    # Match the established OFDM WIDE_20K occupied span and leave conversion
    # margin below the 24 kHz Nyquist edge of a 48 kHz sound card.
    ("20K", 18_750.0),
)


def _band_edges(label: str, bandwidth: float) -> tuple[float, float]:
    if label == "2K7":
        return 350.0, 3_050.0
    low = 450.0
    return low, low + bandwidth


def _single_profile(family: str, label: str, bandwidth: float) -> WaveformProfile:
    low, high = _band_edges(label, bandwidth)
    beta = 0.125
    tau = 1.0 if family == "sc_hs" else 0.90
    mode = "sc_fde" if family == "sc_fde_ftn" else "mmse"
    return WaveformProfile(
        name=f"{family.upper()}_{label}", family=family,
        center_hz=(low + high) / 2.0,
        nyquist_symbol_rate=bandwidth / (1.0 + beta),
        symbol_rate=bandwidth / ((1.0 + beta) * tau),
        rolloff=beta,
        equalizer_taps=(31 if family == "sc_hs" else 81),
        equalizer_mode=mode,
        equalizer_iterations=(1 if family == "sc_hs" else 2),
        noise_whitening=(family != "sc_hs"),
    )


def _sefdm_profile(label: str, bandwidth: float) -> WaveformProfile:
    spacing = 48_000 / 1024
    alpha = 0.985
    carriers = max(8, int(round((bandwidth - spacing) / (alpha * spacing))) + 1)
    # Preserve the reviewed 2.3.1/2.3.2 carrier grid exactly. Wider profiles
    # extend upward from the same first carrier so existing radio captures keep
    # the same low-edge behaviour.
    first_carrier = 450.0
    return WaveformProfile(
        name=f"SEFDM_{label}", family="sefdm", tx_rms=0.08,
        first_carrier_hz=first_carrier,
        num_carriers=carriers, pilot_spacing=6, sefdm_alpha=alpha,
    )


_SC_HS_PROFILES = tuple(
    _single_profile("sc_hs", label, width) for label, width in _BANDWIDTHS
)
_SC_FTN_PROFILES = tuple(
    _single_profile("sc_ftn", label, width) for label, width in _BANDWIDTHS
)
_SC_FDE_FTN_PROFILES = tuple(
    _single_profile("sc_fde_ftn", label, width) for label, width in _BANDWIDTHS
)
_SEFDM_PROFILES = tuple(
    _sefdm_profile(label, width) for label, width in _BANDWIDTHS
)

SC_HS_2K7 = next(item for item in _SC_HS_PROFILES if item.name == "SC_HS_2K7")
SC_FTN_2K7 = next(item for item in _SC_FTN_PROFILES if item.name == "SC_FTN_2K7")
SC_FDE_FTN_2K7 = next(
    item for item in _SC_FDE_FTN_PROFILES if item.name == "SC_FDE_FTN_2K7"
)
SEFDM_2K7 = next(item for item in _SEFDM_PROFILES if item.name == "SEFDM_2K7")

FAMILY_PROFILE_LADDERS: dict[str, tuple[str, ...]] = {
    "sc_hs": tuple(item.name for item in _SC_HS_PROFILES),
    "sc_ftn": tuple(item.name for item in _SC_FTN_PROFILES),
    "sc_fde_ftn": tuple(item.name for item in _SC_FDE_FTN_PROFILES),
    "sefdm": tuple(item.name for item in _SEFDM_PROFILES),
}
PROFILE_LADDER = tuple(
    name for family in ("sc_hs", "sc_ftn", "sc_fde_ftn", "sefdm")
    for name in FAMILY_PROFILE_LADDERS[family]
)
PROFILES = {
    profile.name: profile
    for profile in (
        *_SC_HS_PROFILES, *_SC_FTN_PROFILES,
        *_SC_FDE_FTN_PROFILES, *_SEFDM_PROFILES,
    )
}


def family_profile_names(family: str) -> tuple[str, ...]:
    try:
        return FAMILY_PROFILE_LADDERS[str(family).strip().lower()]
    except KeyError:
        return ()


def profile_for(family: str, bandwidth: str = "2K7") -> WaveformProfile:
    name = f"{str(family).strip().upper()}_{str(bandwidth).strip().upper()}"
    return PROFILES.get(name, SC_HS_2K7)


def profile_names() -> tuple[str, ...]:
    return tuple(PROFILES)


def profile_or_default(name: str | None) -> WaveformProfile:
    return PROFILES.get(str(name or "").strip().upper(), SC_HS_2K7)
