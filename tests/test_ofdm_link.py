"""Stop-and-wait ARQ: segmentation, acknowledgement, retry, duplicates.

The link runs over `SimulatedDuplexPipe` here. The same state machine drives a
real radio through `RadioAudioPipe`; only the pipe changes, which is why these
tests are worth as much as they are.
"""

from __future__ import annotations

import queue
import threading

import numpy as np
import pytest

from guardian.ofdm import BENCH, OfdmLink, simulated_pair
from guardian.ofdm.channel import Channel, ChannelSpec
from guardian.ofdm.framing import OfdmFrameType, PhyHeader, burst_duration
from guardian.ofdm.link import SimulatedDuplexPipe
from guardian.ofdm.metrics import AdaptationState, LinkMetrics, OfdmStatus

SEED = 0xA5
#: Short compared with a real radio, but the pipe answers instantly, so the
#: timeouts only need to be long enough to be non-zero.
TURNAROUND = 0.05
MARGIN = 0.5


def _payload(size: int, seed: int = SEED) -> bytes:
    return np.random.default_rng(seed).integers(0, 256, size, dtype=np.uint8).tobytes()


def _exchange(payload: bytes, spec: ChannelSpec | None = None, *, msg_id: int = 42,
              mcs_index: int = 1, seed: int = SEED, max_retries: int = 4,
              damage=None) -> tuple[OfdmLink, OfdmLink, bytes | None, bool]:
    """Run one whole transfer and return both links, the bytes, and the verdict."""
    spec = spec if spec is not None else ChannelSpec(snr_db=15.0, delay=500,
                                                    trailing=1500)
    near, far = simulated_pair(BENCH, spec, seed=seed)
    if damage is not None:
        near.damage = damage
    sender = OfdmLink(BENCH, near, mcs_index=mcs_index, max_retries=max_retries,
                      ptt_turnaround=TURNAROUND, timeout_margin=MARGIN)
    receiver = OfdmLink(BENCH, far, mcs_index=mcs_index, max_retries=max_retries,
                        ptt_turnaround=TURNAROUND, timeout_margin=MARGIN)
    got: dict[str, bytes | None] = {}
    listener = threading.Thread(
        target=lambda: got.__setitem__("data", receiver.receive_message(msg_id=msg_id)),
        daemon=True,
    )
    listener.start()
    ok = sender.send_message(msg_id, payload)
    listener.join(timeout=180)
    assert not listener.is_alive(), "the receiver never finished"
    return sender, receiver, got.get("data"), ok


# -- the pipe --------------------------------------------------------------- #

def test_a_pipe_pair_carries_samples_in_both_directions() -> None:
    near, far = simulated_pair(BENCH, ChannelSpec(), seed=1)
    near.send(np.ones(100))
    assert far.receive(0.1) is not None
    assert near.receive(0.01) is None       # nothing came the other way
    far.send(np.ones(100))
    assert near.receive(0.1) is not None


def test_each_direction_gets_its_own_noise() -> None:
    # Two radios do not share a noise generator, and a link that only worked
    # because they did would be a fiction.
    near, far = simulated_pair(BENCH, ChannelSpec(snr_db=10.0), seed=1)
    assert near.channel.seed != far.channel.seed


def test_the_damage_hook_only_touches_the_transmission_it_names() -> None:
    marked = []
    pipe = SimulatedDuplexPipe(
        outbound=queue.Queue(), inbound=queue.Queue(),
        channel=Channel(BENCH, ChannelSpec()),
        damage=lambda n, s: (marked.append(n) or s),
    )
    for _ in range(3):
        pipe.send(np.ones(10))
    assert marked == [1, 2, 3]
    assert pipe.transmissions == 3


# -- timeouts follow the profile -------------------------------------------- #

def test_timeouts_are_derived_from_airtime_and_turnaround() -> None:
    # Not round numbers: change the profile and these follow it.
    link = OfdmLink(BENCH, None, mcs_index=1, ptt_turnaround=0.4, timeout_margin=1.0)
    ack = burst_duration(BENCH, PhyHeader(OfdmFrameType.ACK, 0))
    data = burst_duration(BENCH, PhyHeader(OfdmFrameType.DATA, 0, mcs=1,
                                           payload_len=BENCH.block_size))
    assert link.reply_timeout() == pytest.approx(ack + 0.8 + 1.0)
    assert link.data_timeout() == pytest.approx(data + 0.8 + 1.0)
    assert link.reply_timeout() < link.data_timeout()


