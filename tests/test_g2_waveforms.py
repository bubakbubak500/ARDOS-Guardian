"""Independent Guardian G2 high-capacity waveform families."""

from __future__ import annotations

import threading

import numpy as np
import pytest

from guardian.config import StationConfig
from guardian.ofdm.channel import Channel, ChannelSpec
from guardian.ofdm.coding import FecProfile
from guardian.ofdm.config import (BENCH, SC_MCS_TABLE, OfdmConfigError, mcs,
                                  sc_mcs)
from guardian.ofdm.framing import (OfdmFrameError, OfdmFrameType, PhyHeader,
                                   SubBlock)
from guardian.ofdm.link import OfdmLink, simulated_pair
from guardian.payload import make_backend
from guardian.waveforms.config import (FAMILY_PROFILE_LADDERS, PROFILES as WAVE_PROFILES,
                                       SC_FDE_FTN_2K7, SC_FTN_2K7, SC_HS_2K7,
                                       SEFDM_2K7)
from guardian.waveforms.constellation import (BITS_PER_SYMBOL, demap_llr,
                                              map_bits)
from guardian.waveforms.shaping import PAS64_INPUT_BITS
from guardian.waveforms.framing import ExperimentalBurstCodec
from guardian.waveforms import bench as waveform_bench
from guardian.waveforms.capacity_report import benchmark_point, write_report


PROFILES = (SC_HS_2K7, SC_FTN_2K7, SEFDM_2K7)


def test_profiles_are_independent_and_fit_the_voice_channel() -> None:
    assert len({profile.family for profile in PROFILES}) == 3
    for profile in PROFILES:
        low, high = profile.occupied_band
        assert 250.0 <= low < high <= 3_200.0
        assert profile.occupied_bandwidth <= 2_700.0
        assert profile.sample_rate == 48_000
    assert SC_HS_2K7.ftn_tau == pytest.approx(1.0)


def test_experimental_decode_many_recovers_sc_ftn_train():
    codec = ExperimentalBurstCodec()
    payloads = [bytes([17 + index]) * 72 for index in range(2)]
    bursts = []
    for index, payload in enumerate(payloads):
        header = PhyHeader(
            OfdmFrameType.DATA, 33, index, 2, 1, len(payload),
            subblock_count=1,
        )
        bursts.append(codec.build_burst(
            SC_FTN_2K7, header, blocks=[SubBlock(index, payload)]
        ))
    gap = np.zeros(int(SC_FTN_2K7.sample_rate * 0.03))
    decoded = codec.decode_many(
        SC_FTN_2K7, np.concatenate([gap, bursts[0], gap, bursts[1], gap])
    )
    assert [item.blocks[index] for index, item in enumerate(decoded)] == payloads
    assert SC_FTN_2K7.ftn_tau == pytest.approx(0.9)
    assert SC_FTN_2K7.symbol_rate > SC_HS_2K7.symbol_rate
    assert SEFDM_2K7.sefdm_alpha < 1.0
    assert SEFDM_2K7.sefdm_alpha == pytest.approx(0.985)
    assert SEFDM_2K7.pilot_spacing == 6
    assert SEFDM_2K7.points_per_block > BENCH.num_data_carriers


@pytest.mark.parametrize("modulation", BITS_PER_SYMBOL)
def test_experimental_constellations_have_exact_soft_round_trip(modulation: str) -> None:
    rng = np.random.default_rng(0x213)
    width = BITS_PER_SYMBOL[modulation]
    bits = rng.integers(0, 2, 128 * width, dtype=np.int8)
    llr = demap_llr(map_bits(bits, modulation), modulation, 0.01)
    assert np.array_equal((llr > 0).astype(np.int8), bits)


def test_new_mcs_values_do_not_expand_the_verified_ofdm_picker() -> None:
    assert [entry.index for entry in SC_MCS_TABLE] == list(range(21))
    with pytest.raises(OfdmConfigError, match="unknown MCS index 4"):
        mcs(4)
    assert sc_mcs(4).modulation == "qam256"
    assert sc_mcs(5).modulation == "apsk16"
    assert sc_mcs(6).modulation == "apsk32"
    assert sc_mcs(7).modulation == "psk8"
    assert sc_mcs(10).modulation == "qam128"
    assert sc_mcs(11).modulation == "apsk128"
    assert sc_mcs(15).modulation == "qam1024"
    assert sc_mcs(16).modulation == "gqam16"
    assert sc_mcs(19).modulation == "gqam1024"
    assert sc_mcs(20).modulation == "pas64"


