"""Production SC-FTN automatic MCS/FEC and timing policy.

The operator chooses only the physical waveform family and occupied bandwidth.
Everything that can be learned from delivery feedback is owned here so two peers
do not depend on matching a page of expert-only settings.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .coding import FecProfile, fec_spec


SUPPORTED_G2_WAVEFORMS: tuple[str, ...] = ("sc_ftn",)
SUPPORTED_G2_BANDWIDTHS: tuple[str, ...] = (
    "1K2", "2K7", "4K5", "5K", "10K", "20K",
)

SC_FTN_PROFILE_FOR_BANDWIDTH: dict[str, str] = {
    width: f"SC_FTN_{width}" for width in SUPPORTED_G2_BANDWIDTHS
}
# Historical import name retained for callers that only use the width lookup.
# It maps to SC-FTN profiles and does not expose an OFDM implementation.
OFDM_PROFILE_FOR_BANDWIDTH = SC_FTN_PROFILE_FOR_BANDWIDTH


@dataclass(frozen=True)
class G2AutomaticPolicy:
    waveform: str
    bandwidth: str
    profile_name: str
    initial_mcs: int
    maximum_mcs: int
    initial_fec: FecProfile
    initial_burst_bytes: int = 16_384
    minimum_burst_bytes: int = 2_048
    maximum_burst_bytes: int = 16_384
    arq_block_bytes: int = 2_048
    maximum_retries: int = 3
    rescue_retries: int = 4
    rescue_mcs: int = 1
    clean_bursts_to_upgrade: int = 3
    rapid_acquisition: bool = False
    # Keep every keyed data waveform below the lab's independent 20 s PTT
    # watchdog, including a learned low-rate first pass. 18 s preserves the
    # measured 15 KiB capacity fast path while leaving timing margin for lead,
    # guard and tail on radios other than the IC-705 pair used in qualification.
    maximum_train_seconds: float = 18.0
    acquisition_lead_seconds: float = 0.0
    tx_guard_ms: int = 140
    bootstrap_modulation: str = "qpsk"
    reference_metric_blocks: int = 2
    center_hz: float | None = None
    nyquist_symbol_rate: float | None = None
    symbol_rate: float | None = None

    @property
    def fec_label(self) -> str:
        return fec_spec(self.initial_fec).label

    def summary(self) -> str:
        return (
            f"AUTO MCS≤{self.maximum_mcs}, start MCS{self.initial_mcs}; "
            f"AUTO FEC, start {self.fec_label}; superframe v4, "
            f"ARQ {self.arq_block_bytes} B, burst ≤{self.maximum_burst_bytes} B; "
            f"delivery rescue MCS{self.rescue_mcs}/FEC 1/2"
        )


def normalize_g2_waveform(value: str | None) -> str:
    """Validate the negotiated family without silently changing its meaning."""
    name = str(value or "").strip().lower()
    if name != "sc_ftn":
        raise ValueError(f"unsupported waveform family {value!r}; SC-FTN is required")
    return name


def normalize_g2_bandwidth(value: str | None) -> str:
    name = str(value or "").strip().upper()
    if name not in SUPPORTED_G2_BANDWIDTHS:
        raise ValueError(f"unsupported SC-FTN bandwidth {value!r}")
    return name


def automatic_g2_policy(waveform: str | None, bandwidth: str | None, *,
                        radio_backend: str | None = None,
                        radio_model: str | None = None) -> G2AutomaticPolicy:
    family = normalize_g2_waveform(waveform)
    width = normalize_g2_bandwidth(bandwidth)
    model = str(radio_model or "").strip().lower().replace(" ", "").replace("_", "-")
    k5_hardware = (str(radio_backend or "").strip().lower() == "guardian_k5"
                   or model in {"quanshenguv-k5", "uv-k5", "quanshenguv-k5(8)",
                                "uv-k5(8)", "quanshenguv-k6", "uv-k6"})
    proven_2k7 = width == "2K7"
    backend_name = str(radio_backend or "").strip().lower()
    policy = G2AutomaticPolicy(
        waveform=family,
        bandwidth=width,
        profile_name=SC_FTN_PROFILE_FOR_BANDWIDTH[width],
        # Wider and narrower profiles begin at the measured robust capacity
        # point; delivery feedback moves MCS/FEC independently per peer.
        initial_mcs=17,
        maximum_mcs=17,
        initial_fec=FecProfile.LDPC_4_5,
        # The retained SC-FTN 2K7 geometry is a PHY setting, not calibration.
        center_hz=1779.4117647058824 if proven_2k7 else None,
        nyquist_symbol_rate=2541.176470588235 if proven_2k7 else None,
        symbol_rate=2823.529411764706 if proven_2k7 else None,
        # Only the exact Guardian K5 4K5 path owns the measured acquisition
        # lead. A model string alone cannot prove that transport.
        acquisition_lead_seconds=(
            0.8 if width == "4K5" and backend_name == "guardian_k5" else 0.0
        ),
    )
    # An audio modem cannot infer a clean path from a model name or CAT driver.
    # Every 2K7 SC path acquires with the same short robust probe; Guardian UART
    # adds control/telemetry, not permission to use the delivery rescue path.
    rapid_sc = width == "2K7"
    if rapid_sc or k5_hardware:
        return replace(
            policy,
            initial_mcs=1,
            maximum_mcs=(17 if rapid_sc else 3),
            initial_fec=FecProfile.LDPC_1_2,
            initial_burst_bytes=512 if rapid_sc else 2048,
            rapid_acquisition=rapid_sc,
            # GK5/AIOC RF qualification found a direction with roughly 9 dB
            # raw SNR but correlated FM distortion: 512-byte rescue bursts made
            # from 256-byte selective-repeat blocks completed 6/6 transfers,
            # while a single 512-byte block and MCS1 rescue did not.  Normal
            # clean traffic still aggregates these small blocks into large
            # keyed trains, so this is rescue granularity rather than a cap on
            # throughput.
            minimum_burst_bytes=512,
            maximum_burst_bytes=16_384,
            arq_block_bytes=256,
            # Preserve one same-profile retry for a transient loss, then enter
            # the measured BPSK/mother-code delivery path.  Four rescue retries
            # remain available after the switch.
            maximum_retries=1,
            maximum_train_seconds=14.5 if rapid_sc else 7.5,
            tx_guard_ms=60 if rapid_sc else 200,
            rescue_mcs=0,
        )
    return policy


__all__ = [
    "G2AutomaticPolicy", "OFDM_PROFILE_FOR_BANDWIDTH",
    "SC_FTN_PROFILE_FOR_BANDWIDTH",
    "SUPPORTED_G2_BANDWIDTHS", "SUPPORTED_G2_WAVEFORMS",
    "automatic_g2_policy", "normalize_g2_bandwidth", "normalize_g2_waveform",
]