def test_a_denser_mcs_shortens_the_data_timeout() -> None:
    slow = OfdmLink(BENCH, None, mcs_index=0, ptt_turnaround=0.1)
    fast = OfdmLink(BENCH, None, mcs_index=3, ptt_turnaround=0.1)
    assert fast.data_timeout() < slow.data_timeout()


# -- required test 12: segmentation and reassembly -------------------------- #

@pytest.mark.parametrize("size,blocks", [(0, 1), (1, 1), (512, 1), (513, 2),
                                         (4096, 8), (4097, 9)])
def test_a_message_of_any_size_arrives_intact(size: int, blocks: int) -> None:
    payload = _payload(size)
    sender, receiver, received, ok = _exchange(payload)

    assert ok
    assert received == payload
    assert sender.adaptation.blocks_sent == blocks
    assert sender.adaptation.blocks_acked == blocks
    assert receiver.adaptation.blocks_received == blocks
    assert sender.status.tx_bytes == size
    assert receiver.status.rx_bytes == size


def test_a_duplicate_block_is_reacknowledged_and_dropped() -> None:
    # A lost ACK makes the sender repeat a block the receiver already has. It must
    # answer again -- silence would strand the sender -- and must not append the
    # payload twice.
    payload = _payload(1200)          # three blocks
    swallowed = []

    def eat_the_first_ack(count: int, samples: np.ndarray) -> np.ndarray:
        # Wreck the receiver's first acknowledgement on its way back.
        if count == 1:
            swallowed.append(count)
            return np.zeros_like(samples)
        return samples

    near, far = simulated_pair(BENCH, ChannelSpec(snr_db=18.0, delay=400,
                                                  trailing=1500), seed=SEED)
    far.damage = eat_the_first_ack
    sender = OfdmLink(BENCH, near, mcs_index=1, ptt_turnaround=TURNAROUND,
                      timeout_margin=MARGIN)
    receiver = OfdmLink(BENCH, far, mcs_index=1, ptt_turnaround=TURNAROUND,
                        timeout_margin=MARGIN)
    got: dict[str, bytes | None] = {}
    listener = threading.Thread(
        target=lambda: got.__setitem__("data", receiver.receive_message(msg_id=5)),
        daemon=True)
    listener.start()
    ok = sender.send_message(5, payload)
    listener.join(timeout=180)

    assert swallowed == [1]
    assert ok
    assert got["data"] == payload
    assert sender.status.retries == 1
    assert receiver.adaptation.duplicates == 1
    assert receiver.adaptation.blocks_received == 3


def test_blocks_are_reassembled_in_order_not_arrival_order() -> None:
    payload = bytes(range(256)) * 8         # 2048 bytes, four distinguishable blocks
    _, _, received, ok = _exchange(payload)
    assert ok
    assert received == payload


# -- retry and failure ------------------------------------------------------ #

def test_a_damaged_block_is_nacked_and_resent() -> None:
    payload = _payload(1024)          # two blocks

    def wreck_the_first_block(count: int, samples: np.ndarray) -> np.ndarray:
        if count == 1:
            damaged = samples.copy()
            start = len(damaged) // 2
            damaged[start:] += np.random.default_rng(3).normal(
                0.0, 0.6, len(damaged) - start)
            return damaged
        return samples

    sender, receiver, received, ok = _exchange(payload, damage=wreck_the_first_block)

    assert ok
    assert received == payload
    assert sender.status.retries == 1
    assert sender.adaptation.retransmissions == 1
    assert sender.adaptation.blocks_nacked == 1
    assert sender.adaptation.blocks_sent == 2
    assert sender.adaptation.blocks_acked == 2
    # A block that needed a second try is not a lost packet.
    assert sender.adaptation.packet_error_rate == 0.0
    assert sender.adaptation.retransmission_rate == 0.5


def test_a_sender_gives_up_after_the_retry_limit() -> None:
    payload = _payload(400)
    near, far = simulated_pair(BENCH, ChannelSpec(snr_db=15.0), seed=SEED)
    # Nothing ever comes back.
    near.damage = lambda count, samples: np.zeros_like(samples)
    sender = OfdmLink(BENCH, near, mcs_index=1, max_retries=2,
                      ptt_turnaround=0.0, timeout_margin=0.05)

    assert sender.send_message(9, payload) is False
    assert sender.status.state == "failed"
    assert sender.status.last_block_ok is False
    assert near.transmissions == 3               # the first try plus two retries
    assert sender.adaptation.retransmissions == 2
    assert sender.adaptation.blocks_acked == 0
    assert sender.adaptation.packet_error_rate == 1.0


