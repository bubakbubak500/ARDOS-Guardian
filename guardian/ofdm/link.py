"""Stop-and-wait ARQ over an abstract half-duplex pipe.

This is a radio link layer, not a stream protocol: there is no window, no
congestion control and no connection. One block goes out, the sender waits for
an answer, and either moves on or sends it again. On a half-duplex channel where
turning the transmitter around costs a fraction of a second, that simplicity is
a feature -- there is nothing in flight to reason about when a burst is lost.

Everything the radio touches sits behind `HalfDuplexPipe`. The state machine
below is therefore the *same* code whether it is driving a simulated channel or
a real transceiver: `SimulatedDuplexPipe` is what the tests use, and
`guardian.payload.ofdm_vhf.RadioAudioPipe` is the one that keys a radio. That
boundary is deliberate and it is where the remaining hardware work is isolated.

PTT turnaround is an explicit parameter rather than slack inside a timeout. Two
stations have to agree, roughly, on how long an answer may take, and that number
is dominated by how long the radios need to swap roles -- not by anything the
modem does.
"""

from __future__ import annotations

import queue
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

import numpy as np

from .channel import Channel, ChannelSpec
from .config import DEFAULT_MCS_INDEX, OfdmProfile
from .framing import (OfdmFrameType, PhyHeader, build_burst, burst_duration,
                      decode_burst, split_blocks)
from .metrics import AdaptationState, LinkMetrics, OfdmStatus


class HalfDuplexPipe(Protocol):
    """One channel that cannot send and receive at the same time."""

    def send(self, samples: np.ndarray) -> None:
        """Put a waveform on the channel and return once it has all gone."""

    def receive(self, timeout: float) -> np.ndarray | None:
        """Wait up to `timeout` seconds for a burst. None means nothing came."""


@dataclass
class SimulatedDuplexPipe:
    """A pipe whose far end is another one of these, through a channel model.

    Both directions get their own `Channel`, because two radios do not share a
    noise generator, and both are seeded, so a failing run can be reproduced
    exactly.
    """

    outbound: queue.Queue
    inbound: queue.Queue
    channel: Channel
    #: Optional hook, called as `damage(nth_transmission, samples)`, for tests
    #: that need a specific burst to arrive broken.
    damage: Callable[[int, np.ndarray], np.ndarray] | None = None
    transmissions: int = 0

    def send(self, samples: np.ndarray) -> None:
        self.transmissions += 1
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
    """Two pipes wired to each other, each through its own seeded channel."""
    spec = spec if spec is not None else ChannelSpec()
    forward: queue.Queue = queue.Queue()
    reverse: queue.Queue = queue.Queue()
    return (
        SimulatedDuplexPipe(outbound=forward, inbound=reverse,
                            channel=Channel(profile, spec, seed=seed)),
        SimulatedDuplexPipe(outbound=reverse, inbound=forward,
                            channel=Channel(profile, spec, seed=seed ^ 0xFFFF)),
    )


