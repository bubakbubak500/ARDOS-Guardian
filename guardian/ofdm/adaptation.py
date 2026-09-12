"""Joint FEC and keyed-burst adaptation for the half-duplex SC-FTN link."""

from __future__ import annotations

from dataclasses import dataclass, field

from .coding import FecProfile, fec_profile, fec_spec
from .config import sc_mcs


BURST_LADDER: tuple[int, ...] = (256, 512, 1024, 2048, 4096, 8192, 16384)
ARQ_BLOCK_LADDER: tuple[int, ...] = BURST_LADDER


def fec_snr_margin_db(fec: FecProfile) -> float:
    """Extra acquisition margin above the MCS table's rate-1/2 reference."""
    return max(0.0, 14.0 * (float(fec_spec(fec).rate) - 0.5))


@dataclass(frozen=True)
class AdaptationConfig:
    adaptive_fec: bool = True
    modern_ldpc: bool = False
    fixed_fec: FecProfile = FecProfile.FEC_1_2
    adaptive_burst: bool = True
    fixed_burst_bytes: int = 4096
    min_burst_bytes: int = 512
    max_burst_bytes: int = 8192
    arq_block_bytes: int = 512
    initial_fec: FecProfile | None = None
    initial_burst_bytes: int | None = None
    clean_bursts_to_upgrade: int = 3
    loss_cooldown_bursts: int = 6
    ewma_alpha: float = 0.25
    rapid_acquisition: bool = False

    def __post_init__(self) -> None:
        fec_profile(self.fixed_fec)
        if self.initial_fec is not None:
            fec_profile(self.initial_fec)
        if self.arq_block_bytes not in ARQ_BLOCK_LADDER:
            raise ValueError("ARQ block size is not on the ARQ block ladder")
        if self.min_burst_bytes not in BURST_LADDER:
            raise ValueError("minimum burst size is not on the burst ladder")
        if self.max_burst_bytes not in BURST_LADDER:
            raise ValueError("maximum burst size is not on the burst ladder")
        if self.min_burst_bytes > self.max_burst_bytes:
            raise ValueError("minimum burst size exceeds maximum burst size")
        if self.fixed_burst_bytes not in BURST_LADDER:
            raise ValueError("fixed burst size is not on the burst ladder")
        if (self.initial_burst_bytes is not None
                and self.initial_burst_bytes not in BURST_LADDER):
            raise ValueError("initial burst size is not on the burst ladder")
        if self.min_burst_bytes < self.arq_block_bytes:
            raise ValueError("minimum burst size must hold one ARQ block")
        if self.fixed_burst_bytes < self.arq_block_bytes:
            raise ValueError("fixed burst size must hold one ARQ block")
        if (self.initial_burst_bytes is not None
                and not self.min_burst_bytes <= self.initial_burst_bytes
                <= self.max_burst_bytes):
            raise ValueError("initial burst size is outside the adaptive range")
        if self.clean_bursts_to_upgrade < 1:
            raise ValueError("clean_bursts_to_upgrade must be >= 1")
        if self.loss_cooldown_bursts < 0:
            raise ValueError("loss_cooldown_bursts cannot be negative")
        if not 0.0 < self.ewma_alpha <= 1.0:
            raise ValueError("ewma_alpha must be in (0, 1]")


@dataclass(frozen=True)
class TxProfile:
    mcs_index: int
    fec: FecProfile
    burst_bytes: int
    arq_block_bytes: int


