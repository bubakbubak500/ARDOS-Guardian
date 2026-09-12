"""Deterministic acceptance checks for the G2 2.4.8 SC-FTN core port."""

from __future__ import annotations

import threading
import hashlib
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from guardian.ofdm.channel import ChannelSpec
from guardian.ofdm.coding import FecProfile
from guardian.ofdm.config import MCS_TABLE, SC_MCS_TABLE, OfdmConfigError, mcs, sc_mcs
from guardian.ofdm.framing import OfdmFrameType, PhyHeader, SubBlock
from guardian.ofdm.link import OfdmLink, simulated_pair
from guardian.ofdm.automatic import automatic_g2_policy
from guardian.waveforms.config import FAMILY_PROFILE_LADDERS, PROFILES, SC_FTN_2K7
from guardian.waveforms.constellation import BITS_PER_SYMBOL, demap_llr, map_bits
from guardian.waveforms.framing import ExperimentalBurstCodec
from guardian.waveforms.shaping import PAS64_INPUT_BITS


def test_only_sc_ftn_profile_ladder_is_production_surface() -> None:
    assert tuple(FAMILY_PROFILE_LADDERS) == ("sc_ftn",)
    assert len(FAMILY_PROFILE_LADDERS["sc_ftn"]) == 6
    assert tuple(PROFILES) == FAMILY_PROFILE_LADDERS["sc_ftn"]
    with pytest.raises(ValueError, match="SC-FTN"):
        from guardian.waveforms.config import profile_for

        profile_for("ofdm", "2K7")


def test_sc_mcs_wire_table_keeps_all_ids_without_expanding_legacy_table() -> None:
    assert [entry.index for entry in MCS_TABLE] == [0, 1, 2, 3]
    assert [entry.index for entry in SC_MCS_TABLE] == list(range(21))
    with pytest.raises(OfdmConfigError):
        mcs(4)
    assert sc_mcs(17).modulation == "gqam64"
    assert sc_mcs(20).modulation == "pas64"

    for index in range(21):
        raw = PhyHeader(
            OfdmFrameType.DATA,
            0x501,
            mcs=index,
            payload_len=16,
            subblock_count=1,
        ).encode()
        assert PhyHeader.decode(raw, mcs_lookup=sc_mcs).mcs == index


@pytest.mark.parametrize("modulation", tuple(BITS_PER_SYMBOL))
def test_sc_constellations_have_exact_soft_roundtrip(modulation: str) -> None:
    rng = np.random.default_rng(0x213)
    width = BITS_PER_SYMBOL[modulation]
    bits = rng.integers(0, 2, 128 * width, dtype=np.int8)
    llr = demap_llr(map_bits(bits, modulation), modulation, 0.01)
    assert np.array_equal((llr > 0).astype(np.int8), bits)


def test_pas64_distribution_matcher_roundtrips() -> None:
    rng = np.random.default_rng(0xA564)
    bits = rng.integers(0, 2, PAS64_INPUT_BITS * 2, dtype=np.int8)
    symbols = map_bits(bits, "pas64")
    recovered = (demap_llr(symbols, "pas64", 0.01) > 0).astype(np.int8)
    assert np.array_equal(recovered, bits)
    assert np.mean(np.abs(symbols) ** 2) == pytest.approx(1.0)


@pytest.mark.parametrize("name", tuple(PROFILES))
def test_each_sc_width_builds_and_decodes_a_crc_checked_frame(name: str) -> None:
    profile = PROFILES[name]
    payload = bytes(range(32))
    header = PhyHeader(
        OfdmFrameType.DATA,
        0x601,
        mcs=1,
        fec=FecProfile.LDPC_1_2,
        payload_len=len(payload),
        subblock_count=1,
    )
    codec = ExperimentalBurstCodec()
    waveform = codec.build_burst(profile, header, blocks=[SubBlock(0, payload)])
    decoded = codec.decode_burst(profile, waveform)
    assert decoded.ok
    assert decoded.payload == payload
    assert decoded.blocks == {0: payload}
    assert decoded.metrics.equalizer_mode == "mmse"


