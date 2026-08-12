"""Adaptive FEC, version-2 burst framing, and joint link control."""

from __future__ import annotations

import threading

import numpy as np
import pytest

from guardian.ofdm import BENCH
from guardian.ofdm.adaptation import AdaptationConfig, LinkAdaptationController
from guardian.ofdm.coding import (FEC_SPECS, FecProfile, decode_soft,
                                  combine_harq_soft,
                                  effective_rate, encode_bits, encoded_bits,
                                  mother_bits)
from guardian.ofdm.config import HEADER_MCS
from guardian.ofdm.framing import (AckBitmap, SUPERFRAME_VERSION, OfdmFrameType, PhyHeader,
                                   SubBlock, build_burst, burst_duration,
                                   decode_burst, header_symbols,
                                   protocol_overhead_bytes, section_symbols)
from guardian.ofdm.link import OfdmLink, simulated_pair
from guardian.waveforms.config import SC_FTN_2K7
from guardian.waveforms.framing import ExperimentalBurstCodec


@pytest.mark.parametrize("spec", FEC_SPECS, ids=lambda item: item.label)
def test_every_punctured_profile_round_trips_the_mother_code(spec) -> None:
    raw = np.random.default_rng(17).integers(0, 2, 4096, dtype=np.int8)
    coded = encode_bits(raw, spec.profile)
    soft = (2.0 * coded - 1.0) * 20.0
    decoded = decode_soft(soft, 512, spec.profile)

    assert np.array_equal(decoded[:len(raw)], raw)
    assert len(coded) == encoded_bits(512, spec.profile)
    assert len(coded) <= mother_bits(512)
    assert effective_rate(len(raw), len(coded)) == pytest.approx(
        len(raw) / len(coded)
    )


def test_faster_fec_profiles_transmit_strictly_fewer_bits() -> None:
    for family in ("conv", "ldpc"):
        lengths = [
            encoded_bits(512, spec.profile)
            for spec in FEC_SPECS if spec.family == family
        ]
        assert lengths == sorted(lengths, reverse=True)
        assert len(set(lengths)) == len(lengths)


@pytest.mark.parametrize("spec", FEC_SPECS, ids=lambda item: item.label)
def test_header_explicitly_selects_each_fec_and_three_blocks_decode(spec) -> None:
    rng = np.random.default_rng(29)
    blocks = [
        SubBlock(7, rng.integers(0, 256, 512, dtype=np.uint8).tobytes()),
        SubBlock(9, rng.integers(0, 256, 512, dtype=np.uint8).tobytes()),
        SubBlock(12, rng.integers(0, 256, 177, dtype=np.uint8).tobytes()),
    ]
    header = PhyHeader(
        OfdmFrameType.DATA, 123, block_seq=44, block_count=20, mcs=1,
        fec=spec.profile, payload_len=sum(len(item.payload) for item in blocks),
        subblock_count=len(blocks), retransmission=True,
    )
    decoded = decode_burst(BENCH, build_burst(BENCH, header, blocks=blocks))

    assert decoded.ok
    assert decoded.header.fec is spec.profile
    assert decoded.header.retransmission is True
    assert decoded.block_order == (7, 9, 12)
    assert decoded.blocks == {item.sequence: item.payload for item in blocks}


def test_a_1024_byte_arq_subblock_is_supported_by_version_two() -> None:
    payload = np.random.default_rng(31).integers(
        0, 256, 1024, dtype=np.uint8
    ).tobytes()
    header = PhyHeader(
        OfdmFrameType.DATA, 4, block_count=1, mcs=1,
        fec=FecProfile.FEC_3_4, payload_len=len(payload), subblock_count=1,
    )
    decoded = decode_burst(BENCH, build_burst(BENCH, header, payload))
    assert decoded.ok
    assert decoded.payload == payload


