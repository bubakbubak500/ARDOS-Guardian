"""Joint FEC and keyed-burst adaptation for the half-duplex OFDM link."""

from __future__ import annotations

from dataclasses import dataclass, field

from .coding import FecProfile, fec_profile, fec_spec


BURST_LADDER: tuple[int, ...] = (256, 512, 1024, 2048, 4096, 8192, 16384)


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
    clean_bursts_to_upgrade: int = 3
    ewma_alpha: float = 0.25

    def __post_init__(self) -> None:
        fec_profile(self.fixed_fec)
        if self.arq_block_bytes not in (256, 512, 1024):
            raise ValueError("ARQ block size must be 256, 512 or 1024 bytes")
        if self.min_burst_bytes not in BURST_LADDER:
            raise ValueError("minimum burst size is not on the burst ladder")
        if self.max_burst_bytes not in BURST_LADDER:
            raise ValueError("maximum burst size is not on the burst ladder")
        if self.min_burst_bytes > self.max_burst_bytes:
            raise ValueError("minimum burst size exceeds maximum burst size")
        if self.fixed_burst_bytes not in BURST_LADDER:
            raise ValueError("fixed burst size is not on the burst ladder")
        if self.min_burst_bytes < self.arq_block_bytes:
            raise ValueError("minimum burst size must hold one ARQ block")
        if self.fixed_burst_bytes < self.arq_block_bytes:
            raise ValueError("fixed burst size must hold one ARQ block")
        if self.clean_bursts_to_upgrade < 1:
            raise ValueError("clean_bursts_to_upgrade must be >= 1")
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
    clean_streak: int = 0
    _next_upgrade: str = "fec"

    def __post_init__(self) -> None:
        self.current_fec = ((FecProfile.LDPC_1_2 if self.config.modern_ldpc
                             else FecProfile.FEC_1_2) if self.config.adaptive_fec
                            else fec_profile(self.config.fixed_fec))
        if self.config.adaptive_burst:
            start = max(self.config.min_burst_bytes, 2048)
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
        if base in {FecProfile.LDPC_1_2, FecProfile.LDPC_3_4, FecProfile.LDPC_9_10}:
            ladder = [
                FecProfile.FEC_1_2, FecProfile.LDPC_1_2,
                FecProfile.LDPC_3_4, FecProfile.LDPC_9_10,
            ]
            return ladder[max(0, ladder.index(base) - attempts)]
        return FecProfile(max(int(FecProfile.FEC_1_2), int(base) - attempts))

    def report_burst(self, *, sent_blocks: int, acked_blocks: int,
                     retransmitted_bytes: int = 0,
                     unique_bytes: int = 0, elapsed_seconds: float = 0.0,
                     remote_snr_db: float | None = None,
                     remote_evm_rms: float | None = None) -> None:
        sent = max(1, int(sent_blocks))
        ratio = max(0.0, min(1.0, int(acked_blocks) / sent))
        retry_ratio = max(0.0, retransmitted_bytes / max(1, unique_bytes))
        self.success_ewma = self._ewma(self.success_ewma, ratio)
        self.retry_ewma = self._ewma(self.retry_ewma, retry_ratio)
        if elapsed_seconds > 0.0 and unique_bytes > 0:
            goodput = unique_bytes * 8.0 / elapsed_seconds
            self.goodput_ewma_bps = self._ewma(self.goodput_ewma_bps, goodput)
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
            self._downgrade()
            return
        self.clean_streak += 1
        if self.clean_streak >= self.config.clean_bursts_to_upgrade:
            self.clean_streak = 0
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
            if index + 1 < len(ladder):
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
            if index + 1 < len(ladder):
                self.current_fec = ladder[index + 1]
        self._next_upgrade = "burst"

    def _fec_ladder(self) -> list[FecProfile]:
        if self.config.modern_ldpc:
            return [
                FecProfile.LDPC_1_2,
                FecProfile.LDPC_3_4,
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