def test_a_receiver_gives_up_when_the_peer_goes_quiet() -> None:
    _, far = simulated_pair(BENCH, ChannelSpec(), seed=SEED)
    receiver = OfdmLink(BENCH, far, ptt_turnaround=0.0, timeout_margin=0.0)
    assert receiver.receive_message(timeout=0.05) is None
    assert receiver.status.state == "failed"


def test_a_block_for_another_message_is_ignored() -> None:
    payload = _payload(200)
    near, far = simulated_pair(BENCH, ChannelSpec(snr_db=20.0), seed=SEED)
    sender = OfdmLink(BENCH, near, mcs_index=1, ptt_turnaround=0.0, timeout_margin=0.1)
    receiver = OfdmLink(BENCH, far, mcs_index=1, ptt_turnaround=0.0, timeout_margin=0.1)

    # A burst belonging to a different message arrives first.
    sender.send_message(77, payload)
    assert receiver.receive_message(msg_id=78, timeout=0.2) is None


# -- required test 20: a deterministic seeded simulation -------------------- #

def _run_reference_transfer() -> tuple[OfdmLink, OfdmLink, bytes | None, bool]:
    """The canonical run: 4096 bytes at 12 dB, seed 0xA5, one forced NACK."""
    def wreck_the_third_burst(count: int, samples: np.ndarray) -> np.ndarray:
        if count == 3:
            damaged = samples.copy()
            start = len(damaged) // 2
            damaged[start:] += np.random.default_rng(1).normal(
                0.0, 0.5, len(damaged) - start)
            return damaged
        return samples

    return _exchange(_payload(4096), ChannelSpec(snr_db=12.0, delay=500,
                                                 trailing=1500),
                     seed=0xA5, damage=wreck_the_third_burst)


def test_the_reference_transfer_delivers_exactly_one_retry() -> None:
    payload = _payload(4096)
    sender, receiver, received, ok = _run_reference_transfer()

    assert ok
    assert received == payload
    assert sender.status.retries == 1
    assert sender.adaptation.retransmissions == 1
    assert sender.adaptation.blocks_sent == 8
    assert sender.adaptation.blocks_acked == 8
    assert receiver.adaptation.blocks_received == 8
    assert receiver.adaptation.mean_snr_db == pytest.approx(12.0, abs=1.5)


def test_the_reference_transfer_repeats_identically() -> None:
    # Same seed, same spec, same numbers -- so a failure is something you can go
    # and look at rather than something that happened once.
    first_sender, first_receiver, first_bytes, _ = _run_reference_transfer()
    again_sender, again_receiver, again_bytes, _ = _run_reference_transfer()

    assert first_bytes == again_bytes
    assert first_receiver.adaptation.snr_history == again_receiver.adaptation.snr_history
    assert first_receiver.adaptation.evm_history == again_receiver.adaptation.evm_history
    assert first_sender.status.retries == again_sender.status.retries
    assert first_sender.channel_seconds == again_sender.channel_seconds


# -- measured throughput ---------------------------------------------------- #

def test_throughput_is_measured_from_airtime_and_sits_below_the_phy_rate() -> None:
    # A real figure, not a nominal one: it counts the preamble, the training
    # block, the header, every acknowledgement and every retry. The BENCH MCS1
    # payload carriers alone would give 1833 bit/s; end to end is well under that.
    payload = _payload(4096)
    sender, _, received, ok = _exchange(payload, ChannelSpec(snr_db=15.0, delay=400,
                                                            trailing=1500))
    assert ok and received == payload
    phy_rate = BENCH.num_data_carriers * 2 * 0.5 / BENCH.symbol_duration
    assert phy_rate == pytest.approx(1833.0, abs=1.0)
    assert 0 < sender.status.est_bitrate_bps < phy_rate
    assert sender.channel_seconds > 8 * burst_duration(
        BENCH, PhyHeader(OfdmFrameType.DATA, 0, mcs=1, payload_len=BENCH.block_size))


def test_a_retry_lowers_the_measured_throughput() -> None:
    payload = _payload(4096)
    clean, _, _, _ = _exchange(payload, ChannelSpec(snr_db=15.0, delay=400,
                                                    trailing=1500))
    retried, _, _, _ = _run_reference_transfer()
    assert retried.status.est_bitrate_bps < clean.status.est_bitrate_bps