def test_superframe_v3_carries_more_than_32_independently_checked_blocks() -> None:
    rng = np.random.default_rng(310)
    blocks = [
        SubBlock(index, rng.integers(0, 256, 24, dtype=np.uint8).tobytes())
        for index in range(40)
    ]
    with pytest.raises(ValueError, match="1..32"):
        PhyHeader(
            OfdmFrameType.DATA, 310, block_count=40, payload_len=960,
            subblock_count=40,
        ).encode()
    header = PhyHeader(
        OfdmFrameType.DATA, 310, block_count=40, mcs=1,
        payload_len=sum(len(item.payload) for item in blocks),
        subblock_count=len(blocks), version=SUPERFRAME_VERSION,
    )
    decoded = decode_burst(BENCH, build_burst(BENCH, header, blocks=blocks))
    assert decoded.ok
    assert decoded.header.version == SUPERFRAME_VERSION
    assert decoded.block_order == tuple(range(40))
    assert decoded.blocks == {item.sequence: item.payload for item in blocks}


def test_a_broken_manifest_never_delivers_unidentified_bytes() -> None:
    payload = bytes(range(256)) * 2
    header = PhyHeader(
        OfdmFrameType.DATA, 8, block_count=1, mcs=1,
        payload_len=len(payload), subblock_count=1,
    )
    waveform = build_burst(BENCH, header, payload)
    first = (BENCH.preamble_symbols + BENCH.training_symbols
             + header_symbols(BENCH)) * BENCH.symbol_samples
    manifest_symbols = section_symbols(BENCH, 6, HEADER_MCS.modulation)
    damaged = waveform.copy()
    damaged[first:first + manifest_symbols * BENCH.symbol_samples] = 0.0
    decoded = decode_burst(BENCH, damaged)

    assert decoded.header is not None
    assert not decoded.ok
    assert decoded.payload is None
    assert decoded.blocks == {}
    assert "manifest" in (decoded.metrics.error or "")


def test_bitmap_is_variable_length_and_carries_remote_quality() -> None:
    received = frozenset({0, 1, 63, 64, 129})
    bitmap = AckBitmap(130, received, remote_snr_db=12.25,
                       remote_evm_rms=0.135)
    raw = bitmap.encode()
    decoded = AckBitmap.decode(raw)

    assert len(raw) == 4 + 17
    assert decoded.total_blocks == 130
    assert decoded.received == received
    assert decoded.remote_snr_db == 12.0  # encoded in half-dB units
    assert decoded.remote_evm_rms == pytest.approx(0.135, abs=0.003)


def test_sparse_and_bitmap_ack_represent_identical_receiver_state() -> None:
    state = AckBitmap(4096, frozenset(set(range(4096)) - {7, 29, 2048}))
    dense = state.encode()
    compact = state.encode_compact()
    assert len(compact) < len(dense) / 10
    assert AckBitmap.decode(dense) == AckBitmap.decode(compact)


def test_compact_ack_keeps_dense_encoding_when_errors_are_not_sparse() -> None:
    state = AckBitmap(32, frozenset(range(0, 32, 2)))
    assert state.encode_compact() == state.encode()


def test_protocol_overhead_counts_v2_manifests_crcs_and_control_payloads() -> None:
    data = PhyHeader(
        OfdmFrameType.DATA, 9, block_count=4, payload_len=1536,
        subblock_count=3,
    )
    ack_payload = AckBitmap(4, frozenset({0, 2})).encode()
    ack = PhyHeader(
        OfdmFrameType.ACK, 9, block_count=4, payload_len=len(ack_payload),
    )

    # 16-byte header, three 4-byte manifest entries, manifest CRC, and three
    # subblock CRCs. ACK adds its encoded bitmap plus its CRC to the header.
    assert protocol_overhead_bytes(data) == 16 + 3 * 4 + 2 + 3 * 2
    assert protocol_overhead_bytes(ack) == 16 + len(ack_payload) + 2