@dataclass
class OfdmLink:
    """Moves a message across one hop, block by block, with retransmission."""

    profile: OfdmProfile
    pipe: HalfDuplexPipe
    mcs_index: int = DEFAULT_MCS_INDEX
    max_retries: int = 4
    #: Seconds a radio pair needs to swap transmit and receive roles.
    ptt_turnaround: float = 0.5
    #: Slack on top of airtime and turnaround, for scheduling and decode time.
    timeout_margin: float = 1.0
    on_log: Callable[[str], None] | None = None
    on_status: Callable[[OfdmStatus], None] | None = None

    status: OfdmStatus = field(default_factory=OfdmStatus)
    adaptation: AdaptationState = field(default_factory=AdaptationState)
    #: Measurements from the most recently decoded burst, for diagnostics.
    last_metrics: LinkMetrics | None = None
    #: Seconds the channel has been occupied by this transfer -- airtime in both
    #: directions plus a turnaround per change of direction.
    channel_seconds: float = 0.0

    def __post_init__(self) -> None:
        self.status.profile = self.profile.name
        self.status.mcs = self.mcs_index

    # -- plumbing -----------------------------------------------------------

    def _log(self, message: str) -> None:
        if self.on_log:
            self.on_log(message)

    def _rate(self, moved: int) -> None:
        """Update the measured throughput from bytes moved per second of channel.

        Channel time, not wall clock. Wall clock is the right measure on air and
        meaningless in simulation, where a burst is handed over instantly; airtime
        is the same quantity in both, and it is what the radios really spend. The
        figure therefore includes acknowledgements, retransmissions and PTT
        turnaround, so it is well below the raw PHY rate -- which is the point.
        """
        if moved and self.channel_seconds > 0.0:
            self.status.est_bitrate_bps = moved * 8 / self.channel_seconds

    def _transmit(self, waveform: np.ndarray) -> None:
        self._publish("transmitting")
        self.pipe.send(waveform)
        self.channel_seconds += len(waveform) / self.profile.sample_rate
        self.channel_seconds += self.ptt_turnaround

    def _publish(self, state: str | None = None) -> None:
        if state is not None:
            self.status.state = state
        if self.on_status:
            self.on_status(self.status)

    def _record(self, decoded) -> None:
        metrics = decoded.metrics
        self.last_metrics = metrics
        self.adaptation.record_burst(metrics)
        self.status.snr_db = metrics.snr_db
        self.status.evm_rms = metrics.evm_rms
        if decoded.header is not None:
            # Charge the peer's burst to the channel from its own header, which
            # says exactly how long it must have been.
            self.channel_seconds += burst_duration(self.profile, decoded.header)
            self.channel_seconds += self.ptt_turnaround

    # -- timeouts -----------------------------------------------------------

    def reply_timeout(self) -> float:
        """How long to wait for an ACK or NACK.

        Its airtime, both radios turning around, and the margin. Nothing here is
        a round number pulled out of the air: change the profile and this follows.
        """
        airtime = burst_duration(self.profile, PhyHeader(OfdmFrameType.ACK, 0))
        return airtime + 2.0 * self.ptt_turnaround + self.timeout_margin

    def data_timeout(self) -> float:
        """How long to wait for a full-size data burst to arrive."""
        header = PhyHeader(OfdmFrameType.DATA, 0, mcs=self.mcs_index,
                           payload_len=self.profile.block_size)
        airtime = burst_duration(self.profile, header)
        return airtime + 2.0 * self.ptt_turnaround + self.timeout_margin

    # -- sending ------------------------------------------------------------

    def send_message(self, msg_id: int, payload: bytes) -> bool:
        """Move a whole message across the hop. True only if every block was acked."""
        blocks = split_blocks(payload, self.profile.block_size)
        self.status.total_bytes = len(payload)
        self.status.tx_bytes = 0
        self.status.retries = 0
        self.channel_seconds = 0.0
        self._log(
            f"OFDM: sending #{msg_id} as {len(blocks)} block(s) of up to "
            f"{self.profile.block_size} B at MCS{self.mcs_index}"
        )
        for seq, block in enumerate(blocks):
            if not self._send_block(msg_id, seq, len(blocks), block):
                self.status.last_block_ok = False
                self._publish("failed")
                self._log(f"OFDM: block {seq + 1}/{len(blocks)} of #{msg_id} failed")
                return False
            self.status.tx_bytes += len(block)
            self.status.last_block_ok = True
            self._rate(self.status.tx_bytes)
            self._publish()
        self._publish("idle")
        self._log(f"OFDM: #{msg_id} complete -- {self.adaptation.summary()}")
        return True

    def _send_block(self, msg_id: int, seq: int, count: int, block: bytes) -> bool:
        header = PhyHeader(
            OfdmFrameType.DATA, msg_id, block_seq=seq, block_count=count,
            mcs=self.mcs_index, payload_len=len(block),
        )
        waveform = build_burst(self.profile, header, block)
        self.adaptation.blocks_sent += 1
        for attempt in range(self.max_retries + 1):
            if attempt:
                self.adaptation.retransmissions += 1
                self.status.retries += 1
            self._transmit(waveform)
            self._publish("waiting_ack")
            answer = self._await_reply(msg_id, seq)
            if answer is OfdmFrameType.ACK:
                self.adaptation.blocks_acked += 1
                return True
            if answer is OfdmFrameType.NACK:
                self.adaptation.blocks_nacked += 1
                self._log(f"OFDM: block {seq} nacked, resending")
            else:
                self._log(f"OFDM: no answer to block {seq}, resending")
        return False

    def _await_reply(self, msg_id: int, seq: int) -> OfdmFrameType | None:
        """Wait for this block's ACK or NACK, ignoring anything else."""
        deadline = time.monotonic() + self.reply_timeout()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return None
            samples = self.pipe.receive(remaining)
            if samples is None:
                return None
            decoded = decode_burst(self.profile, samples)
            self._record(decoded)
            header = decoded.header
            if header is None or not decoded.ok:
                continue
            if header.frame_type is OfdmFrameType.DATA:
                continue
            # A re-sent acknowledgement of an earlier block proves nothing about
            # this one, so keep listening rather than treating it as progress.
            if header.msg_id != msg_id or header.block_seq != seq:
                continue
            return header.frame_type

    # -- receiving ----------------------------------------------------------

    def receive_message(self, msg_id: int | None = None,
                        timeout: float | None = None) -> bytes | None:
        """Collect a whole message, acknowledging each block. None if it failed.

        `timeout` bounds each individual wait, not the transfer: a long message
        is allowed to take as long as its blocks take, but a peer that has gone
        quiet ends it.
        """
        wait = self.data_timeout() if timeout is None else timeout
        blocks: dict[int, bytes] = {}
        expected: int | None = None
        self.status.rx_bytes = 0
        # Cleared rather than accumulated: a link reused for a second message
        # would otherwise carry the first one's size into the progress figure.
        self.status.total_bytes = 0
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
                # Without a header there is no block number to blame, so there
                # is nothing to NACK either. Let the sender's timeout handle it.
                self._log(f"OFDM: burst rejected -- {decoded.metrics.error}")
                continue
            if header.frame_type is not OfdmFrameType.DATA:
                continue
            if msg_id is not None and header.msg_id != msg_id:
                self._log(f"OFDM: ignoring block for #{header.msg_id}")
                continue

            if not decoded.ok:
                self.status.last_block_ok = False
                self._log(f"OFDM: block {header.block_seq} bad -- {decoded.metrics.error}")
                self._answer(OfdmFrameType.NACK, header)
                self._publish()
                continue

            expected = header.block_count
            # An upper bound until the last block arrives -- the sender does not
            # announce the total, only how many blocks there are, and the final
            # one may be short. Good enough to drive a progress bar; replaced by
            # the exact figure once the message is complete.
            self.status.total_bytes = expected * self.profile.block_size
            if header.block_seq in blocks:
                # A duplicate means our previous acknowledgement was lost, not
                # that the sender has anything new to say. Acknowledge again and
                # drop the payload.
                self.adaptation.duplicates += 1
                self._log(f"OFDM: block {header.block_seq} is a duplicate, re-acking")
            else:
                blocks[header.block_seq] = decoded.payload or b""
                self.status.rx_bytes += len(decoded.payload or b"")
                self.adaptation.blocks_received += 1
            self.status.last_block_ok = True
            self._answer(OfdmFrameType.ACK, header)

            self._rate(self.status.rx_bytes)
            self._publish()

            if expected is not None and len(blocks) == expected:
                self.status.total_bytes = self.status.rx_bytes
                self._publish("idle")
                message = b"".join(blocks[index] for index in range(expected))
                self._log(
                    f"OFDM: #{header.msg_id} received -- {len(message)} B in "
                    f"{expected} block(s), {self.adaptation.summary()}"
                )
                return message

    def _answer(self, kind: OfdmFrameType, header: PhyHeader) -> None:
        """Send an ACK or NACK for a received block, at the most robust MCS."""
        reply = PhyHeader(kind, header.msg_id, block_seq=header.block_seq,
                          block_count=header.block_count)
        self._transmit(build_burst(self.profile, reply))