def test_selective_repeat_link_uses_sc_codec_end_to_end() -> None:
    near, far = simulated_pair(
        SC_FTN_2K7,
        ChannelSpec(snr_db=None),
        seed=0xA5,
    )
    sender = OfdmLink(
        SC_FTN_2K7,
        near,
        mcs_index=1,
        ptt_turnaround=0.01,
        timeout_margin=0.2,
        max_retries=1,
    )
    receiver = OfdmLink(
        SC_FTN_2K7,
        far,
        mcs_index=1,
        ptt_turnaround=0.01,
        timeout_margin=0.2,
        max_retries=1,
    )
    payload = bytes(range(32))
    received: dict[str, bytes | None] = {}
    listener = threading.Thread(
        target=lambda: received.setdefault("payload", receiver.receive_message(0x701)),
        daemon=True,
    )
    listener.start()
    assert sender.send_message(0x701, payload)
    listener.join(10.0)
    assert received.get("payload") == payload
    assert near.transmissions >= 1
    assert far.transmissions >= 1


@pytest.mark.parametrize(
    ("bandwidth", "backend", "model", "expected"),
    [
        ("2K7", "", "", (1, 17, FecProfile.LDPC_1_2, 512, 512, 256, 1, 14.5, 60, 0.0)),
        ("4K5", "guardian_k5", "", (1, 3, FecProfile.LDPC_1_2, 2048, 512, 256, 1, 7.5, 200, 0.8)),
        ("4K5", "vox", "Quansheng UV-K5", (1, 3, FecProfile.LDPC_1_2, 2048, 512, 256, 1, 7.5, 200, 0.0)),
        ("10K", "vox", "IC-705", (17, 17, FecProfile.LDPC_4_5, 16384, 2048, 2048, 3, 18.0, 140, 0.0)),
    ],
)
def test_automatic_policy_preserves_sc_ftn_matrix(
    bandwidth: str,
    backend: str,
    model: str,
    expected: tuple[object, ...],
) -> None:
    policy = automatic_g2_policy(
        "sc_ftn",
        bandwidth,
        radio_backend=backend,
        radio_model=model,
    )
    assert (
        policy.initial_mcs,
        policy.maximum_mcs,
        policy.initial_fec,
        policy.initial_burst_bytes,
        policy.minimum_burst_bytes,
        policy.arq_block_bytes,
        policy.maximum_retries,
        policy.maximum_train_seconds,
        policy.tx_guard_ms,
        policy.acquisition_lead_seconds,
    ) == expected


def test_2k7_policy_geometry_is_applied_to_the_profile() -> None:
    from guardian.waveforms.config import profile_for

    policy = automatic_g2_policy("sc_ftn", "2K7")
    profile = replace(
        profile_for(policy.waveform, policy.bandwidth),
        center_hz=policy.center_hz,
        nyquist_symbol_rate=policy.nyquist_symbol_rate,
        symbol_rate=policy.symbol_rate,
        bootstrap_modulation=policy.bootstrap_modulation,
        data_acquisition_lead_seconds=policy.acquisition_lead_seconds,
        reference_metric_blocks=policy.reference_metric_blocks,
    )
    assert profile.center_hz == pytest.approx(1779.4117647058824)
    assert profile.nyquist_symbol_rate == pytest.approx(2541.176470588235)
    assert profile.symbol_rate == pytest.approx(2823.529411764706)
    assert profile.bootstrap_modulation == "qpsk"
    assert profile.reference_metric_blocks == 2
    assert profile.data_acquisition_lead_seconds == 0.0


def test_pilot_clock_tracking_recovers_a_long_sc_ftn_frame() -> None:
    profile = replace(SC_FTN_2K7, symbol_clock_tracking=True)
    payload = np.random.default_rng(0x950509).integers(
        0, 256, 4096, dtype=np.uint8
    ).tobytes()
    blocks = [SubBlock(index, payload[index * 256:(index + 1) * 256])
              for index in range(16)]
    header = PhyHeader(
        OfdmFrameType.DATA,
        0x955,
        block_count=16,
        mcs=5,
        payload_len=len(payload),
        subblock_count=16,
        fec=FecProfile.LDPC_1_2,
        version=3,
    )
    codec = ExperimentalBurstCodec()
    source = codec.build_burst(profile, header, blocks=blocks)
    factor = 1.0 + 7.0 / 1e6
    aired = np.interp(
        np.arange(round(len(source) * factor)) / factor,
        np.arange(len(source)),
        source,
    )
    decoded = codec.decode_burst(profile, aired)
    assert decoded.blocks == {block.sequence: block.payload for block in blocks}
    assert decoded.metrics.sample_clock_ppm == pytest.approx(7.0, abs=2.0)


