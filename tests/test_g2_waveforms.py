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
from guardian.ofdm.framing import OfdmFrameError, OfdmFrameType, PhyHeader
from guardian.ofdm.link import OfdmLink, simulated_pair
from guardian.payload import make_backend
from guardian.waveforms.config import SC_FTN_2K7, SC_HS_2K7, SEFDM_2K7
from guardian.waveforms.constellation import (BITS_PER_SYMBOL, demap_llr,
                                              map_bits)
from guardian.waveforms.framing import ExperimentalBurstCodec


PROFILES = (SC_HS_2K7, SC_FTN_2K7, SEFDM_2K7)


def test_profiles_are_independent_and_fit_the_voice_channel() -> None:
    assert len({profile.family for profile in PROFILES}) == 3
    for profile in PROFILES:
        low, high = profile.occupied_band
        assert 250.0 <= low < high <= 3_200.0
        assert profile.occupied_bandwidth <= 2_700.0
        assert profile.sample_rate == 48_000
    assert SC_HS_2K7.ftn_tau == pytest.approx(1.0)
    assert SC_FTN_2K7.ftn_tau == pytest.approx(0.9)
    assert SC_FTN_2K7.symbol_rate > SC_HS_2K7.symbol_rate
    assert SEFDM_2K7.sefdm_alpha < 1.0
    assert SEFDM_2K7.points_per_block > BENCH.num_data_carriers


@pytest.mark.parametrize("modulation", BITS_PER_SYMBOL)
def test_experimental_constellations_have_exact_soft_round_trip(modulation: str) -> None:
    rng = np.random.default_rng(0x213)
    width = BITS_PER_SYMBOL[modulation]
    bits = rng.integers(0, 2, 128 * width, dtype=np.int8)
    llr = demap_llr(map_bits(bits, modulation), modulation, 0.01)
    assert np.array_equal((llr > 0).astype(np.int8), bits)


def test_new_mcs_values_do_not_expand_the_verified_ofdm_picker() -> None:
    assert [entry.index for entry in SC_MCS_TABLE] == list(range(7))
    with pytest.raises(OfdmConfigError, match="unknown MCS index 4"):
        mcs(4)
    assert sc_mcs(4).modulation == "qam256"
    assert sc_mcs(5).modulation == "apsk16"
    assert sc_mcs(6).modulation == "apsk32"

    experimental = PhyHeader(
        OfdmFrameType.DATA, 0x213, 0, 1, 4, 16,
        fec=FecProfile.FEC_7_8,
    ).encode()
    with pytest.raises(OfdmFrameError, match="unknown MCS index 4"):
        PhyHeader.decode(experimental)
    assert PhyHeader.decode(experimental, mcs_lookup=sc_mcs).mcs == 4


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
    StationConfig(g2_waveform="sc_ftn", g2_mcs=6).save(path)
    loaded = StationConfig.load(path)
    assert (loaded.g2_waveform, loaded.g2_mcs) == ("sc_ftn", 6)
    backend = make_backend(
        "ofdm_vhf", g2_waveform=loaded.g2_waveform, g2_mcs=loaded.g2_mcs
    )
    assert backend.profile is SC_FTN_2K7
    assert backend.mcs_index == 6
    assert isinstance(backend.codec, ExperimentalBurstCodec)


def test_bad_waveform_config_falls_back_without_disabling_ofdm(tmp_path) -> None:
    path = tmp_path / "g2.json"
    path.write_text('{"g2_waveform":"telepathy","g2_mcs":99}', encoding="utf-8")
    loaded = StationConfig.load(path)
    assert loaded.g2_waveform == "ofdm"
    assert loaded.g2_mcs == 6
