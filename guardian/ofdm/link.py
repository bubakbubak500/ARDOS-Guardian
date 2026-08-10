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
from .config import DEFAULT_MCS_INDEX, OfdmProfile
from .framing import (AckBitmap, LEGACY_FRAME_VERSION, OfdmFrameError,
                      OfdmFrameType, PhyHeader, SubBlock, build_burst,
                      burst_duration, decode_burst, protocol_overhead_bytes,
                      split_blocks)
from .metrics import AdaptationState, LinkMetrics, OfdmStatus


class HalfDuplexPipe(Protocol):
    def send(self, samples: np.ndarray) -> None:
        """Put a waveform on the channel and return once it has all gone."""

    def receive(self, timeout: float) -> np.ndarray | None:
        """Wait up to `timeout` seconds for a burst. None means nothing came."""


@dataclass
class SimulatedDuplexPipe:
    outbound: queue.Queue
    inbound: queue.Queue
    channel: Channel
    damage: Callable[[int, np.ndarray], np.ndarray] | None = None
    transmissions: int = 0
    samples_sent: int = 0

    def send(self, samples: np.ndarray) -> None:
        self.transmissions += 1
        self.samples_sent += len(samples)
        aired = self.channel(samples)
        if self.damage is not None:
            aired = self.damage(self.transmissions, aired)
        self.outbound.put(aired)

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
    on_log: Callable[[str], None] | None = None
    on_status: Callable[[OfdmStatus], None] | None = None
    controller: LinkAdaptationController | None = None
    legacy_mode: bool = False

    status: OfdmStatus = field(default_factory=OfdmStatus)
    adaptation: AdaptationState = field(default_factory=AdaptationState)
    last_metrics: LinkMetrics | None = None
    last_reply_reason: str = ""
    channel_seconds: float = 0.0
    _next_burst_id: int = 0
    _started_at: float | None = None

    def __post_init__(self) -> None:
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

    # -- plumbing ---------------------------------------------------------

    def _log(self, message: str) -> None:
        if self.on_log:
            self.on_log(message)

    def _publish_profile(self) -> None:
        selected = self.controller.profile
        self.status.fec = fec_spec(selected.fec).label
        self.status.burst_bytes = selected.burst_bytes
        self.status.arq_block_bytes = selected.arq_block_bytes

    def _publish(self, state: str | None = None) -> None:
        if state is not None:
            self.status.state = state
        self._publish_profile()
        if self.on_status:
            self.on_status(self.status)

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
        self.pipe.send(waveform)
        airtime = len(waveform) / self.profile.sample_rate
        self.channel_seconds += airtime
        self.channel_seconds += self.ptt_turnaround
        if control:
            self.status.ack_bursts += 1
            self.status.ack_airtime_seconds += airtime
        else:
            self.status.data_bursts += 1
            self.status.data_airtime_seconds += airtime
        self.status.turnaround_seconds += self.ptt_turnaround
        self.status.protocol_overhead_bytes += protocol_overhead_bytes(header)

    def _record(self, decoded) -> None:
        metrics = decoded.metrics
        self.last_metrics = metrics
        self.adaptation.record_burst(metrics)
        self.status.snr_db = metrics.residual_snr_db or metrics.snr_db
        self.status.evm_rms = metrics.evm_rms
        if decoded.header is not None:
            lengths = ([decoded.block_lengths[sequence]
                        for sequence in decoded.block_order]
                       if decoded.block_lengths else None)
            airtime = burst_duration(self.profile, decoded.header, lengths)
            self.channel_seconds += airtime
            self.channel_seconds += self.ptt_turnaround
            control = decoded.header.frame_type is not OfdmFrameType.DATA
            if control:
                self.status.ack_bursts += 1
                self.status.ack_airtime_seconds += airtime
            else:
                self.status.data_bursts += 1
                self.status.data_airtime_seconds += airtime
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
        airtime = burst_duration(self.profile, header)
        return ((airtime + 2.0 * self.ptt_turnaround + self.timeout_margin)
                * max(0.5, float(self.timeout_multiplier)))

    def data_timeout(self) -> float:
        if self.legacy_mode:
            header = PhyHeader(
                OfdmFrameType.DATA, 0, mcs=self.mcs_index,
                payload_len=self.profile.block_size,
                version=LEGACY_FRAME_VERSION,
            )
            airtime = burst_duration(self.profile, header)
        else:
            selected = self.controller.profile
            count = min(32, max(1, math.ceil(
                selected.burst_bytes / selected.arq_block_bytes
            )))
            lengths = [selected.arq_block_bytes] * count
            header = PhyHeader(
                OfdmFrameType.DATA, 0, block_count=count, mcs=self.mcs_index,
                fec=selected.fec, payload_len=sum(lengths),
                subblock_count=count,
            )
            airtime = burst_duration(self.profile, header, lengths)
        return ((airtime + 2.0 * self.ptt_turnaround + self.timeout_margin)
                * max(0.5, float(self.timeout_multiplier)))

    # -- sending ---------------------------------------------------------

    def send_message(self, msg_id: int, payload: bytes) -> bool:
        self._started_at = time.monotonic()
        self.status.direction = "send"
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
        self.status.data_airtime_seconds = 0.0
        self.status.ack_airtime_seconds = 0.0
        self.status.turnaround_seconds = 0.0
        self.status.elapsed_seconds = 0.0
        self.status.goodput_bps = None
        self.channel_seconds = 0.0
        self._log(
            f"OFDM: sending #{msg_id} as {len(blocks)} block(s), "
            f"{self.controller.summary()}, MCS{self.mcs_index}"
        )

        next_sequence = 0
        while next_sequence < len(blocks):
            selected = self.controller.profile
            capacity = min(32, max(1, selected.burst_bytes //
                                   selected.arq_block_bytes))
            sequences = list(range(
                next_sequence, min(len(blocks), next_sequence + capacity)
            ))
            burst_id = self._next_burst_id
            self._next_burst_id = (self._next_burst_id + 1) & 0xFFFF
            state = BurstTxState(
                msg_id=msg_id, burst_id=burst_id, total_blocks=len(blocks),
                blocks={sequence: blocks[sequence] for sequence in sequences},
                pending=set(sequences),
                retry_count={sequence: 0 for sequence in sequences},
            )
            if not self._send_window(state):
                self.status.last_block_ok = False
                self._publish("failed")
                self._log(
                    f"OFDM: burst {burst_id} of #{msg_id} failed; "
                    f"missing={sorted(state.pending)}"
                )
                return False
            next_sequence = sequences[-1] + 1

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

    def _send_window(self, state: BurstTxState) -> bool:
        self.adaptation.blocks_sent += len(state.blocks)
        acknowledged: set[int] = set()
        for attempt in range(self.max_retries + 1):
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
            )
            waveform = build_burst(self.profile, header, blocks=members)
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
                return True
            last = attempt == self.max_retries
            reason = (self.last_reply_reason if answer is None else
                      f"missing={sorted(state.pending)}")
            self._log(
                f"OFDM RETX #{state.burst_id}: {reason}"
                f"{'' if last else ', selective resend'}"
            )
        return False

    def _await_bitmap(self, msg_id: int, burst_id: int,
                      total_blocks: int) -> AckBitmap | None:
        deadline = time.monotonic() + self.reply_timeout(total_blocks)
        heard = 0
        why = ""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                break
            samples = self.pipe.receive(remaining)
            if samples is None:
                break
            heard += 1
            decoded = decode_burst(self.profile, samples)
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
        self.status.data_airtime_seconds = 0.0
        self.status.ack_airtime_seconds = 0.0
        self.status.turnaround_seconds = 0.0
        self.status.elapsed_seconds = 0.0
        self.status.goodput_bps = None
        self.status.protocol_overhead_bytes = 0
        self.channel_seconds = 0.0
        while True:
            self._publish("synchronizing")
            samples = self.pipe.receive(wait)
            if samples is None:
                self._publish("failed")
                self._log("OFDM: peer went quiet")
                return None
            self._publish("receiving")
            decoded = decode_burst(self.profile, samples)
            self._record(decoded)
            header = decoded.header
            if header is None:
                self._log(f"OFDM: burst rejected -- {decoded.metrics.error}")
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

            if not decoded.block_order:
                self.status.last_block_ok = False
                self._log(f"OFDM: burst {header.block_seq} manifest rejected -- "
                          f"{decoded.metrics.error}")
                self._answer_bitmap(OfdmFrameType.NACK, header, state, decoded.metrics)
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
            self.status.last_first_pass_ok = len(decoded.block_order) - len(missing_here)
            kind = OfdmFrameType.ACK if not missing_here else OfdmFrameType.NACK
            self._answer_bitmap(kind, header, state, decoded.metrics)
            self._rate(self.status.rx_bytes)
            self._publish()

            if len(state.blocks) == state.total_blocks:
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
        payload = report.encode()
        reply = PhyHeader(
            kind, incoming.msg_id, block_seq=incoming.block_seq,
            block_count=state.total_blocks, payload_len=len(payload),
        )
        self._transmit(
            build_burst(self.profile, reply, payload),
            header=reply, control=True,
        )

    def _linger_bitmap(self, state: RxBurstState) -> None:
        rounds = max(1, self.max_retries)
        self._log(
            f"OFDM: #{state.msg_id} complete, holding "
            f"{self.reply_timeout(state.total_blocks):.1f} s for a lost ACK"
        )
        for _ in range(rounds):
            samples = self.pipe.receive(self.reply_timeout(state.total_blocks))
            if samples is None:
                return
            decoded = decode_burst(self.profile, samples)
            self._record(decoded)
            header = decoded.header
            if header is None:
                continue
            if header.frame_type is not OfdmFrameType.DATA or header.msg_id != state.msg_id:
                return
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
        self.status.data_airtime_seconds = 0.0
        self.status.ack_airtime_seconds = 0.0
        self.status.turnaround_seconds = 0.0
        self.status.elapsed_seconds = 0.0
        self.status.goodput_bps = None
        self.channel_seconds = 0.0
        self._log(f"OFDM legacy: sending #{msg_id} as {len(blocks)} block(s)")
        for sequence, block in enumerate(blocks):
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
        waveform = build_burst(self.profile, header, block)
        self.adaptation.blocks_sent += 1
        for attempt in range(self.max_retries + 1):
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
            samples = self.pipe.receive(deadline - time.monotonic())
            if samples is None:
                return None
            decoded = decode_burst(self.profile, samples)
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
            decoded = decode_burst(self.profile, samples)
            self._record(decoded)

    def _answer_legacy(self, kind: OfdmFrameType, header: PhyHeader) -> None:
        reply = PhyHeader(
            kind, header.msg_id, block_seq=header.block_seq,
            block_count=header.block_count, version=LEGACY_FRAME_VERSION,
        )
        self._transmit(build_burst(self.profile, reply),
                       header=reply, control=True)

    def _squelch_note(self) -> str:
        describe = getattr(self.pipe, "describe_squelch", None)
        if describe is None:
            return ""
        try:
            return f" ({describe()})"
        except Exception:  # noqa: BLE001 - diagnostics must not fail a transfer
            return ""