def test_pilot_clock_tracking_is_local_to_each_combined_capture() -> None:
    profile = replace(SC_FTN_2K7, symbol_clock_tracking=True)
    payload = np.random.default_rng(0x956).integers(
        0, 256, 4096, dtype=np.uint8
    ).tobytes()
    blocks = [SubBlock(index, payload[index * 256:(index + 1) * 256])
              for index in range(16)]
    codec = ExperimentalBurstCodec()
    parts: list[np.ndarray] = []
    for msg_id, ppm in ((0x9561, 40.0), (0x9562, -40.0)):
        header = PhyHeader(
            OfdmFrameType.DATA,
            msg_id,
            block_count=16,
            mcs=5,
            payload_len=len(payload),
            subblock_count=16,
            fec=FecProfile.LDPC_1_2,
            version=3,
        )
        source = codec.build_burst(profile, header, blocks=blocks)
        factor = 1.0 + ppm / 1e6
        parts.append(np.interp(
            np.arange(round(len(source) * factor)) / factor,
            np.arange(len(source)),
            source,
        ))
        parts.append(np.zeros(profile.sample_rate // 5))

    decoded = codec.decode_many(profile, np.concatenate(parts))
    assert [item.header.msg_id for item in decoded] == [0x9561, 0x9562]
    assert all(item.blocks == {
        block.sequence: block.payload for block in blocks
    } for item in decoded)
    assert [item.metrics.sample_clock_ppm for item in decoded] == pytest.approx(
        [40.0, -40.0], abs=2.0
    )


def test_sc_audio_matches_immutable_g2_reference_when_available() -> None:
    """Compare a deterministic SC frame with the audited G2 2.4.8 oracle."""
    root = Path(__file__).resolve().parents[1]
    oracle = root / "output" / "g2-port-audit" / "g2-2.4.8"
    if not (oracle / "guardian" / "waveforms" / "framing.py").exists():
        pytest.skip("audited G2 2.4.8 source is not present")
    payload = bytes(range(64))
    header = PhyHeader(
        OfdmFrameType.DATA,
        0xA24,
        mcs=5,
        fec=FecProfile.LDPC_1_2,
        payload_len=len(payload),
        subblock_count=1,
    )
    codec = ExperimentalBurstCodec()
    waveform = codec.build_burst(SC_FTN_2K7, header, blocks=[SubBlock(0, payload)])
    local_decoded = codec.decode_burst(
        SC_FTN_2K7, waveform
    )
    local_digest = hashlib.sha256(waveform.astype("<f8").tobytes()).hexdigest()
    local_header_digest = hashlib.sha256(header.encode()).hexdigest()
    script = (
        "import hashlib; "
        "from guardian.waveforms.config import SC_FTN_2K7; "
        "from guardian.waveforms.framing import ExperimentalBurstCodec; "
        "from guardian.ofdm.framing import PhyHeader, OfdmFrameType, SubBlock; "
        "from guardian.ofdm.coding import FecProfile; "
        "p=bytes(range(64)); h=PhyHeader(OfdmFrameType.DATA,0xA24,mcs=5,"
        "fec=FecProfile.LDPC_1_2,payload_len=len(p),subblock_count=1); "
        "w=ExperimentalBurstCodec.build_burst(SC_FTN_2K7,h,blocks=[SubBlock(0,p)]); "
        "d=ExperimentalBurstCodec.decode_burst(SC_FTN_2K7,w); "
        "print(hashlib.sha256(h.encode()).hexdigest(), "
        "hashlib.sha256(w.astype('<f8').tobytes()).hexdigest(), "
        "d.ok, d.payload.hex())"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(oracle)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=oracle,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    oracle_header, oracle_waveform, oracle_ok, oracle_payload = result.stdout.strip().split()
    assert local_header_digest == oracle_header
    assert local_digest == oracle_waveform
    assert oracle_ok == "True"
    assert local_decoded.ok
    assert local_decoded.payload == bytes.fromhex(oracle_payload) == payload
