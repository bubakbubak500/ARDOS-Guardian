"""Selective-repeat ARQ over one half-duplex OFDM radio channel.

Version 2 amortises keying and turnaround over several independently protected
512-byte sub-blocks.  One robust manifest identifies the members, one compact
bitmap reports everything the receiver already holds, and a retry contains only
the missing members.  The same state machine drives the deterministic simulated
pipe and the real soundcard/PTT pipe.
"""

from __future__ import annotations

import math
import queue
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

import numpy as np

from .adaptation import AdaptationConfig, LinkAdaptationController
from .channel import Channel, ChannelSpec
from .coding import FecProfile, fec_spec
from .config import (DEFAULT_MCS_INDEX, MCS_TABLE, SC_MCS_TABLE, OfdmProfile)
from .framing import (AckBitmap, DEFER_ACK_FLAG, FINAL_ACK_CONFIRM_FLAG,
                      FRAME_VERSION, LEGACY_FRAME_VERSION,
                      SUPERFRAME_VERSION, MAX_SUBBLOCKS,
                      MAX_SUPERFRAME_SUBBLOCKS, OfdmFrameError,
                      OfdmFrameType, PhyHeader, SubBlock, build_burst,
                      burst_duration, decode_burst, decode_many,
                      protocol_overhead_bytes,
                      split_blocks)
from .metrics import AdaptationState, LinkMetrics, OfdmStatus
from ..timing import TxTiming


class HalfDuplexPipe(Protocol):
    def send(self, samples: np.ndarray) -> TxTiming | None:
        """Put a waveform on the channel and return once it has all gone."""

    def receive(self, timeout: float) -> np.ndarray | None:
        """Wait up to `timeout` seconds for a burst. None means nothing came."""


class BurstCodec(Protocol):
    """Physical burst implementation consumed by the proven ARQ state machine.

    OFDM remains the default.  Other Guardian G2 waveform families implement
    this deliberately small boundary so framing/ARQ semantics do not get copied
    or subtly changed with every modem experiment.
    """

    def build_burst(self, profile, header: PhyHeader, payload: bytes = b"", *,
                    blocks: list[SubBlock] | None = None) -> np.ndarray: ...

    def decode_burst(self, profile, samples, **kwargs): ...

    def decode_many(self, profile, samples, **kwargs): ...

    def burst_duration(self, profile, header: PhyHeader,
                       block_lengths: list[int] | None = None) -> float: ...


class OfdmBurstCodec:
    """Adapter preserving the original OFDM framing implementation verbatim."""

    @staticmethod
    def build_burst(profile, header: PhyHeader, payload: bytes = b"", *,
                    blocks: list[SubBlock] | None = None) -> np.ndarray:
        return build_burst(profile, header, payload, blocks=blocks)

    @staticmethod
    def decode_burst(profile, samples, **kwargs):
        return decode_burst(profile, samples, **kwargs)

    @staticmethod
    def decode_many(profile, samples, **kwargs):
        return decode_many(profile, samples, **kwargs)

    @staticmethod
    def burst_duration(profile, header: PhyHeader,
                       block_lengths: list[int] | None = None) -> float:
        return burst_duration(profile, header, block_lengths)


@dataclass
class SimulatedDuplexPipe:
    outbound: queue.Queue
    inbound: queue.Queue
    channel: Channel
    damage: Callable[[int, np.ndarray], np.ndarray] | None = None
    transmissions: int = 0
    samples_sent: int = 0

    def send(self, samples: np.ndarray) -> TxTiming:
        self.transmissions += 1
        self.samples_sent += len(samples)
        aired = self.channel(samples)
        if self.damage is not None:
            aired = self.damage(self.transmissions, aired)
        self.outbound.put(aired)
        return TxTiming.deterministic(len(samples) / self.channel.profile.sample_rate)

    def receive(self, timeout: float) -> np.ndarray | None:
        try:
            return self.inbound.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None


def simulated_pair(profile: OfdmProfile, spec: ChannelSpec | None = None,
                   seed: int = 0xA5) -> tuple[SimulatedDuplexPipe, SimulatedDuplexPipe]:
    spec = spec if spec is not None else ChannelSpec()
    forward: queue.Queue = queue.Queue()
    reverse: queue.Queue = queue.Queue()
    return (
        SimulatedDuplexPipe(forward, reverse, Channel(profile, spec, seed=seed)),
        SimulatedDuplexPipe(reverse, forward,
                            Channel(profile, spec, seed=seed ^ 0xFFFF)),
    )


@dataclass
class BurstTxState:
    msg_id: int
    burst_id: int
    total_blocks: int
    blocks: dict[int, bytes]
    pending: set[int]
    retry_count: dict[int, int]
    first_pass_acked: set[int] = field(default_factory=set)
    started: float = field(default_factory=time.monotonic)


@dataclass
class RxBurstState:
    msg_id: int
    total_blocks: int
    blocks: dict[int, bytes] = field(default_factory=dict)
    started: float = field(default_factory=time.monotonic)
    updated: float = field(default_factory=time.monotonic)
    train_seen: bool = False