@dataclass
class LinkAdaptationController:
    """Conservative one-owner controller, shared across backend transfers.

    Delivery feedback leads.  A failure strengthens FEC immediately; only when
    already at rate 1/2 does it shorten the burst.  A clean window upgrades one
    dimension at a time, alternating FEC and burst length to avoid coupled
    oscillation.  Fixed controls bypass either half independently.
    """

    config: AdaptationConfig = field(default_factory=AdaptationConfig)
    mcs_index: int = 1
    current_fec: FecProfile = FecProfile.FEC_1_2
    current_burst_bytes: int = 2048
    success_ewma: float | None = None
    retry_ewma: float | None = None
    goodput_ewma_bps: float | None = None
    remote_snr_ewma_db: float | None = None
    remote_evm_ewma: float | None = None
    feedback_mcs_index: int | None = None
    latest_remote_snr_db: float | None = None
    clean_streak: int = 0
    # MCS acquisition has to survive short messages.  Keeping this beside the
    # learned MCS (rather than on one ephemeral OfdmLink) lets three clean
    # one-window messages earn the same upgrade as one three-window message.
    mcs_clean_streak: int = 0
    # A loss remains evidence after the window in which it happened.  Without
    # this hysteresis, later clean windows in the same long message could
    # immediately promote the profile that had just required retransmission.
    profile_upgrade_cooldown: int = 0
    mcs_upgrade_cooldown: int = 0
    _next_upgrade: str = "fec"

    def __post_init__(self) -> None:
        default_fec = (FecProfile.LDPC_1_2 if self.config.modern_ldpc
                       else FecProfile.FEC_1_2)
        self.current_fec = (fec_profile(self.config.initial_fec)
                            if (self.config.adaptive_fec
                                and self.config.initial_fec is not None)
                            else default_fec if self.config.adaptive_fec
                            else fec_profile(self.config.fixed_fec))
        if self.config.adaptive_burst:
            start = (self.config.initial_burst_bytes
                     if self.config.initial_burst_bytes is not None
                     else max(self.config.min_burst_bytes, 2048))
            self.current_burst_bytes = min(start, self.config.max_burst_bytes)
        else:
            self.current_burst_bytes = self.config.fixed_burst_bytes

    @property
    def profile(self) -> TxProfile:
        fec = self.current_fec if self.config.adaptive_fec else fec_profile(
            self.config.fixed_fec
        )
        burst = (self.current_burst_bytes if self.config.adaptive_burst
                 else self.config.fixed_burst_bytes)
        return TxProfile(self.mcs_index, fec, burst, self.config.arq_block_bytes)

    def fec_for_retry(self, attempt: int) -> FecProfile:
        """One stronger profile per retry, never below the mother code."""
        base = self.profile.fec
        attempts = max(0, int(attempt))
        if base in {
            FecProfile.LDPC_1_2, FecProfile.LDPC_2_3,
            FecProfile.LDPC_7_10, FecProfile.LDPC_3_4,
            FecProfile.LDPC_4_5, FecProfile.LDPC_7_8,
            FecProfile.LDPC_9_10,
        }:
            ladder = [
                FecProfile.LDPC_1_2,
                FecProfile.LDPC_2_3, FecProfile.LDPC_7_10,
                FecProfile.LDPC_3_4, FecProfile.LDPC_4_5,
                FecProfile.LDPC_7_8, FecProfile.LDPC_9_10,
            ]
            if attempts >= 2:
                # The third physical attempt is the portable mother-code escape
                # hatch: lower decode cost, maximum interleaving tolerance and
                # decodable by every capacity-frame peer.
                return FecProfile.FEC_1_2
            return ladder[max(0, ladder.index(base) - attempts)]
        return FecProfile(max(int(FecProfile.FEC_1_2), int(base) - attempts))

    def report_burst(self, *, sent_blocks: int, acked_blocks: int,
                     retransmitted_bytes: int = 0,
                     unique_bytes: int = 0, elapsed_seconds: float = 0.0,
                     remote_snr_db: float | None = None,
                     remote_evm_rms: float | None = None,
                     mcs_index: int | None = None) -> None:
        sent = max(1, int(sent_blocks))
        ratio = max(0.0, min(1.0, int(acked_blocks) / sent))
        retry_ratio = max(0.0, retransmitted_bytes / max(1, unique_bytes))
        self.success_ewma = self._ewma(self.success_ewma, ratio)
        self.retry_ewma = self._ewma(self.retry_ewma, retry_ratio)
        if elapsed_seconds > 0.0 and unique_bytes > 0:
            goodput = unique_bytes * 8.0 / elapsed_seconds
            self.goodput_ewma_bps = self._ewma(self.goodput_ewma_bps, goodput)
        if (self.config.rapid_acquisition and mcs_index is not None
                and mcs_index != self.feedback_mcs_index):
            # Different constellations can have different residual distortion;
            # a QPSK probe must not dilute the next measured APSK/QAM window.
            self.feedback_mcs_index = mcs_index
            self.remote_snr_ewma_db = None
            self.remote_evm_ewma = None
        self.latest_remote_snr_db = remote_snr_db
        if remote_snr_db is not None:
            self.remote_snr_ewma_db = self._ewma(
                self.remote_snr_ewma_db, float(remote_snr_db)
            )
        if remote_evm_rms is not None:
            self.remote_evm_ewma = self._ewma(
                self.remote_evm_ewma, float(remote_evm_rms)
            )

        if ratio < 1.0:
            self.clean_streak = 0
            self.profile_upgrade_cooldown = self.config.loss_cooldown_bursts
            self._downgrade()
            return
        if self.profile_upgrade_cooldown > 0:
            self.profile_upgrade_cooldown -= 1
            self.clean_streak = 0
            return
        if (self.config.rapid_acquisition and self.config.adaptive_burst
                and retransmitted_bytes == 0 and unique_bytes >= 512
                and remote_snr_db is not None and remote_snr_db >= 10.0
                and remote_evm_rms is not None and remote_evm_rms <= 0.25):
            # Aggregation does not spend FEC margin. Frame-count and airtime
            # limits in OfdmLink still bound each transmission, and a loss
            # disables this shortcut for the ordinary recovery cooldown.
            self.current_burst_bytes = self.config.max_burst_bytes
        self.clean_streak += 1
        threshold = self.config.clean_bursts_to_upgrade
        rapid_fec_evidence = (self.config.rapid_acquisition and self.config.modern_ldpc
                and retransmitted_bytes == 0 and unique_bytes >= 2048
                and remote_snr_db is not None and remote_snr_db >= 13.0
                and remote_evm_rms is not None and remote_evm_rms <= 0.18)
        if rapid_fec_evidence:
            # A full clean window after the initial probe can earn one code-rate
            # step. The short probe itself never spends the FEC margin.
            threshold = min(threshold, 2)
        if self.clean_streak >= threshold:
            self.clean_streak = 0
            if (rapid_fec_evidence and self.config.adaptive_fec
                    and self.current_fec is FecProfile.LDPC_1_2
                    and self._fec_upgrade_allowed(FecProfile.LDPC_3_4)):
                self.current_fec = FecProfile.LDPC_3_4
                self._next_upgrade = "burst"
            else:
                self._upgrade()

    def _ewma(self, old: float | None, value: float) -> float:
        if old is None:
            return float(value)
        alpha = self.config.ewma_alpha
        return old + alpha * (float(value) - old)

    def _downgrade(self) -> None:
        if self.config.adaptive_fec:
            ladder = self._fec_ladder()
            index = ladder.index(self.current_fec)
            if index > 0:
                self.current_fec = ladder[index - 1]
                self._next_upgrade = "fec"
                return
        if self.config.adaptive_burst:
            allowed = self._allowed_bursts()
            index = allowed.index(self.current_burst_bytes)
            if index > 0:
                self.current_burst_bytes = allowed[index - 1]
        self._next_upgrade = "fec"

    def _upgrade(self) -> None:
        if self._next_upgrade == "fec" and self.config.adaptive_fec:
            ladder = self._fec_ladder()
            index = ladder.index(self.current_fec)
            if (index + 1 < len(ladder)
                    and self._fec_upgrade_allowed(ladder[index + 1])):
                self.current_fec = ladder[index + 1]
                self._next_upgrade = "burst"
                return
        if self.config.adaptive_burst:
            allowed = self._allowed_bursts()
            index = allowed.index(self.current_burst_bytes)
            if index + 1 < len(allowed):
                self.current_burst_bytes = allowed[index + 1]
                self._next_upgrade = "fec"
                return
        if self.config.adaptive_fec:
            ladder = self._fec_ladder()
            index = ladder.index(self.current_fec)
            if (index + 1 < len(ladder)
                    and self._fec_upgrade_allowed(ladder[index + 1])):
                self.current_fec = ladder[index + 1]
        self._next_upgrade = "burst"

    def _fec_upgrade_allowed(self, candidate: FecProfile) -> bool:
        """Require measured margin before increasing the data code rate.

        Delivery still has final authority: any loss steps down immediately.
        The thresholds stop a clean short window from promoting a marginal path
        to the 7/8 or 9/10 rates that were specifically unstable on the weaker
        RF direction during the two-Icom campaign.
        """
        if not self.config.modern_ldpc:
            return True
        if self.config.rapid_acquisition:
            try:
                required = sc_mcs(self.mcs_index).min_snr_db + 1.5 + fec_snr_margin_db(candidate)
            except ValueError:
                return False
            measured = self.remote_snr_ewma_db
            if measured is not None and self.latest_remote_snr_db is not None:
                measured = min(measured, self.latest_remote_snr_db)
            if measured is None or measured < required:
                return False
        minimum_snr = {
            FecProfile.LDPC_1_2: 0.0,
            FecProfile.LDPC_2_3: 8.0,
            FecProfile.LDPC_7_10: 11.0,
            FecProfile.LDPC_3_4: 14.0,
            FecProfile.LDPC_4_5: 16.5,
            FecProfile.LDPC_7_8: 23.0,
            FecProfile.LDPC_9_10: 28.0,
        }.get(candidate, 99.0)
        if (self.remote_snr_ewma_db is None
                and candidate not in {
                    FecProfile.LDPC_4_5,
                    FecProfile.LDPC_7_8,
                    FecProfile.LDPC_9_10,
                }):
            return True
        if (self.remote_snr_ewma_db is None
                or self.remote_snr_ewma_db < minimum_snr):
            return False
        maximum_evm = {
            FecProfile.LDPC_4_5: 0.16,
            FecProfile.LDPC_7_8: 0.09,
            FecProfile.LDPC_9_10: 0.06,
        }.get(candidate)
        return (maximum_evm is None or self.remote_evm_ewma is None
                or self.remote_evm_ewma <= maximum_evm)

    def _fec_ladder(self) -> list[FecProfile]:
        if self.config.modern_ldpc:
            return [
                FecProfile.LDPC_1_2,
                FecProfile.LDPC_2_3,
                FecProfile.LDPC_7_10,
                FecProfile.LDPC_3_4,
                FecProfile.LDPC_4_5,
                FecProfile.LDPC_7_8,
                FecProfile.LDPC_9_10,
            ]
        return [
            FecProfile.FEC_1_2, FecProfile.FEC_2_3,
            FecProfile.FEC_3_4, FecProfile.FEC_5_6,
            FecProfile.FEC_7_8,
        ]

    def _allowed_bursts(self) -> list[int]:
        return [size for size in BURST_LADDER
                if self.config.min_burst_bytes <= size <= self.config.max_burst_bytes]

    def summary(self) -> str:
        profile = self.profile
        spec = fec_spec(profile.fec)
        return f"FEC {spec.label}, burst {profile.burst_bytes} B, ARQ {profile.arq_block_bytes} B"
