"""Joint FEC and keyed-burst adaptation for the half-duplex SC-FTN link."""

from __future__ import annotations

from dataclasses import dataclass, field
import math

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

    @property
    def recovery_cooldown_bursts(self) -> int:
        # Rapid SC uses remembered failed profiles and improved DATA quality,
        # rather than a fixed number of idle recovery windows.
        return 0 if self.rapid_acquisition else self.loss_cooldown_bursts

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
    """One controller per directed path, shared across backend transfers.

    SC AUTO explores bounded MCS/FEC profiles using DATA delivery feedback.
    Other callers retain the incremental FEC/burst policy in report_burst.
    Fixed controls bypass their corresponding adaptive choices.
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
    # One bounded experiment at a time. A clean long window can earn a trial
    # even when a heuristic SNR threshold refuses admission indefinitely.
    fec_probe_from: FecProfile | None = None
    mcs_probe_from: int | None = None
    capacity_probe_next: str = "mcs"
    capacity_probe_profile: TxProfile | None = None
    capacity_probe_quality: tuple[float, float] | None = None
    capacity_rejections: dict[tuple[int, int, int, int], tuple[float, float]] = field(default_factory=dict)
    capacity_quality: dict[tuple[int, int], tuple[float, float]] = field(default_factory=dict)
    capacity_window_ceiling: dict[tuple[int, int], tuple[int, tuple[float, float]]] = field(default_factory=dict)
    capacity_ceiling_clean_streak: dict[tuple[int, int], int] = field(default_factory=dict)
    capacity_loss_cooldown: int = 0
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
        if self.fec_probe_from is not None or self.mcs_probe_from is not None:
            burst = min(burst, max(2048, self.config.arq_block_bytes))
        return TxProfile(self.mcs_index, fec, burst, self.config.arq_block_bytes)

    def warm_start_burst_bytes(self) -> int:
        """Validate a learned path with useful data before a long window."""
        initial = (self.config.initial_burst_bytes
                   or self.config.min_burst_bytes)
        if ((self.mcs_index, int(self.current_fec)) not in self.capacity_quality
                or self.capacity_probe_profile is not None):
            return initial
        return max(initial, min(self.current_burst_bytes, 4096))

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

    def report_capacity_burst(self, *, mcs_ladder, sent_blocks: int,
                              acked_blocks: int, retransmitted_bytes: int,
                              unique_bytes: int, elapsed_seconds: float,
                              remote_snr_db: float | None,
                              remote_evm_rms: float | None,
                              mcs_index: int,
                              remaining_bytes: int | None = None) -> None:
        """Explore this directed path, one bounded profile trial per ACK.

        SNR/EVM can select the first short probe. Thereafter one clean DATA
        window is evidence to try the next density or code rate. A failed trial
        restores its actual baseline and is remembered until quality measured
        at that same baseline improves. ACK reception quality is never input.
        """
        self.report_burst(
            sent_blocks=sent_blocks, acked_blocks=acked_blocks,
            retransmitted_bytes=retransmitted_bytes, unique_bytes=unique_bytes,
            elapsed_seconds=elapsed_seconds, remote_snr_db=remote_snr_db,
            remote_evm_rms=remote_evm_rms, mcs_index=mcs_index,
            adapt_profile=False,
        )
        if retransmitted_bytes:
            # Rescue can use another modulation and code rate. It cannot
            # qualify or disqualify the regular DATA profile a second time.
            return
        current = self.profile
        baseline = self.capacity_probe_profile
        quality = (remote_snr_db, remote_evm_rms)
        def measured(pair):
            return (pair[0] is not None and pair[1] is not None
                    and math.isfinite(pair[0]) and math.isfinite(pair[1])
                    and pair[0] > 0.0 and 0.0 < pair[1] < 1.0)

        def key(target, source):
            return (target.mcs_index, int(target.fec), source.mcs_index, int(source.fec))

        if acked_blocks < sent_blocks:
            recent_loss = self.capacity_loss_cooldown > 0
            self.capacity_loss_cooldown = 2
            if (baseline is not None
                    and current.mcs_index == baseline.mcs_index
                    and current.fec != baseline.fec and sent_blocks >= 4):
                candidate_density = (sc_mcs(current.mcs_index).bits_per_symbol
                                     * fec_spec(current.fec).rate)
                baseline_density = (sc_mcs(baseline.mcs_index).bits_per_symbol
                                    * fec_spec(baseline.fec).rate)
                if (acked_blocks * candidate_density
                        >= sent_blocks * baseline_density * 1.25):
                    # A sparse CRC loss can still make the faster code clearly
                    # more productive than its proven baseline. Selectively
                    # repair the missing block and confirm it with a bounded
                    # window rather than sending the entire remainder at 1/2.
                    self.current_burst_bytes = min(
                        self.config.max_burst_bytes,
                        max(self.config.min_burst_bytes,
                            2 * current.burst_bytes),
                    )
                    self.fec_probe_from = self.mcs_probe_from = None
                    self.capacity_probe_profile = self.capacity_probe_quality = None
                    return
            if baseline is None and measured(quality):
                required_snr = (sc_mcs(current.mcs_index).min_snr_db
                                + fec_snr_margin_db(current.fec))
                position = next((i for i, item in enumerate(mcs_ladder)
                                 if item.index == current.mcs_index), 0)
                if (position > 0
                        and sc_mcs(current.mcs_index).bits_per_symbol >= 7
                        and remote_snr_db < required_snr - 1.0):
                    # CRC loss with insufficient measured margin is evidence
                    # against the constellation, not only the code rate.
                    self.mcs_index = mcs_ladder[position - 1].index
                    self.current_burst_bytes = max(
                        self.config.min_burst_bytes,
                        min(current.burst_bytes // 2, self.current_burst_bytes),
                    )
                    return
            if (baseline is None and current.fec != FecProfile.LDPC_1_2
                    and sent_blocks >= 16 and acked_blocks * 8 >= sent_blocks * 7
                    and not recent_loss
                    and current.burst_bytes > self.config.min_burst_bytes):
                # A short FEC trial can pass while a larger frame loses a few
                # blocks. Keep the working code rate, selectively repair those
                # blocks and bound the next DATA window. One missing block can
                # retain the observed window; multiple misses halve it.
                # Repeated loss at the minimum takes the FEC fallback below.
                observed = sent_blocks * current.arq_block_bytes
                target = (observed if sent_blocks - acked_blocks == 1
                          else math.ceil(observed / 2))
                shorter = next((size for size in self._allowed_bursts()
                                if size >= target), self.config.max_burst_bytes)
                self.current_burst_bytes = max(
                    self.config.min_burst_bytes,
                    min(current.burst_bytes, shorter),
                )
                if measured(quality):
                    ceiling_key = (current.mcs_index, int(current.fec))
                    self.capacity_window_ceiling[ceiling_key] = (
                        self.current_burst_bytes, quality,
                    )
                    self.capacity_ceiling_clean_streak[ceiling_key] = 0
                return
            # A failed *trial* returns to its known baseline. Once that trial
            # has succeeded, the next ordinary DATA loss must step down the
            # physical ladder instead of jumping all the way back to the
            # robust profile that preceded initial acquisition.
            fallback = baseline
            partial_probe = False
            if (baseline is not None and current.mcs_index != baseline.mcs_index
                    and sent_blocks >= 4 and acked_blocks * 4 >= sent_blocks * 3):
                # A bounded probe with only sparse CRC loss is evidence that
                # the path is close to the candidate. If acquisition skipped
                # several densities, keep the delivered blocks and retreat
                # one real modulation step instead of sending the remainder
                # at the original robust bootstrap rate.
                position = next((i for i, item in enumerate(mcs_ladder)
                                 if item.index == current.mcs_index), 0)
                previous = mcs_ladder[max(0, position - 1)].index
                if previous != baseline.mcs_index:
                    fallback = TxProfile(
                        previous, current.fec, self.current_burst_bytes,
                        current.arq_block_bytes,
                    )
                    partial_probe = True
            if fallback is None or (fallback.mcs_index, fallback.fec) == (current.mcs_index, current.fec):
                fecs = (self._fec_ladder() if self.config.adaptive_fec
                        else [current.fec])
                position = next((i for i, item in enumerate(mcs_ladder)
                                 if item.index == current.mcs_index), 0)
                fallback = TxProfile(
                    current.mcs_index if fecs.index(current.fec) else mcs_ladder[max(0, position - 1)].index,
                    fecs[max(0, fecs.index(current.fec) - 1)],
                    self.current_burst_bytes, current.arq_block_bytes,
                )
            failed_quality = (quality if partial_probe
                              else self.capacity_probe_quality if baseline is not None
                              else self.capacity_quality.get((fallback.mcs_index, int(fallback.fec))))
            if failed_quality is not None and measured(failed_quality):
                self.capacity_rejections[key(current, fallback)] = failed_quality
            self.mcs_index, self.current_fec = fallback.mcs_index, fallback.fec
            self.fec_probe_from = self.mcs_probe_from = None
            self.capacity_probe_profile = self.capacity_probe_quality = None
            # At the robust floor, reduce exposure as well as retaining ARQ.
            if (fallback.mcs_index, fallback.fec) == (current.mcs_index, current.fec):
                self.current_burst_bytes = max(self.config.min_burst_bytes, self.current_burst_bytes // 2)
            return

        if baseline is not None:
            self.fec_probe_from = self.mcs_probe_from = None
            self.capacity_probe_profile = self.capacity_probe_quality = None
        if not measured(quality):
            return
        self.capacity_quality[(current.mcs_index, int(current.fec))] = quality
        if self.config.adaptive_burst and unique_bytes >= 512:
            if baseline is not None and baseline.fec != current.fec:
                # First grow a newly confirmed FEC to only twice its proven
                # trial size. A successful 2 KiB frame is not evidence that a
                # 16 KiB window survives the same path.
                desired = max(self.config.min_burst_bytes, 2 * unique_bytes)
            elif current.fec == FecProfile.LDPC_1_2:
                desired = self.config.max_burst_bytes
            else:
                desired = max(self.config.min_burst_bytes,
                              2 * self.current_burst_bytes)
            ceiling_key = (current.mcs_index, int(current.fec))
            ceiling = self.capacity_window_ceiling.get(ceiling_key)
            if ceiling is not None:
                limit, failed_quality = ceiling
                if (remote_snr_db >= failed_quality[0] + 1.5
                        or remote_evm_rms <= failed_quality[1] * 0.85):
                    del self.capacity_window_ceiling[ceiling_key]
                    self.capacity_ceiling_clean_streak.pop(ceiling_key, None)
                else:
                    clean = self.capacity_ceiling_clean_streak.get(ceiling_key, 0)
                    if unique_bytes >= limit:
                        clean += 1
                    self.capacity_ceiling_clean_streak[ceiling_key] = clean
                    if (clean >= 2 and remaining_bytes is not None
                            and remaining_bytes >= 2 * limit):
                        del self.capacity_window_ceiling[ceiling_key]
                        del self.capacity_ceiling_clean_streak[ceiling_key]
                    else:
                        desired = min(desired, limit)
            self.current_burst_bytes = min(
                self.config.max_burst_bytes,
                next((size for size in self._allowed_bursts()
                      if size >= desired), self.config.max_burst_bytes),
            )
        current = self.profile
        if self.capacity_loss_cooldown:
            self.capacity_loss_cooldown -= 1
            return
        # A confirmed short modulation trial must not spend nearly the entire
        # message at rate 1/2 before trying a faster code. This is a bounded
        # DATA/CRC experiment, based on the measured path rather than radio ID.
        # Requiring enough payload left lets the extra DATA/ACK turn pay back.
        short_fec_opportunity = (
            baseline is not None
            and baseline.mcs_index != current.mcs_index
            and current.fec == FecProfile.LDPC_1_2
            and sc_mcs(current.mcs_index).bits_per_symbol >= 4
            and unique_bytes >= max(2048, 8 * current.arq_block_bytes)
            and remaining_bytes is not None and remaining_bytes >= 6144
            and remote_snr_db >= sc_mcs(current.mcs_index).min_snr_db + 2.5
            and remote_evm_rms <= 0.13
        )
        if (remaining_bytes is not None
                and remaining_bytes <= max(8192, 32 * current.arq_block_bytes)
                and not short_fec_opportunity):
            # A late bounded probe costs another turn and cannot pay for itself
            # before this message ends. Preserve the already proven profile.
            return
        position = next((i for i, item in enumerate(mcs_ladder)
                         if item.index == self.mcs_index), 0)
        if unique_bytes < max(512, current.arq_block_bytes):
            return
        # A short confirmed MCS trial may earn one more bounded trial while
        # substantial data remains. Other short trials still need a full window
        # before an ungated promotion, to avoid a ladder of costly probes.
        blind_probe_allowed = (baseline is None or
                               unique_bytes >= max(4096, 16 * current.arq_block_bytes)
                               or short_fec_opportunity)
        candidates = {"mcs": [], "fec": []}
        fecs = (self._fec_ladder() if self.config.adaptive_fec
                else [current.fec])
        fec_index = fecs.index(current.fec)
        current_density = float(sc_mcs(current.mcs_index).bits_per_symbol) * fec_spec(current.fec).rate
        higher_mcs = mcs_ladder[position + 1:]
        # Quality may justify skipping rungs, but is not a permanent veto on
        # the next rung. All choices still have to pass a bounded CRC trial.
        def mcs_admitted(item) -> bool:
            margin = 0.5 + fec_snr_margin_db(current.fec)
            if (sc_mcs(current.mcs_index).bits_per_symbol == 2
                    and item.bits_per_symbol >= 7):
                # QPSK quality can overpredict a dense constellation. Demand
                # both SNR and EVM headroom for a direct first-window jump.
                margin = max(margin, 2.0)
                return (remote_snr_db >= item.min_snr_db + margin
                        and remote_evm_rms <= 10 ** (-(item.min_snr_db + margin) / 20.0))
            return remote_snr_db >= item.min_snr_db + margin

        admitted = [item for item in higher_mcs if mcs_admitted(item)]
        targets = list(reversed(admitted))
        if blind_probe_allowed and higher_mcs and higher_mcs[0] not in targets:
            targets.append(higher_mcs[0])
        for item in targets:
            # A higher constellation may need stronger FEC. Excluding these
            # joint choices traps a clean low MCS at 4/5 or 9/10 forever.
            for fec in reversed(fecs[:fec_index + 1]):
                density = float(item.bits_per_symbol) * fec_spec(fec).rate
                if density > current_density:
                    candidates["mcs"].append(TxProfile(item.index, fec,
                        current.burst_bytes, current.arq_block_bytes))
        if self.config.adaptive_fec and fec_index + 1 < len(fecs):
            higher_fec = fecs[fec_index + 1:]
            admitted_fec = [fec for fec in higher_fec if self._fec_upgrade_allowed(fec)]
            ordered_fec = list(reversed(admitted_fec))
            if short_fec_opportunity:
                # On a measured strong path, one bounded 4/5 trial can pay
                # back before a medium message ends. Marginal 15–17 dB links
                # repeatedly lost even 2/3 or 3/4 trial blocks on RF, so
                # their clean 1/2 MCS trial is not enough evidence to spend
                # another DATA/ACK turn immediately.
                preferred = FecProfile.LDPC_4_5
                ordered_fec = [preferred] + [fec for fec in ordered_fec
                                              if fec != preferred]
            if blind_probe_allowed and higher_fec[0] not in ordered_fec:
                ordered_fec.append(higher_fec[0])
            candidates["fec"] = [TxProfile(current.mcs_index, fec,
                current.burst_bytes, current.arq_block_bytes) for fec in ordered_fec]
        order = (self.capacity_probe_next, "mcs" if self.capacity_probe_next == "fec" else "fec")
        for dimension in order:
            for target in candidates[dimension]:
                if (remaining_bytes is not None and elapsed_seconds > 0.0
                        and unique_bytes > 0):
                    target_density = (float(sc_mcs(target.mcs_index).bits_per_symbol)
                                      * fec_spec(target.fec).rate)
                    saved_seconds = (
                        elapsed_seconds * remaining_bytes / unique_bytes
                        * (1.0 - current_density / target_density)
                    )
                    # A bounded trial is another radio turn. Use the measured
                    # window cost so slower PTT/audio paths require more data
                    # to pay for exploration; longer messages still can probe.
                    if saved_seconds < max(1.5, elapsed_seconds):
                        continue
                # Failure at this constellation also rejects weaker protection
                # at it. Compare quality only at the same source constellation.
                rejected = [quality for (mcs, fec, source_mcs, _), quality
                            in self.capacity_rejections.items()
                            if mcs == target.mcs_index and source_mcs == current.mcs_index
                            and fec_spec(fec).rate <= fec_spec(target.fec).rate]
                if any(remote_snr_db < snr + 1.5 and remote_evm_rms > evm * .85
                       for snr, evm in rejected):
                    continue
                self.capacity_probe_profile = current
                self.capacity_probe_quality = quality
                self.mcs_probe_from = current.mcs_index if target.mcs_index != current.mcs_index else None
                self.fec_probe_from = current.fec if target.fec != current.fec else None
                self.mcs_index, self.current_fec = target.mcs_index, target.fec
                self.capacity_probe_next = "fec" if dimension == "mcs" else "mcs"
                return

    def report_burst(self, *, sent_blocks: int, acked_blocks: int,
                     retransmitted_bytes: int = 0,
                     unique_bytes: int = 0, elapsed_seconds: float = 0.0,
                     remote_snr_db: float | None = None,
                     remote_evm_rms: float | None = None,
                     mcs_index: int | None = None,
                     adapt_profile: bool = True) -> None:
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

        if not adapt_profile:
            # The joint capacity controller owns this decision; update only
            # measurements here, without a second independent FEC decision.
            self.clean_streak = 0
            return

        if ratio < 1.0:
            self.clean_streak = 0
            self.profile_upgrade_cooldown = self.config.recovery_cooldown_bursts
            self._downgrade()
            return
        if self.config.rapid_acquisition and retransmitted_bytes:
            # A successful robust retry says nothing about the normal profile.
            return
        if self.profile_upgrade_cooldown > 0:
            self.profile_upgrade_cooldown -= 1
            self.clean_streak = self.clean_streak + 1 if self.config.rapid_acquisition else 0
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
