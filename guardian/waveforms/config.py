"""Production SC-FTN audio profiles.

The G2 modem carried several experimental waveform families in this package.
G1 deliberately exposes one negotiated family, SC-FTN, while retaining the
profile object and helper names used by the payload backend.  Keeping the
geometry here makes the PHY, airtime accounting and policy read the same
numbers at every layer.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WaveformProfile:
    """One complete, reproducible SC-FTN audio waveform."""

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
    symbol_clock_tracking: bool = False
    # Fixed profile-known bootstrap for PHY headers, manifests and control
    # payloads. BPSK remains the compatibility default; capacity experiments
    # can select a denser robust constellation without making it self-describing.
    bootstrap_modulation: str = "bpsk"
    # Optional carrier-only interval before a DATA burst's synchronisation
    # preamble.  Some FM receivers keep their discriminator/USB audio muted for
    # several hundred milliseconds after a cold squelch opening.  Keeping this
    # delay inside the described waveform makes it measurable and lets the
    # scheduler account for it; zero preserves every reviewed production profile.
    data_acquisition_lead_seconds: float = 0.0
    # Zero keeps full per-subblock reference EVM/GMI accounting. A positive
    # value samples that many evenly spaced blocks so live ACK latency does not
    # scale with a large superframe's diagnostic work.
    reference_metric_blocks: int = 0

    # Retained geometry fields keep serialized/profile-tool callers source
    # compatible.  SC-FTN does not use an FFT or cyclic prefix.
    fft_size: int = 1024
    cp_length: int = 128
    first_carrier_hz: float = 450.0
    num_carriers: int = 60
    pilot_spacing: int = 8
    sefdm_alpha: float = 0.95

    def __post_init__(self) -> None:
        if self.family != "sc_ftn":
            raise ValueError(f"unknown waveform family {self.family!r}")
        if self.sample_rate < 8_000 or self.block_size < 1:
            raise ValueError("invalid sample rate or block size")
        if not 0.0 < self.tx_rms <= 1.0:
            raise ValueError("tx_rms must be in (0, 1]")
        if self.bootstrap_modulation not in {"bpsk", "qpsk", "psk8"}:
            raise ValueError("bootstrap_modulation must be bpsk, qpsk or psk8")
        if not 0.0 <= self.data_acquisition_lead_seconds <= 2.0:
            raise ValueError("data_acquisition_lead_seconds must be in [0, 2]")
        if not 0 <= self.reference_metric_blocks <= 64:
            raise ValueError("reference_metric_blocks must be in [0, 64]")
        if self.symbol_rate <= 0.0 or self.nyquist_symbol_rate <= 0.0:
            raise ValueError("single-carrier symbol rates must be positive")
        if self.sample_rate / self.nyquist_symbol_rate < 2.2:
            raise ValueError("single-carrier pulse needs at least 2.2 samples/symbol")
        if self.equalizer_taps < 3 or self.equalizer_taps % 2 == 0:
            raise ValueError("single-carrier equalizer_taps must be odd and >= 3")
        if self.equalizer_mode not in {"lmmse", "mmse"}:
            raise ValueError("SC-FTN equalizer_mode must be lmmse or mmse")
        if not 1 <= self.equalizer_iterations <= 8:
            raise ValueError("equalizer_iterations must be in [1, 8]")
        if self.equalizer_ridge_floor <= 0.0:
            raise ValueError("equalizer_ridge_floor must be positive")

    @property
    def is_single_carrier(self) -> bool:
        return True

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
        return self.pilot_symbols_per_block

    @property
    def num_data_carriers(self) -> int:
        return self.data_symbols_per_block

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
        width = self.nyquist_symbol_rate * (1.0 + self.rolloff)
        return self.center_hz - width / 2.0, self.center_hz + width / 2.0

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
        return int(round(
            self.physical_symbols_per_block * self.symbol_spacing_samples
        ))

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
            "data_acquisition_lead_ms": round(
                self.data_acquisition_lead_seconds * 1000.0, 3
            ),
            "symbol_rate": self.symbol_rate,
            "ftn_tau": self.ftn_tau,
            "equalizer_mode": self.equalizer_mode,
            "symbol_clock_tracking": self.symbol_clock_tracking,
            "equalizer_iterations": self.equalizer_iterations,
        }


_BANDWIDTHS: tuple[tuple[str, float], ...] = (
    ("1K2", 1_200.0),
    ("2K7", 2_700.0),
    ("4K5", 4_500.0),
    ("5K", 5_000.0),
    ("10K", 10_000.0),
    # Leave conversion margin below the 24 kHz Nyquist edge of a 48 kHz sound
    # card for the widest SC-FTN profile.
    ("20K", 18_750.0),
)


def _band_edges(label: str, bandwidth: float) -> tuple[float, float]:
    if label in {"2K7", "4K5"}:
        return 350.0, 350.0 + bandwidth
    low = 450.0
    return low, low + bandwidth


def _single_profile(family: str, label: str, bandwidth: float) -> WaveformProfile:
    low, high = _band_edges(label, bandwidth)
    beta = 0.125
    tau = 0.90
    mode = "mmse"
    return WaveformProfile(
        name=f"{family.upper()}_{label}", family=family,
        center_hz=(low + high) / 2.0,
        nyquist_symbol_rate=bandwidth / (1.0 + beta),
        symbol_rate=bandwidth / ((1.0 + beta) * tau),
        rolloff=beta,
        equalizer_taps=81,
        equalizer_mode=mode,
        equalizer_iterations=2,
        noise_whitening=True,
        symbol_clock_tracking=(label == "2K7"),
    )


_SC_FTN_PROFILES = tuple(
    _single_profile("sc_ftn", label, width) for label, width in _BANDWIDTHS
)
SC_FTN_2K7 = next(item for item in _SC_FTN_PROFILES if item.name == "SC_FTN_2K7")

FAMILY_PROFILE_LADDERS: dict[str, tuple[str, ...]] = {
    "sc_ftn": tuple(item.name for item in _SC_FTN_PROFILES),
}
PROFILE_LADDER = tuple(
    name for family in ("sc_ftn",)
    for name in FAMILY_PROFILE_LADDERS[family]
)
PROFILES = {
    profile.name: profile
    for profile in _SC_FTN_PROFILES
}


def family_profile_names(family: str) -> tuple[str, ...]:
    try:
        return FAMILY_PROFILE_LADDERS[str(family).strip().lower()]
    except KeyError:
        return ()


def profile_for(family: str, bandwidth: str = "2K7") -> WaveformProfile:
    name = f"{str(family).strip().upper()}_{str(bandwidth).strip().upper()}"
    if str(family).strip().lower() != "sc_ftn":
        raise ValueError(f"unsupported waveform family {family!r}; SC-FTN is required")
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(f"unsupported SC-FTN bandwidth {bandwidth!r}") from None


def profile_names() -> tuple[str, ...]:
    return tuple(PROFILES)


def profile_or_default(name: str | None) -> WaveformProfile:
    return PROFILES.get(str(name or "").strip().upper(), SC_FTN_2K7)