# -- status and adaptation state -------------------------------------------- #

def test_status_reports_progress_and_the_configured_waveform() -> None:
    payload = _payload(2048)
    sender, receiver, _, _ = _exchange(payload, mcs_index=2)
    assert sender.status.profile == "BENCH"
    assert sender.status.mcs == 2
    assert sender.status.state == "idle"
    assert sender.status.percent == 100
    assert receiver.status.percent == 100
    assert sender.status.last_block_ok is True


def test_status_publishes_every_state_the_ui_expects_to_see() -> None:
    seen = []
    payload = _payload(600)
    near, far = simulated_pair(BENCH, ChannelSpec(snr_db=18.0), seed=SEED)
    sender = OfdmLink(BENCH, near, mcs_index=1, ptt_turnaround=0.0,
                      timeout_margin=0.2, on_status=lambda s: seen.append(s.state))
    receiver = OfdmLink(BENCH, far, mcs_index=1, ptt_turnaround=0.0, timeout_margin=0.2)
    listener = threading.Thread(target=lambda: receiver.receive_message(msg_id=1),
                                daemon=True)
    listener.start()
    sender.send_message(1, payload)
    listener.join(timeout=120)

    assert {"transmitting", "waiting_ack", "idle"} <= set(seen)


def test_percent_is_zero_rather_than_a_division_by_zero_before_the_total_is_known() -> None:
    assert OfdmStatus().percent == 0


def test_an_unavailable_measurement_stays_unavailable() -> None:
    # Never fake a metric: an adaptation controller would believe it.
    empty = LinkMetrics()
    assert empty.snr_db is None
    assert not empty.snr_available
    assert "failed" in empty.summary()

    state = AdaptationState()
    assert state.mean_snr_db is None
    assert state.packet_error_rate is None
    assert state.retransmission_rate is None
    assert state.worst_carriers is None


def test_adaptation_state_accumulates_the_per_carrier_picture() -> None:
    # The input a per-subcarrier bit-loading map would eventually be computed
    # from. Phase 1 only fills it in.
    payload = _payload(2048)
    _, receiver, _, _ = _exchange(payload)
    state = receiver.adaptation

    assert state.carrier_power is not None
    assert len(state.carrier_power) == BENCH.num_carriers
    assert len(state.snr_history) >= 4
    assert state.worst_carriers is not None
    assert len(state.worst_carriers) == BENCH.num_carriers
    assert "blocks accepted" in state.summary()


def test_a_notched_carrier_shows_up_as_the_worst_one_in_the_history() -> None:
    payload = _payload(1024)
    spacing = BENCH.subcarrier_spacing
    target = 30
    spec = ChannelSpec(snr_db=18.0, delay=400, trailing=2000,
                       notch=((BENCH.first_carrier + target - 0.5) * spacing,
                              (BENCH.first_carrier + target + 0.5) * spacing, -25.0))
    _, receiver, received, ok = _exchange(payload, spec)

    assert ok and received == payload
    assert int(receiver.adaptation.worst_carriers[0]) == target


def test_the_sender_summary_names_blocks_acked_and_the_receiver_blocks_accepted() -> None:
    # A receiver never sends a block, so "0/0 blocks acked" would read as failure.
    payload = _payload(600)
    sender, receiver, _, _ = _exchange(payload)
    assert "blocks acked" in sender.adaptation.summary()
    assert "blocks accepted" in receiver.adaptation.summary()


def test_the_log_narrates_the_transfer() -> None:
    lines = []
    payload = _payload(600)
    near, far = simulated_pair(BENCH, ChannelSpec(snr_db=18.0), seed=SEED)
    sender = OfdmLink(BENCH, near, mcs_index=1, ptt_turnaround=0.0,
                      timeout_margin=0.2, on_log=lines.append)
    receiver = OfdmLink(BENCH, far, mcs_index=1, ptt_turnaround=0.0, timeout_margin=0.2)
    listener = threading.Thread(target=lambda: receiver.receive_message(msg_id=1),
                                daemon=True)
    listener.start()
    sender.send_message(1, payload)
    listener.join(timeout=120)

    joined = "\n".join(lines)
    assert "sending #1" in joined
    assert "MCS1" in joined
    assert "complete" in joined
    # Log lines reach a Windows console through tools/ofdm_bench.py.
    assert joined.isascii()