def test_pas64_distribution_matcher_is_reversible_and_nonuniform() -> None:
    rng = np.random.default_rng(0xA564)
    bits = rng.integers(0, 2, PAS64_INPUT_BITS * 4, dtype=np.int8)
    symbols = map_bits(bits, "pas64")
    recovered = (demap_llr(symbols, "pas64", 0.01) > 0).astype(np.int8)
    assert np.array_equal(recovered, bits)
    assert len(symbols) == 64
    assert np.mean(np.abs(symbols) ** 2) == pytest.approx(1.0)


def test_pas64_full_frame_round_trip_keeps_crc_and_reports_gmi() -> None:
    payload = np.random.default_rng(2064).integers(
        0, 256, 192, dtype=np.uint8
    ).tobytes()
    header = PhyHeader(
        OfdmFrameType.DATA, 2064, block_count=1, mcs=20,
        payload_len=len(payload), subblock_count=1,
    )
    codec = ExperimentalBurstCodec()
    decoded = codec.decode_burst(
        SC_FTN_2K7,
        codec.build_burst(SC_FTN_2K7, header, blocks=[SubBlock(0, payload)]),
    )
    assert decoded.ok
    assert decoded.payload == payload
    assert decoded.metrics.gmi_bits_per_symbol is not None
    assert decoded.metrics.gmi_bits_per_symbol <= 43 / 8


def test_capacity_report_is_reproducible_and_exports_raw_rows(tmp_path) -> None:
    first = benchmark_point(
        SC_FDE_FTN_2K7, 2, rf_snr_db=30.0,
        payload_bytes=64, repeats=1, seed=233,
    )
    second = benchmark_point(
        SC_FDE_FTN_2K7, 2, rf_snr_db=30.0,
        payload_bytes=64, repeats=1, seed=233,
    )
    assert first == second
    assert first.frames_ok == 1
    assert first.median_gmi_bits_per_symbol is not None
    json_path, csv_path = write_report([first], tmp_path / "capacity")
    assert json_path.exists() and csv_path.exists()
    assert "decoded_payload_bps" in csv_path.read_text(encoding="utf-8-sig")

    experimental = PhyHeader(
        OfdmFrameType.DATA, 0x213, 0, 1, 4, 16,
        fec=FecProfile.FEC_7_8,
    ).encode()
    with pytest.raises(OfdmFrameError, match="unknown MCS index 4"):
        PhyHeader.decode(experimental)
    assert PhyHeader.decode(experimental, mcs_lookup=sc_mcs).mcs == 4


def test_every_experimental_family_has_the_width_ladder() -> None:
    for family in ("sc_hs", "sc_ftn", "sc_fde_ftn", "sefdm"):
        names = FAMILY_PROFILE_LADDERS[family]
        assert len(names) == 5
        widths = [WAVE_PROFILES[name].occupied_bandwidth for name in names]
        assert widths == sorted(widths)
        assert widths[0] == pytest.approx(1200.0, abs=60.0)
        assert widths[-1] == pytest.approx(18750.0, abs=60.0)
    assert SC_FDE_FTN_2K7.equalizer_mode == "sc_fde"
    assert SC_FDE_FTN_2K7.noise_whitening


@pytest.mark.parametrize(
    "name", ("SC_HS_1K2", "SC_FTN_5K", "SC_FDE_FTN_10K", "SC_FDE_FTN_20K")
)
def test_fractional_clock_single_carrier_profiles_round_trip(name: str) -> None:
    profile = WAVE_PROFILES[name]
    payload = bytes(range(32))
    header = PhyHeader(
        OfdmFrameType.DATA, 0x233, 0, 1, 2, len(payload),
        fec=FecProfile.FEC_1_2,
    )
    codec = ExperimentalBurstCodec()
    waveform = codec.build_burst(
        profile, header, blocks=[SubBlock(0, payload)]
    )
    decoded = codec.decode_burst(profile, waveform)
    assert decoded.blocks == {0: payload}
    assert decoded.metrics.equalizer_mode == profile.equalizer_mode
    assert decoded.metrics.equalizer_iterations == profile.equalizer_iterations