@dataclass
class OfdmLink:
    """Move one message per link, using aggregated bursts and selective repeat."""

    profile: OfdmProfile
    pipe: HalfDuplexPipe
    mcs_index: int = DEFAULT_MCS_INDEX
    max_retries: int = 4
    ptt_turnaround: float = 0.5
    timeout_margin: float = 1.0
    timeout_multiplier: float = 1.0
    train_bursts: int = 1
    superframe: bool = False
    adaptive_train: bool = False
    adaptive_mcs: bool = False
    train_gap_seconds: float = 0.03
    max_train_seconds: float = 20.0
    decode_margin_per_burst: float = 1.5
    on_log: Callable[[str], None] | None = None
    on_status: Callable[[OfdmStatus], None] | None = None
    cancelled: Callable[[], bool] | None = None
    controller: LinkAdaptationController | None = None
    legacy_mode: bool = False
    codec: BurstCodec | None = None

    status: OfdmStatus = field(default_factory=OfdmStatus)
    adaptation: AdaptationState = field(default_factory=AdaptationState)
    last_metrics: LinkMetrics | None = None
    last_reply_reason: str = ""
    channel_seconds: float = 0.0
    _next_burst_id: int = 0
    _started_at: float | None = None
    _current_train_bursts: int = 1
    _train_clean_streak: int = 0
    _mcs_clean_streak: int = 0
    _maximum_mcs_index: int = 0
    _soft_cache: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.train_bursts = max(1, min(8, int(self.train_bursts)))
        self._current_train_bursts = 1 if self.adaptive_train else self.train_bursts
        self._maximum_mcs_index = int(self.mcs_index)
        if self.adaptive_mcs and self._maximum_mcs_index > DEFAULT_MCS_INDEX:
            # A ceiling is not a safe acquisition mode. Start every automatic
            # session at robust QPSK and earn denser constellations from three
            # consecutive clean cumulative ACKs plus measured SNR margin.
            self.mcs_index = DEFAULT_MCS_INDEX
        self.train_gap_seconds = max(0.01, min(0.20, float(self.train_gap_seconds)))
        self.max_train_seconds = max(1.0, min(60.0, float(self.max_train_seconds)))
        self.decode_margin_per_burst = max(
            0.0, min(10.0, float(self.decode_margin_per_burst))
        )
        if self.codec is None:
            self.codec = OfdmBurstCodec()
        if self.controller is None:
            self.controller = LinkAdaptationController(
                AdaptationConfig(adaptive_fec=True, adaptive_burst=True),
                mcs_index=self.mcs_index,
            )
        else:
            self.controller.mcs_index = self.mcs_index
        self.status.profile = self.profile.name
        self.status.mcs = self.mcs_index
        self._publish_profile()

    def _build_burst(self, header: PhyHeader, payload: bytes = b"", *,
                     blocks: list[SubBlock] | None = None) -> np.ndarray:
        return self.codec.build_burst(  # type: ignore[union-attr]
            self.profile, header, payload, blocks=blocks
        )

    def _decode_burst(self, samples):
        started = time.monotonic()
        decoded = self.codec.decode_burst(  # type: ignore[union-attr]
            self.profile, samples, soft_cache=self._soft_cache
        )
        elapsed = time.monotonic() - started
        self.status.rx_decode_seconds += elapsed
        rx = getattr(self.pipe, "last_rx_timing", None)
        if rx is not None:
            self.status.rx_trigger_wait_seconds += max(0.0, rx.trigger_wait)
            self.status.rx_capture_seconds += max(0.0, rx.capture)
            self.status.rx_hangover_seconds += max(0.0, rx.hangover)
            try:
                self.pipe.last_rx_timing = None
            except (AttributeError, TypeError):
                pass
        return decoded

    def _decode_many(self, samples):
        method = getattr(self.codec, "decode_many", None)
        if method is None:
            return [self._decode_burst(samples)]
        started = time.monotonic()
        decoded = method(self.profile, samples, soft_cache=self._soft_cache)
        self.status.rx_decode_seconds += time.monotonic() - started
        rx = getattr(self.pipe, "last_rx_timing", None)
        if rx is not None:
            self.status.rx_trigger_wait_seconds += max(0.0, rx.trigger_wait)
            self.status.rx_capture_seconds += max(0.0, rx.capture)
            self.status.rx_hangover_seconds += max(0.0, rx.hangover)
            try:
                self.pipe.last_rx_timing = None
            except (AttributeError, TypeError):
                pass
        return decoded

    def _report_train_feedback(self, sent: int, received: int) -> None:
        """Adapt aggregation separately from FEC and payload burst sizing.

        A single loss shortens the next train immediately; three completely
        clean windows buy one additional microburst.  This slow-up/fast-down
        asymmetry keeps a long superframe from magnifying a transient fade.
        """
        if not self.adaptive_train:
            return
        if int(received) < int(sent):
            self._train_clean_streak = 0
            self._current_train_bursts = max(1, self._current_train_bursts - 1)
            return
        self._train_clean_streak += 1
        if self._train_clean_streak >= 3:
            self._train_clean_streak = 0
            self._current_train_bursts = min(
                self.train_bursts, self._current_train_bursts + 1
            )

    def _report_mcs_feedback(self, sent: int, received: int,
                             remote_snr_db: float | None) -> None:
        if not self.adaptive_mcs:
            return
        table = (MCS_TABLE if isinstance(self.codec, OfdmBurstCodec)
                 else SC_MCS_TABLE)
        ceiling = next((item for item in table
                        if item.index == self._maximum_mcs_index), table[0])
        # New experimental IDs preserve old wire assignments, so numeric MCS
        # order is intentionally not a robustness ladder (for example 8-PSK is
        # MCS7, after 256-QAM MCS4). Define "below the selected ceiling" by both
        # information density and measured SNR threshold, then sort physically.
        allowed = sorted(
            (item for item in table
             if float(item.bits_per_symbol) <= float(ceiling.bits_per_symbol)
             and item.min_snr_db <= ceiling.min_snr_db),
            key=lambda item: (
                item.min_snr_db, float(item.bits_per_symbol), item.index,
            ),
        )
        position = next((index for index, item in enumerate(allowed)
                         if item.index == self.mcs_index), 0)
        if int(received) < int(sent):
            self._mcs_clean_streak = 0
            if position > 0:
                self.mcs_index = allowed[position - 1].index
                self.controller.mcs_index = self.mcs_index
            return
        if remote_snr_db is None:
            return
        self._mcs_clean_streak += 1
        if self._mcs_clean_streak < 3:
            return
        self._mcs_clean_streak = 0
        usable = [item for item in allowed
                  if float(remote_snr_db) >= item.min_snr_db + 1.5]
        if usable:
            target = max(
                usable,
                key=lambda item: (
                    float(item.bits_per_symbol), -item.min_snr_db, -item.index,
                ),
            )
            if target.index != self.mcs_index:
                self.mcs_index = target.index
                self.controller.mcs_index = target.index

    def _burst_duration(self, header: PhyHeader,
                        block_lengths: list[int] | None = None) -> float:
        return self.codec.burst_duration(  # type: ignore[union-attr]
            self.profile, header, block_lengths
        )

    # -- plumbing ---------------------------------------------------------

    def _log(self, message: str) -> None:
        if self.on_log:
            self.on_log(message)

    def _publish_profile(self) -> None:
        selected = self.controller.profile
        self.status.fec = fec_spec(selected.fec).label
        self.status.mcs = self.mcs_index
        self.status.burst_bytes = selected.burst_bytes
        self.status.arq_block_bytes = selected.arq_block_bytes

    def _publish(self, state: str | None = None) -> None:
        if state is not None:
            self.status.state = state
        self._publish_profile()
        if self.on_status:
            self.on_status(self.status)

    def _reset_timing(self) -> None:
        self.status.tx_lead_seconds = 0.0
        self.status.tx_guard_seconds = 0.0
        self.status.tx_tail_seconds = 0.0
        self.status.keyed_seconds = 0.0
        self.status.rx_trigger_wait_seconds = 0.0
        self.status.rx_capture_seconds = 0.0
        self.status.rx_hangover_seconds = 0.0
        self.status.rx_decode_seconds = 0.0

    def _rate(self, moved: int) -> None:
        if moved and self.channel_seconds > 0.0:
            self.status.est_bitrate_bps = moved * 8.0 / self.channel_seconds
        self.status.elapsed_seconds = (
            0.0 if self._started_at is None
            else max(0.0, time.monotonic() - self._started_at)
        )
        if moved and self.status.elapsed_seconds > 0.0:
            self.status.goodput_bps = moved * 8.0 / self.status.elapsed_seconds

    def _transmit(self, waveform: np.ndarray, *, header: PhyHeader,
                  control: bool = False) -> None:
        self._publish("transmitting")
        timing = self.pipe.send(waveform)
        airtime = len(waveform) / self.profile.sample_rate
        if isinstance(timing, TxTiming):
            occupied = timing.wall_clock + self.ptt_turnaround
            self.status.tx_lead_seconds += timing.lead
            self.status.tx_guard_seconds += timing.guard
            self.status.tx_tail_seconds += timing.tail
            self.status.keyed_seconds += timing.keyed_total
        else:
            # Third-party/test pipes written for format 2 return None. Preserve
            # compatibility while making the omitted timing visible as the
            # configured deterministic turnaround.
            occupied = airtime + self.ptt_turnaround
        self.channel_seconds += occupied
        if control:
            self.status.ack_bursts += 1
            self.status.ack_airtime_seconds += airtime
        else:
            self.status.data_bursts += 1
            self.status.data_airtime_seconds += airtime
        self.status.ptt_cycles += 1
        self.status.turnaround_seconds += max(0.0, occupied - airtime)
        self.status.protocol_overhead_bytes += protocol_overhead_bytes(header)

    def _record(self, decoded, *, turnaround: bool = True) -> None:
        metrics = decoded.metrics
        self.last_metrics = metrics
        self.adaptation.record_burst(metrics)
        self.status.snr_db = metrics.residual_snr_db or metrics.snr_db
        self.status.evm_rms = metrics.evm_rms
        if decoded.header is not None:
            lengths = ([decoded.block_lengths[sequence]
                        for sequence in decoded.block_order]
                       if decoded.block_lengths else None)
            airtime = self._burst_duration(decoded.header, lengths)
            self.channel_seconds += airtime
            if turnaround:
                self.channel_seconds += self.ptt_turnaround
            control = decoded.header.frame_type is not OfdmFrameType.DATA
            if control:
                self.status.ack_bursts += 1
                self.status.ack_airtime_seconds += airtime
            else:
                self.status.data_bursts += 1
                self.status.data_airtime_seconds += airtime
            if turnaround:
                self.status.turnaround_seconds += self.ptt_turnaround
            self.status.protocol_overhead_bytes += protocol_overhead_bytes(
                decoded.header
            )

    # -- duration-aware timeouts ----------------------------------------

    def reply_timeout(self, total_blocks: int = 1) -> float:
        bitmap = AckBitmap(max(1, int(total_blocks)), frozenset()).encode()
        header = PhyHeader(
            OfdmFrameType.ACK, 0, block_count=max(1, int(total_blocks)),
            payload_len=len(bitmap),
        )
        airtime = self._burst_duration(header)
        return ((airtime + 2.0 * self.ptt_turnaround + self.timeout_margin)
                * max(0.5, float(self.timeout_multiplier)))

    def data_timeout(self) -> float:
        if self.legacy_mode:
            header = PhyHeader(
                OfdmFrameType.DATA, 0, mcs=self.mcs_index,
                payload_len=self.profile.block_size,
                version=LEGACY_FRAME_VERSION,
            )
            airtime = self._burst_duration(header)
        else:
            selected = self.controller.profile
            base_count = min(MAX_SUBBLOCKS, max(1, math.ceil(
                selected.burst_bytes / selected.arq_block_bytes
            )))
            count = base_count
            version = FRAME_VERSION
            if self.superframe:
                prototype_lengths = [selected.arq_block_bytes] * base_count
                prototype = PhyHeader(
                    OfdmFrameType.DATA, 0, block_count=base_count,
                    mcs=self.mcs_index, fec=selected.fec,
                    payload_len=sum(prototype_lengths), subblock_count=base_count,
                )
                micro_seconds = self._burst_duration(prototype, prototype_lengths)
                by_time = max(1, int(
                    (self.max_train_seconds + self.train_gap_seconds)
                    / max(0.001, micro_seconds + self.train_gap_seconds)
                ))
                count = min(
                    MAX_SUPERFRAME_SUBBLOCKS,
                    base_count * min(self._current_train_bursts, by_time),
                )
                version = SUPERFRAME_VERSION
            lengths = [selected.arq_block_bytes] * count
            header = PhyHeader(
                OfdmFrameType.DATA, 0, block_count=count, mcs=self.mcs_index,
                fec=selected.fec, payload_len=sum(lengths),
                subblock_count=count, version=version,
            )
            airtime = self._burst_duration(header, lengths)
        return ((airtime + 2.0 * self.ptt_turnaround + self.timeout_margin)
                * max(0.5, float(self.timeout_multiplier)))

    def _is_cancelled(self) -> bool:
        return bool(self.cancelled is not None and self.cancelled())

    def _reply_capture_seconds(self, total_blocks: int) -> float:
        bitmap = AckBitmap(max(1, int(total_blocks)), frozenset()).encode()
        header = PhyHeader(
            OfdmFrameType.ACK, 0, block_count=max(1, int(total_blocks)),
            payload_len=len(bitmap),
        )
        # Once squelch opens, a false trigger must not inherit the maximum DATA
        # capture length. ACK airtime plus the existing decode margin is enough
        # for the complete control waveform and its hangover.
        return max(1.0, self._burst_duration(header) + self.timeout_margin)

    def _receive_reply(self, timeout: float,
                       total_blocks: int) -> np.ndarray | None:
        limited = getattr(self.pipe, "receive_limited", None)
        if limited is not None:
            return limited(
                timeout, self._reply_capture_seconds(total_blocks)
            )
        # Third-party/test pipes implementing the original protocol remain
        # valid; only the real soundcard pipe needs the capture hard cap.
        return self.pipe.receive(timeout)

    def _guard_peer_receiver(self) -> None:
        seconds = max(
            0.0, float(getattr(self.pipe, "peer_turnaround_guard", 0.0))
        )
        if seconds and not self._is_cancelled():
            time.sleep(seconds)

    # -- sending ---------------------------------------------------------

    def send_message(self, msg_id: int, payload: bytes) -> bool:
        self._started_at = time.monotonic()
        self.status.direction = "send"
        if self._is_cancelled():
            self._publish("failed")
            return False
        if self.legacy_mode:
            return self._send_message_legacy(msg_id, payload)
        selected = self.controller.profile
        blocks_list = split_blocks(payload, selected.arq_block_bytes)
        if len(blocks_list) > 0xFFFF:
            raise ValueError("OFDM message exceeds 65535 ARQ blocks")
        blocks = dict(enumerate(blocks_list))
        self.status.total_bytes = len(payload)
        self.status.tx_bytes = 0
        self.status.retries = 0
        self.status.retransmitted_bytes = 0
        self.status.protocol_overhead_bytes = 0
        self.status.data_bursts = 0
        self.status.ack_bursts = 0
        self.status.ptt_cycles = 0
        self.status.data_airtime_seconds = 0.0
        self.status.ack_airtime_seconds = 0.0
        self.status.turnaround_seconds = 0.0
        self._reset_timing()
        self.status.elapsed_seconds = 0.0
        self.status.goodput_bps = None
        self.channel_seconds = 0.0
        self._log(
            f"OFDM: sending #{msg_id} as {len(blocks)} block(s), "
            f"{self.controller.summary()}, MCS{self.mcs_index}"
        )

        next_sequence = 0
        while next_sequence < len(blocks):
            if self._is_cancelled():
                self._publish("failed")
                self._log(f"OFDM: sending #{msg_id} cancelled")
                return False
            selected = self.controller.profile
            base_capacity = min(MAX_SUBBLOCKS, max(
                1, selected.burst_bytes // selected.arq_block_bytes
            ))
            prototype_lengths = [selected.arq_block_bytes] * base_capacity
            prototype = PhyHeader(
                OfdmFrameType.DATA, msg_id, block_count=len(blocks),
                mcs=self.mcs_index, fec=selected.fec,
                payload_len=sum(prototype_lengths), subblock_count=base_capacity,
            )
            micro_seconds = self._burst_duration(prototype, prototype_lengths)
            by_time = max(1, int(
                (self.max_train_seconds + self.train_gap_seconds)
                / max(0.001, micro_seconds + self.train_gap_seconds)
            ))
            train_count = min(self._current_train_bursts, by_time)
            capacity = (min(MAX_SUPERFRAME_SUBBLOCKS,
                            base_capacity * train_count)
                        if self.superframe else base_capacity)
            window_capacity = (capacity if self.superframe
                               else capacity * train_count)
            sequences = list(range(
                next_sequence,
                min(len(blocks), next_sequence + window_capacity),
            ))
            burst_id = self._next_burst_id
            reserved_ids = (1 if self.superframe else
                            max(1, math.ceil(len(sequences) / capacity)))
            self._next_burst_id = (self._next_burst_id + reserved_ids) & 0xFFFF
            state = BurstTxState(
                msg_id=msg_id, burst_id=burst_id, total_blocks=len(blocks),
                blocks={sequence: blocks[sequence] for sequence in sequences},
                pending=set(sequences),
                retry_count={sequence: 0 for sequence in sequences},
            )
            send = self._send_window if reserved_ids == 1 else self._send_train_window
            if not send(state):
                self.status.last_block_ok = False
                self._publish("failed")
                self._log(
                    f"OFDM: burst {burst_id} of #{msg_id} failed; "
                    f"missing={sorted(state.pending)}"
                )
                return False
            next_sequence = sequences[-1] + 1

        # Tell the receiver its final bitmap arrived. Without this one-bit
        # handshake it has to keep the OFDM soundcard open for a complete reply
        # timeout in case that ACK was lost; for a large bitmap that can be
        # minutes and delays the session-layer RECEIVED frame on the control
        # channel. A lost confirmation is harmless: the existing linger/re-ACK
        # path remains active until its normal timeout.
        if self._is_cancelled():
            self._publish("failed")
            return False
        self._confirm_final_bitmap(state)
        self.status.last_block_ok = True
        self._publish("idle")
        self._log(
            f"OFDM: #{msg_id} complete -- {self.adaptation.summary()}, "
            f"goodput={self.status.goodput_bps or 0:.0f} bit/s over "
            f"{self.status.elapsed_seconds:.2f} s, "
            f"modeled={self.status.est_bitrate_bps or 0:.0f} bit/s, "
            f"retransmitted={self.status.retransmitted_bytes} B, "
            f"protocol_overhead={self.status.protocol_overhead_bytes} B"
        )
        return True

    def _confirm_final_bitmap(self, state: BurstTxState) -> None:
        if self._is_cancelled():
            return
        confirmation = PhyHeader(
            OfdmFrameType.POLL,
            state.msg_id,
            block_seq=state.burst_id,
            block_count=state.total_blocks,
            flags=FINAL_ACK_CONFIRM_FLAG,
        )
        self._log(f"OFDM ACK CONFIRM #{state.burst_id}")
        try:
            self._transmit(
                self._build_burst(confirmation),
                header=confirmation,
                control=True,
            )
        except Exception as exc:  # delivered data must not become a false failure
            self._log(
                f"OFDM ACK CONFIRM #{state.burst_id} could not be sent: {exc}; "
                "receiver will use its bounded re-ACK hold"
            )

    def _send_window(self, state: BurstTxState) -> bool:
        self.adaptation.blocks_sent += len(state.blocks)
        acknowledged: set[int] = set()
        for attempt in range(self.max_retries + 1):
            if self._is_cancelled():
                return False
            members = [SubBlock(sequence, state.blocks[sequence])
                       for sequence in sorted(state.pending)]
            fec = self.controller.fec_for_retry(attempt)
            if attempt:
                self.status.retries += 1
                self.adaptation.retransmissions += len(members)
                resent = sum(len(block.payload) for block in members)
                self.status.retransmitted_bytes += resent
                for block in members:
                    state.retry_count[block.sequence] += 1
            header = PhyHeader(
                OfdmFrameType.DATA, state.msg_id,
                block_seq=state.burst_id, block_count=state.total_blocks,
                mcs=self.mcs_index, fec=fec,
                payload_len=sum(len(block.payload) for block in members),
                subblock_count=len(members), retransmission=attempt > 0,
                version=(SUPERFRAME_VERSION if self.superframe
                         else FRAME_VERSION),
            )
            waveform = self._build_burst(header, blocks=members)
            self.status.last_burst_blocks = len(members)
            self._log(
                f"OFDM TX BURST #{state.burst_id}: payload={header.payload_len} B, "
                f"blocks={len(members)}, FEC={fec_spec(fec).label}, "
                f"airtime={len(waveform) / self.profile.sample_rate:.2f} s"
            )
            before = self.channel_seconds
            self._transmit(waveform, header=header)
            self._publish("waiting_ack")
            answer = self._await_bitmap(
                state.msg_id, state.burst_id, state.total_blocks
            )
            received = (set() if answer is None else
                        state.pending & set(answer.received))
            self._report_train_feedback(len(members), len(received))
            self._report_mcs_feedback(
                len(members), len(received),
                answer.remote_snr_db if answer else None,
            )
            if attempt == 0:
                state.first_pass_acked.update(received)
            newly = received - acknowledged
            acknowledged.update(received)
            for sequence in newly:
                self.status.tx_bytes += len(state.blocks[sequence])
            self.adaptation.blocks_acked += len(newly)
            state.pending.difference_update(received)
            if answer is not None and state.pending:
                self.adaptation.blocks_nacked += len(state.pending)
            moved = sum(len(state.blocks[sequence]) for sequence in received)
            self.controller.report_burst(
                sent_blocks=len(members), acked_blocks=len(received),
                retransmitted_bytes=(sum(len(block.payload) for block in members)
                                     if attempt else 0),
                unique_bytes=moved,
                elapsed_seconds=max(0.0, self.channel_seconds - before),
                remote_snr_db=(answer.remote_snr_db if answer else None),
                remote_evm_rms=(answer.remote_evm_rms if answer else None),
            )
            if answer is not None and answer.remote_snr_db is not None:
                self.status.remote_snr_db = answer.remote_snr_db
            if answer is not None and answer.remote_evm_rms is not None:
                self.status.remote_evm_rms = answer.remote_evm_rms
            self._rate(self.status.tx_bytes)
            self._publish()
            if not state.pending:
                self.status.last_first_pass_ok = len(state.first_pass_acked)
                self._log(
                    f"OFDM COMPLETE #{state.burst_id}: "
                    f"{len(state.blocks)}/{len(state.blocks)} blocks, "
                    f"retries={attempt}"
                )
                self._guard_peer_receiver()
                return True
            last = attempt == self.max_retries
            reason = (self.last_reply_reason if answer is None else
                      f"missing={sorted(state.pending)}")
            self._log(
                f"OFDM RETX #{state.burst_id}: {reason}"
                f"{'' if last else ', selective resend'}"
            )
        return False

    def _send_train_window(self, state: BurstTxState) -> bool:
        """Send independently decodable microbursts under one PTT and one ACK."""
        self.adaptation.blocks_sent += len(state.blocks)
        acknowledged: set[int] = set()
        selected = self.controller.profile
        capacity = min(32, max(1, selected.burst_bytes // selected.arq_block_bytes))
        for attempt in range(self.max_retries + 1):
            if self._is_cancelled():
                return False
            sequences = sorted(state.pending)
            chunks = [sequences[index:index + capacity]
                      for index in range(0, len(sequences), capacity)]
            fec = self.controller.fec_for_retry(attempt)
            if attempt:
                self.status.retries += 1
                self.adaptation.retransmissions += len(sequences)
                resent = sum(len(state.blocks[value]) for value in sequences)
                self.status.retransmitted_bytes += resent
                for sequence in sequences:
                    state.retry_count[sequence] += 1
            waveforms: list[np.ndarray] = []
            headers: list[PhyHeader] = []
            for index, chunk in enumerate(chunks):
                members = [SubBlock(sequence, state.blocks[sequence])
                           for sequence in chunk]
                header = PhyHeader(
                    OfdmFrameType.DATA, state.msg_id,
                    block_seq=(state.burst_id + index) & 0xFFFF,
                    block_count=state.total_blocks, mcs=self.mcs_index, fec=fec,
                    payload_len=sum(len(item.payload) for item in members),
                    subblock_count=len(members), retransmission=attempt > 0,
                    flags=(DEFER_ACK_FLAG if index < len(chunks) - 1 else 0),
                )
                headers.append(header)
                waveforms.append(self._build_burst(header, blocks=members))
            gap = np.zeros(int(self.profile.sample_rate * self.train_gap_seconds))
            train = np.concatenate([
                part for index, waveform in enumerate(waveforms)
                for part in ((gap if index else np.zeros(0)), waveform)
            ])
            final_id = headers[-1].block_seq
            self.status.last_burst_blocks = len(sequences)
            self._log(
                f"OFDM TX TRAIN #{state.burst_id}: {len(headers)} microburst(s), "
                f"payload={sum(headers[index].payload_len for index in range(len(headers)))} B, "
                f"FEC={fec_spec(fec).label}, airtime={len(train) / self.profile.sample_rate:.2f} s"
            )
            before = self.channel_seconds
            self._transmit_train(train, headers)
            self._publish("waiting_ack")
            answer = self._await_bitmap(
                state.msg_id, final_id, state.total_blocks,
                extra_timeout=len(headers) * self.decode_margin_per_burst,
            )
            if answer is None:
                if self._is_cancelled():
                    return False
                poll = PhyHeader(
                    OfdmFrameType.POLL, state.msg_id, block_seq=final_id,
                    block_count=state.total_blocks,
                )
                self._log(f"OFDM POLL #{state.burst_id}: final microburst/ACK missing")
                self._transmit(self._build_burst(poll), header=poll, control=True)
                answer = self._await_bitmap(
                    state.msg_id, final_id, state.total_blocks
                )
            received = (set() if answer is None else
                        state.pending & set(answer.received))
            self._report_train_feedback(len(sequences), len(received))
            self._report_mcs_feedback(
                len(sequences), len(received),
                answer.remote_snr_db if answer else None,
            )
            if attempt == 0:
                state.first_pass_acked.update(received)
            newly = received - acknowledged
            acknowledged.update(received)
            for sequence in newly:
                self.status.tx_bytes += len(state.blocks[sequence])
            self.adaptation.blocks_acked += len(newly)
            state.pending.difference_update(received)
            if answer is not None and state.pending:
                self.adaptation.blocks_nacked += len(state.pending)
            moved = sum(len(state.blocks[sequence]) for sequence in received)
            self.controller.report_burst(
                sent_blocks=len(sequences), acked_blocks=len(received),
                retransmitted_bytes=(sum(len(state.blocks[value]) for value in sequences)
                                     if attempt else 0),
                unique_bytes=moved,
                elapsed_seconds=max(0.0, self.channel_seconds - before),
                remote_snr_db=(answer.remote_snr_db if answer else None),
                remote_evm_rms=(answer.remote_evm_rms if answer else None),
            )
            self._rate(self.status.tx_bytes)
            self._publish()
            if not state.pending:
                self.status.last_first_pass_ok = len(state.first_pass_acked)
                self._log(
                    f"OFDM TRAIN COMPLETE #{state.burst_id}: "
                    f"{len(state.blocks)}/{len(state.blocks)} blocks, retries={attempt}"
                )
                self._guard_peer_receiver()
                return True
            self._log(
                f"OFDM TRAIN RETX #{state.burst_id}: "
                f"missing={sorted(state.pending)}"
            )
        return False

    def _transmit_train(self, waveform: np.ndarray,
                        headers: list[PhyHeader]) -> None:
        self._publish("transmitting")
        timing = self.pipe.send(waveform)
        airtime = len(waveform) / self.profile.sample_rate
        if isinstance(timing, TxTiming):
            occupied = timing.wall_clock + self.ptt_turnaround
            self.status.tx_lead_seconds += timing.lead
            self.status.tx_guard_seconds += timing.guard
            self.status.tx_tail_seconds += timing.tail
            self.status.keyed_seconds += timing.keyed_total
        else:
            occupied = airtime + self.ptt_turnaround
        self.channel_seconds += occupied
        self.status.data_bursts += len(headers)
        self.status.data_airtime_seconds += airtime
        self.status.ptt_cycles += 1
        self.status.turnaround_seconds += max(0.0, occupied - airtime)
        self.status.protocol_overhead_bytes += sum(
            protocol_overhead_bytes(header) for header in headers
        )

    def _await_bitmap(self, msg_id: int, burst_id: int,
                      total_blocks: int,
                      extra_timeout: float = 0.0) -> AckBitmap | None:
        deadline = (time.monotonic() + self.reply_timeout(total_blocks)
                    + max(0.0, float(extra_timeout)))
        heard = 0
        why = ""
        while True:
            if self._is_cancelled():
                self.last_reply_reason = "transfer cancelled"
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                break
            samples = self._receive_reply(remaining, total_blocks)
            if samples is None:
                break
            heard += 1
            decoded = self._decode_burst(samples)
            self._record(decoded)
            header = decoded.header
            if header is None or not decoded.ok:
                why = decoded.metrics.error or "burst did not decode"
                continue
            if header.frame_type is OfdmFrameType.DATA:
                why = "the far end was still sending data"
                continue
            if header.msg_id != msg_id or header.block_seq != burst_id:
                why = f"heard an answer for burst {header.block_seq}, not {burst_id}"
                continue
            try:
                bitmap = AckBitmap.decode(decoded.payload or b"")
            except OfdmFrameError as exc:
                why = str(exc)
                continue
            if bitmap.total_blocks != total_blocks:
                why = "ACK bitmap has the wrong message block count"
                continue
            self.last_reply_reason = ""
            quality = ""
            if bitmap.remote_snr_db is not None:
                quality += f", remote_snr={bitmap.remote_snr_db:.1f} dB"
            if bitmap.remote_evm_rms is not None:
                quality += f", remote_evm={bitmap.remote_evm_rms:.3f}"
            self._log(
                f"OFDM RX {header.frame_type.label} #{burst_id}: "
                f"held={len(bitmap.received)}/{bitmap.total_blocks}{quality}"
            )
            return bitmap
        self.last_reply_reason = (
            f"{heard} burst(s) heard, none usable: {why}" if heard
            else f"nothing heard{self._squelch_note()}"
        )
        return None

    # -- receiving -------------------------------------------------------

    def receive_message(self, msg_id: int | None = None,
                        timeout: float | None = None) -> bytes | None:
        self._started_at = time.monotonic()
        self.status.direction = "receive"
        wait = self.data_timeout() if timeout is None else timeout
        state: RxBurstState | None = None
        self.status.rx_bytes = 0
        self.status.total_bytes = 0
        self.status.data_bursts = 0
        self.status.ack_bursts = 0
        self.status.ptt_cycles = 0
        self.status.data_airtime_seconds = 0.0
        self.status.ack_airtime_seconds = 0.0
        self.status.turnaround_seconds = 0.0
        self._reset_timing()
        self.status.elapsed_seconds = 0.0
        self.status.goodput_bps = None
        self.status.protocol_overhead_bytes = 0
        self.channel_seconds = 0.0
        while True:
            if self._is_cancelled():
                self._publish("failed")
                self._log("OFDM: receive cancelled")
                return None
            self._publish("synchronizing")
            samples = self.pipe.receive(wait)
            if samples is None:
                self._publish("failed")
                self._log("OFDM: peer went quiet")
                return None
            self._publish("receiving")
            batch = self._decode_many(samples)
            if not batch:
                self._log("OFDM: capture contained no readable burst header")
                continue
            recorded_airtime = 0.0
            answer: tuple[OfdmFrameType, PhyHeader, LinkMetrics] | None = None
            for decoded in batch:
                self._record(decoded, turnaround=False)
                header = decoded.header
                if header is None:
                    continue
                lengths = ([decoded.block_lengths[value]
                            for value in decoded.block_order]
                           if decoded.block_lengths else None)
                recorded_airtime += self._burst_duration(header, lengths)
                if header.frame_type is OfdmFrameType.POLL:
                    if state is not None and header.msg_id == state.msg_id:
                        self._answer_bitmap(
                            OfdmFrameType.ACK, header, state, decoded.metrics
                        )
                    continue
                if header.frame_type is not OfdmFrameType.DATA:
                    continue
                if msg_id is not None and header.msg_id != msg_id:
                    self._log(f"OFDM: ignoring burst for #{header.msg_id}")
                    continue
                if header.version == LEGACY_FRAME_VERSION:
                    return self._receive_legacy_burst(decoded, msg_id, wait)
                if state is None:
                    state = RxBurstState(header.msg_id, header.block_count)
                    self.status.total_bytes = state.total_blocks * self.profile.block_size
                if header.msg_id != state.msg_id or header.block_count != state.total_blocks:
                    self._log("OFDM: inconsistent message identity or block count")
                    continue
                state.updated = time.monotonic()
                state.train_seen = state.train_seen or header.defer_ack or len(batch) > 1

                if not decoded.block_order:
                    self.status.last_block_ok = False
                    self._log(
                        f"OFDM: burst {header.block_seq} manifest rejected -- "
                        f"{decoded.metrics.error}"
                    )
                    if not header.defer_ack:
                        answer = (OfdmFrameType.NACK, header, decoded.metrics)
                    continue

                for sequence, payload in decoded.blocks.items():
                    if sequence in state.blocks:
                        self.adaptation.duplicates += 1
                    else:
                        state.blocks[sequence] = payload
                        self.status.rx_bytes += len(payload)
                        self.adaptation.blocks_received += 1
                held = set(state.blocks)
                missing_here = set(decoded.block_order) - held
                self.adaptation.blocks_nacked += len(missing_here)
                self.status.last_block_ok = not missing_here
                self.status.last_burst_blocks = len(decoded.block_order)
                self.status.last_first_pass_ok = (
                    len(decoded.block_order) - len(missing_here)
                )
                if not header.defer_ack:
                    kind = (OfdmFrameType.ACK if not missing_here
                            else OfdmFrameType.NACK)
                    answer = (kind, header, decoded.metrics)

            # One capture is one remote PTT cycle even when it contained several
            # independently decoded bursts. Count short train gaps from the real
            # buffer, then one radio turnaround -- never one per microburst.
            capture_airtime = len(samples) / self.profile.sample_rate
            self.channel_seconds += max(0.0, capture_airtime - recorded_airtime)
            self.channel_seconds += self.ptt_turnaround
            self.status.turnaround_seconds += self.ptt_turnaround
            self.status.ptt_cycles += 1
            if answer is not None and state is not None:
                kind, incoming, metrics = answer
                self._answer_bitmap(kind, incoming, state, metrics)
            self._rate(self.status.rx_bytes)
            self._publish()

            if state is not None and len(state.blocks) == state.total_blocks:
                message = b"".join(state.blocks[index]
                                   for index in range(state.total_blocks))
                self.status.total_bytes = len(message)
                self._publish("idle")
                self._log(
                    f"OFDM: #{state.msg_id} received -- {len(message)} B in "
                    f"{state.total_blocks} block(s), {self.adaptation.summary()}"
                )
                self._linger_bitmap(state)
                return message

    def _answer_bitmap(self, kind: OfdmFrameType, incoming: PhyHeader,
                       state: RxBurstState, metrics: LinkMetrics) -> None:
        report = AckBitmap(
            state.total_blocks, frozenset(state.blocks),
            remote_snr_db=metrics.residual_snr_db,
            remote_evm_rms=metrics.evm_rms,
        )
        payload = report.encode_compact() if state.train_seen else report.encode()
        reply = PhyHeader(
            kind, incoming.msg_id, block_seq=incoming.block_seq,
            block_count=state.total_blocks, payload_len=len(payload),
        )
        self._transmit(
            self._build_burst(reply, payload),
            header=reply, control=True,
        )

    def _linger_bitmap(self, state: RxBurstState) -> None:
        rounds = max(1, self.max_retries)
        self._log(
            f"OFDM: #{state.msg_id} complete, holding "
            f"{self.reply_timeout(state.total_blocks):.1f} s for a lost ACK"
        )
        for _ in range(rounds):
            if self._is_cancelled():
                return
            samples = self.pipe.receive(self.reply_timeout(state.total_blocks))
            if samples is None:
                return
            decoded = self._decode_burst(samples)
            header = decoded.header
            if header is None:
                self._record(decoded)
                continue
            if header.msg_id != state.msg_id or header.frame_type not in {
                OfdmFrameType.DATA, OfdmFrameType.POLL,
            }:
                self._record(decoded)
                return
            if (header.frame_type is OfdmFrameType.POLL
                    and header.flags & FINAL_ACK_CONFIRM_FLAG):
                self._log(
                    f"OFDM: final bitmap for #{state.msg_id} confirmed; "
                    "returning to the control channel"
                )
                return
            self._record(decoded)
            if header.frame_type is OfdmFrameType.DATA:
                self.adaptation.duplicates += len(
                    set(decoded.block_order) & set(state.blocks)
                )
            self._log(
                f"OFDM: sender repeated burst {header.block_seq}; "
                "sending the complete bitmap again"
            )
            self._answer_bitmap(OfdmFrameType.ACK, header, state, decoded.metrics)

    # -- version-1 fixed legacy mode ------------------------------------

    def _send_message_legacy(self, msg_id: int, payload: bytes) -> bool:
        self._started_at = time.monotonic()
        blocks = split_blocks(payload, self.profile.block_size)
        self.status.total_bytes = len(payload)
        self.status.tx_bytes = 0
        self.status.retries = 0
        self.status.retransmitted_bytes = 0
        self.status.protocol_overhead_bytes = 0
        self.status.data_bursts = 0
        self.status.ack_bursts = 0
        self.status.ptt_cycles = 0
        self.status.data_airtime_seconds = 0.0
        self.status.ack_airtime_seconds = 0.0
        self.status.turnaround_seconds = 0.0
        self._reset_timing()
        self.status.elapsed_seconds = 0.0
        self.status.goodput_bps = None
        self.channel_seconds = 0.0
        self._log(f"OFDM legacy: sending #{msg_id} as {len(blocks)} block(s)")
        for sequence, block in enumerate(blocks):
            if self._is_cancelled():
                self._publish("failed")
                return False
            if not self._send_legacy_block(msg_id, sequence, len(blocks), block):
                self.status.last_block_ok = False
                self._publish("failed")
                return False
            self.status.tx_bytes += len(block)
            self._rate(self.status.tx_bytes)
        self.status.last_block_ok = True
        self._publish("idle")
        return True

    def _send_legacy_block(self, msg_id: int, sequence: int, count: int,
                           block: bytes) -> bool:
        header = PhyHeader(
            OfdmFrameType.DATA, msg_id, block_seq=sequence, block_count=count,
            mcs=self.mcs_index, payload_len=len(block),
            version=LEGACY_FRAME_VERSION,
        )
        waveform = self._build_burst(header, block)
        self.adaptation.blocks_sent += 1
        for attempt in range(self.max_retries + 1):
            if self._is_cancelled():
                return False
            if attempt:
                self.status.retries += 1
                self.adaptation.retransmissions += 1
            self._transmit(waveform, header=header)
            answer = self._await_legacy_reply(msg_id, sequence)
            if answer is OfdmFrameType.ACK:
                self.adaptation.blocks_acked += 1
                return True
            if answer is OfdmFrameType.NACK:
                self.adaptation.blocks_nacked += 1
        return False

    def _await_legacy_reply(self, msg_id: int,
                            sequence: int) -> OfdmFrameType | None:
        deadline = time.monotonic() + self.reply_timeout()
        while time.monotonic() < deadline:
            if self._is_cancelled():
                return None
            samples = self._receive_reply(
                deadline - time.monotonic(), 1
            )
            if samples is None:
                return None
            decoded = self._decode_burst(samples)
            self._record(decoded)
            header = decoded.header
            if (header is not None and decoded.ok
                    and header.version == LEGACY_FRAME_VERSION
                    and header.frame_type is not OfdmFrameType.DATA
                    and header.msg_id == msg_id and header.block_seq == sequence):
                return header.frame_type
        return None

    def _receive_legacy_burst(self, first, msg_id: int | None,
                              wait: float) -> bytes | None:
        blocks: dict[int, bytes] = {}
        decoded = first
        expected: int | None = None
        while True:
            if self._is_cancelled():
                self._publish("failed")
                return None
            header = decoded.header
            if (header is not None and header.frame_type is OfdmFrameType.DATA
                    and (msg_id is None or header.msg_id == msg_id)):
                expected = header.block_count
                if decoded.ok and header.block_seq not in blocks:
                    blocks[header.block_seq] = decoded.payload or b""
                    self.adaptation.blocks_received += 1
                self._answer_legacy(
                    OfdmFrameType.ACK if decoded.ok else OfdmFrameType.NACK,
                    header,
                )
                if expected is not None and len(blocks) == expected:
                    message = b"".join(blocks[index] for index in range(expected))
                    self.status.rx_bytes = len(message)
                    self.status.total_bytes = len(message)
                    self._publish("idle")
                    return message
            samples = self.pipe.receive(wait)
            if samples is None:
                self._publish("failed")
                return None
            decoded = self._decode_burst(samples)
            self._record(decoded)

    def _answer_legacy(self, kind: OfdmFrameType, header: PhyHeader) -> None:
        reply = PhyHeader(
            kind, header.msg_id, block_seq=header.block_seq,
            block_count=header.block_count, version=LEGACY_FRAME_VERSION,
        )
        self._transmit(self._build_burst(reply),
                       header=reply, control=True)

    def _squelch_note(self) -> str:
        describe = getattr(self.pipe, "describe_squelch", None)
        if describe is None:
            return ""
        try:
            return f" ({describe()})"
        except Exception:  # noqa: BLE001 - diagnostics must not fail a transfer
            return ""