def test_joint_adaptation_uses_hysteresis_and_changes_one_axis_at_a_time() -> None:
    controller = LinkAdaptationController(AdaptationConfig())
    assert controller.profile.fec is FecProfile.FEC_1_2
    assert controller.profile.burst_bytes == 2048

    for _ in range(3):
        controller.report_burst(sent_blocks=4, acked_blocks=4,
                                unique_bytes=2048, elapsed_seconds=10.0,
                                remote_snr_db=14.0, remote_evm_rms=0.12)
    assert controller.profile.fec is FecProfile.FEC_2_3
    assert controller.profile.burst_bytes == 2048
    assert controller.remote_snr_ewma_db == pytest.approx(14.0)
    assert controller.remote_evm_ewma == pytest.approx(0.12)

    for _ in range(3):
        controller.report_burst(sent_blocks=4, acked_blocks=4,
                                unique_bytes=2048, elapsed_seconds=9.0)
    assert controller.profile.fec is FecProfile.FEC_2_3
    assert controller.profile.burst_bytes == 4096

    controller.report_burst(sent_blocks=8, acked_blocks=7,
                            retransmitted_bytes=512, unique_bytes=3584,
                            elapsed_seconds=12.0)
    assert controller.profile.fec is FecProfile.FEC_1_2
    assert controller.profile.burst_bytes == 4096
    controller.report_burst(sent_blocks=8, acked_blocks=7)
    assert controller.profile.burst_bytes == 2048


def test_modern_adaptation_uses_only_the_ldpc_ladder_and_safe_retry() -> None:
    controller = LinkAdaptationController(AdaptationConfig(modern_ldpc=True))
    assert controller.profile.fec is FecProfile.LDPC_1_2
    for _ in range(3):
        controller.report_burst(sent_blocks=4, acked_blocks=4)
    assert controller.profile.fec is FecProfile.LDPC_3_4
    for _ in range(3):
        controller.report_burst(sent_blocks=4, acked_blocks=4)
    # The alternating second clean window grows the burst; the third advances FEC.
    for _ in range(3):
        controller.report_burst(sent_blocks=4, acked_blocks=4)
    assert controller.profile.fec is FecProfile.LDPC_9_10
    assert controller.fec_for_retry(1) is FecProfile.LDPC_3_4
    assert controller.fec_for_retry(2) is FecProfile.LDPC_1_2
    assert controller.fec_for_retry(3) is FecProfile.FEC_1_2


def test_rate_compatible_harq_combines_punctured_observations_on_mother_code() -> None:
    raw = np.random.default_rng(233).integers(0, 2, 512 * 8, dtype=np.int8)
    weak = encode_bits(raw, FecProfile.FEC_7_8)
    strong = encode_bits(raw, FecProfile.FEC_1_2)
    weak_llr = (2.0 * weak - 1.0) * 0.25
    strong_llr = (2.0 * strong - 1.0) * 1.0
    combined = combine_harq_soft(
        strong_llr, FecProfile.FEC_1_2,
        weak_llr, FecProfile.FEC_7_8, 512,
    )
    assert len(combined) == len(strong_llr)
    # Positions seen in both rounds have more confidence; newly revealed mother
    # parity positions retain the strong retry's confidence.
    assert np.max(np.abs(combined)) > np.max(np.abs(strong_llr))
    decoded = decode_soft(combined, 512, FecProfile.FEC_1_2)
    assert np.array_equal(decoded[:len(raw)], raw)

def test_fixed_profiles_do_not_move_and_retries_strengthen_fec() -> None:
    config = AdaptationConfig(
        adaptive_fec=False, fixed_fec=FecProfile.FEC_7_8,
        adaptive_burst=False, fixed_burst_bytes=8192,
        min_burst_bytes=512, max_burst_bytes=16384,
    )
    controller = LinkAdaptationController(config)
    for _ in range(10):
        controller.report_burst(sent_blocks=16, acked_blocks=0)
    assert controller.profile.fec is FecProfile.FEC_7_8
    assert controller.profile.burst_bytes == 8192
    assert controller.fec_for_retry(1) is FecProfile.FEC_5_6
    assert controller.fec_for_retry(4) is FecProfile.FEC_1_2