@pytest.mark.parametrize("profile", PROFILES, ids=lambda profile: profile.name)
def test_every_experimental_phy_round_trips_a_real_frame(profile) -> None:
    payload = bytes(range(128))
    header = PhyHeader(
        OfdmFrameType.DATA, 0x213, 0, 1, 2, len(payload),
        fec=FecProfile.FEC_1_2,
    )
    codec = ExperimentalBurstCodec()
    waveform = codec.build_burst(profile, header, payload)
    decoded = codec.decode_burst(profile, waveform)
    assert decoded.payload == payload
    assert decoded.metrics.frame_ok
    assert len(waveform) == codec.burst_samples(profile, header)


@pytest.mark.parametrize(
    ("profile", "snr_db"),
    ((SC_HS_2K7, 20.0), (SC_FTN_2K7, 30.0), (SEFDM_2K7, 30.0)),
    ids=("sc-hs", "sc-ftn", "sefdm"),
)
def test_each_new_phy_decodes_in_its_measured_awgn_region(profile, snr_db) -> None:
    payload = bytes(range(128))
    header = PhyHeader(OfdmFrameType.DATA, 0x213, 0, 1, 2, len(payload))
    codec = ExperimentalBurstCodec()
    clean = codec.build_burst(profile, header, payload)
    spec = ChannelSpec(
        snr_db=snr_db, delay=311, trailing=profile.symbol_samples,
    )
    aired = Channel(profile, spec, seed=7)(clean)
    assert codec.decode_burst(profile, aired).payload == payload


def test_sefdm_apsk32_survives_the_full_realistic_channel() -> None:
    results = [
        waveform_bench.run_burst(
            SEFDM_2K7, 6, payload_bytes=512, snr_db=35.0,
            seed=0x213 + index, fec="7/8",
        )
        for index in range(8)
    ]
    assert all(result.passed for result in results)
    assert all(result.metrics.error is None for result in results)


@pytest.mark.parametrize(
    ("profile", "snr_db"),
    ((SC_HS_2K7, 30.0), (SC_FTN_2K7, 32.0), (SEFDM_2K7, 35.0)),
    ids=("sc-hs", "sc-ftn", "sefdm"),
)
def test_proven_selective_repeat_link_runs_over_each_codec(profile, snr_db) -> None:
    spec = ChannelSpec(snr_db=snr_db, delay=200, trailing=profile.symbol_samples)
    near, far = simulated_pair(profile, spec, seed=13)
    codec = ExperimentalBurstCodec()
    sender = OfdmLink(profile, near, mcs_index=1, codec=codec)
    receiver = OfdmLink(profile, far, mcs_index=1, codec=codec)
    payload = bytes(range(64))
    received: dict[str, bytes | None] = {}
    listener = threading.Thread(
        target=lambda: received.setdefault("payload", receiver.receive_message(0x213)),
        daemon=True,
    )
    listener.start()
    assert sender.send_message(0x213, payload)
    listener.join(15.0)
    assert received.get("payload") == payload


def test_waveform_selection_persists_and_builds_the_requested_backend(tmp_path) -> None:
    path = tmp_path / "g2.json"
    StationConfig(g2_waveform="sc_fde_ftn", g2_bandwidth="10K", g2_mcs=11).save(path)
    loaded = StationConfig.load(path)
    assert (loaded.g2_waveform, loaded.g2_bandwidth, loaded.g2_mcs) == (
        "sc_fde_ftn", "10K", 11
    )
    backend = make_backend(
        "ofdm_vhf", g2_waveform=loaded.g2_waveform,
        g2_bandwidth=loaded.g2_bandwidth, g2_mcs=loaded.g2_mcs
    )
    assert backend.profile is WAVE_PROFILES["SC_FDE_FTN_10K"]
    assert backend.mcs_index == 11
    assert isinstance(backend.codec, ExperimentalBurstCodec)


def test_bad_waveform_config_falls_back_without_disabling_ofdm(tmp_path) -> None:
    path = tmp_path / "g2.json"
    path.write_text('{"g2_waveform":"telepathy","g2_mcs":99}', encoding="utf-8")
    loaded = StationConfig.load(path)
    assert loaded.g2_waveform == "ofdm"
    assert loaded.g2_mcs == 20