def test_256_byte_bursts_are_available_with_256_byte_arq_blocks() -> None:
    controller = LinkAdaptationController(AdaptationConfig(
        adaptive_burst=False, fixed_burst_bytes=256,
        min_burst_bytes=256, max_burst_bytes=256,
        arq_block_bytes=256,
    ))
    assert controller.profile.burst_bytes == 256
    assert controller.profile.arq_block_bytes == 256
    with pytest.raises(ValueError, match="hold one ARQ block"):
        AdaptationConfig(min_burst_bytes=256, arq_block_bytes=512)


def test_timeout_multiplier_scales_duration_derived_deadlines() -> None:
    normal = OfdmLink(BENCH, None, timeout_multiplier=1.0)
    cautious = OfdmLink(BENCH, None, timeout_multiplier=1.8)
    assert cautious.reply_timeout(32) == pytest.approx(
        normal.reply_timeout(32) * 1.8
    )
    assert cautious.data_timeout() == pytest.approx(normal.data_timeout() * 1.8)


def test_adaptive_train_uses_clean_hysteresis_and_fast_backoff() -> None:
    link = OfdmLink(
        BENCH, None, train_bursts=4, adaptive_train=True, superframe=True,
    )
    assert link._current_train_bursts == 1
    for _ in range(3):
        link._report_train_feedback(4, 4)
    assert link._current_train_bursts == 2
    link._report_train_feedback(8, 7)
    assert link._current_train_bursts == 1


def test_adaptive_mcs_moves_slowly_up_and_immediately_down() -> None:
    link = OfdmLink(BENCH, None, mcs_index=3, adaptive_mcs=True)
    assert link.mcs_index == 1
    assert link._maximum_mcs_index == 3
    for _ in range(3):
        link._report_mcs_feedback(4, 4, 18.0)
    assert link.mcs_index == 3
    link._report_mcs_feedback(4, 3, 18.0)
    assert link.mcs_index == 2


def test_experimental_mcs_ceiling_uses_physical_order_not_wire_id() -> None:
    link = OfdmLink(
        SC_FTN_2K7, None, mcs_index=7, adaptive_mcs=True,
        codec=ExperimentalBurstCodec(),
    )
    for _ in range(3):
        link._report_mcs_feedback(4, 4, 9.0)
    assert link.mcs_index == 7  # 8-PSK, never old-ID 256-QAM MCS4
    link._report_mcs_feedback(4, 3, 9.0)
    assert link.mcs_index == 1  # robust QPSK is the next physical step down


def test_legacy_mode_still_moves_a_multiblock_message_exactly_once() -> None:
    payload = np.random.default_rng(41).integers(
        0, 256, 700, dtype=np.uint8
    ).tobytes()
    near, far = simulated_pair(BENCH, seed=41)
    sender = OfdmLink(BENCH, near, legacy_mode=True, ptt_turnaround=0.0,
                      timeout_margin=0.2)
    receiver = OfdmLink(BENCH, far, legacy_mode=True, ptt_turnaround=0.0,
                        timeout_margin=0.2)
    held: dict[str, bytes | None] = {}
    listener = threading.Thread(
        target=lambda: held.__setitem__("payload", receiver.receive_message(77)),
        daemon=True,
    )
    listener.start()
    assert sender.send_message(77, payload)
    listener.join(timeout=30)
    assert not listener.is_alive()
    assert held["payload"] == payload
    assert near.transmissions == 2
    assert far.transmissions == 2


def test_higher_fec_rate_reduces_clean_burst_airtime() -> None:
    slow = PhyHeader(OfdmFrameType.DATA, 1, mcs=2,
                     fec=FecProfile.FEC_1_2, payload_len=512)
    fast = PhyHeader(OfdmFrameType.DATA, 1, mcs=2,
                     fec=FecProfile.FEC_7_8, payload_len=512)
    assert burst_duration(BENCH, fast) < burst_duration(BENCH, slow)
