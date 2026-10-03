"""Field-derived adaptation tests; successful delivery is independent of SNR."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from guardian.ofdm.coding import FecProfile, fec_spec
from guardian.ofdm.channel import Channel, ChannelSpec
from guardian.ofdm.config import sc_mcs
from guardian.ofdm.framing import AckBitmap, OfdmFrameType, PhyHeader, SubBlock
from guardian.payload.ofdm_vhf import OfdmVhfBackend


def feedback(link, *, good=True, snr=12.5, evm=.24, retry=False, size=None):
    profile = link.controller.profile
    size = size or min(profile.burst_bytes, 8192)
    sent = max(1, size // profile.arq_block_bytes)
    received = sent if good else sent - 1
    link._report_delivery_feedback(
        sent=sent, received=received,
        retransmitted_bytes=size if retry else 0,
        unique_bytes=received * profile.arq_block_bytes,
        elapsed_seconds=10, mcs_index=profile.mcs_index,
        remote_snr_db=snr, remote_evm_rms=evm,
    )


def field_link():
    backend = OfdmVhfBackend()
    link = backend._make_link(None)
    link.mcs_index = link.controller.mcs_index = 16
    link.controller.current_burst_bytes = 16384
    link.controller.capacity_probe_next = "fec"
    return link


def test_clean_field_link_can_probe_fec_despite_snr_gate():
    link = field_link()
    assert not link.controller._fec_upgrade_allowed(FecProfile.LDPC_2_3)
    feedback(link)
    assert link.controller.fec_probe_from == FecProfile.LDPC_1_2
    assert link.controller.profile.fec == FecProfile.LDPC_2_3
    assert link.controller.profile.burst_bytes == 2048
    feedback(link)
    assert link.controller.fec_probe_from is None
    assert link.controller.profile.fec == FecProfile.LDPC_2_3
    # This same successful probe already earns the next MCS trial.
    assert link.controller.mcs_probe_from == 16
    assert link.mcs_index == 8


def test_failed_fec_probe_rolls_back_without_punishing_mcs():
    link = field_link()
    feedback(link)
    feedback(link, good=False)
    assert link.mcs_index == 16
    assert link.controller.profile.fec == FecProfile.LDPC_1_2
    assert link.controller.profile.burst_bytes == 16384
    assert link.controller.profile_upgrade_cooldown == 0
    feedback(link, retry=True, snr=30, evm=.01)
    assert link.controller.profile_upgrade_cooldown == 0
    assert link.controller.current_fec == FecProfile.LDPC_1_2


def test_failed_mcs_probe_preserves_the_proven_fec():
    link = field_link()
    link.controller.current_fec = FecProfile.LDPC_2_3
    link.controller.capacity_probe_next = "mcs"
    feedback(link)
    assert link.mcs_index == 8
    assert link.controller.mcs_probe_from == 16
    assert link.controller.profile.burst_bytes == 2048
    feedback(link, good=False)
    assert link.mcs_index == link.controller.mcs_index == 16
    assert link.controller.current_fec == FecProfile.LDPC_2_3
    assert link.controller.mcs_upgrade_cooldown == 0
    feedback(link, retry=True, snr=30, evm=.01)
    assert link.controller.mcs_upgrade_cooldown == 0


@pytest.mark.parametrize("snr,evm", [(None, None), (0, .3), (12.5, None)])
def test_unknown_or_invalid_quality_does_not_start_exploration(snr, evm):
    link = field_link()
    for _ in range(10):
        feedback(link, snr=snr, evm=evm)
    assert link.controller.fec_probe_from is None
    assert link.controller.mcs_probe_from is None
    assert link.mcs_index == 16


def test_small_clean_messages_do_not_earn_ungated_capacity_probe():
    link = field_link()
    for _ in range(12):
        feedback(link, size=256)
    assert link.controller.profile.fec == FecProfile.LDPC_1_2
    assert link.mcs_index == 16


def test_supported_density_ladder_has_no_lab_mcs17_ceiling():
    link = OfdmVhfBackend()._make_link(None)
    ladder = link._mcs_ladder()
    assert [x.index for x in ladder] == [1, 7, 16, 8, 17, 10, 18, 13, 19]
    assert [x.bits_per_symbol for x in ladder] == list(range(2, 11))
    for _ in range(50):
        before = link.controller.profile
        feedback(link, snr=45, evm=.005)
        after = link.controller.profile
        if before.mcs_index != after.mcs_index or before.fec != after.fec:
            assert link.controller.capacity_probe_profile is not None
            assert after.burst_bytes <= 2048
    assert link.mcs_index == 19


def test_laboratory_quality_can_reach_old_operating_point_by_delivery():
    link = OfdmVhfBackend()._make_link(None)
    proven = False
    for _ in range(60):
        profile = link.controller.profile
        # The lab qualified 64-GQAM 4/5 in the weaker direction. Reject trials
        # above that capacity, rather than pretending a metric guarantees CRCs.
        good = (sc_mcs(profile.mcs_index).bits_per_symbol <= 6
                and fec_spec(profile.fec).rate <= .8)
        feedback(link, good=good, snr=18.09, evm=.125)
        proven |= (profile.mcs_index == 17 and profile.fec == FecProfile.LDPC_4_5 and good)
    assert proven


def test_real_arq_bounds_probes_and_retains_delivered_blocks(monkeypatch):
    backend = OfdmVhfBackend()
    pipe = SimpleNamespace(send=lambda wave: None)
    link = backend._make_link(pipe)
    held = {}
    transmissions = []
    active = []
    failed_probe = []

    def build(header, payload=b"", *, blocks=None):
        if header.frame_type is OfdmFrameType.DATA:
            active[:] = [header, blocks]
            probing = (link.controller.mcs_probe_from is not None
                       or link.controller.fec_probe_from is not None)
            if probing:
                assert header.payload_len <= 2048
            transmissions.append((header, probing))
        return np.zeros(1)

    def ack(*args, **kwargs):
        header, blocks = active
        assert not held.keys() & {x.sequence for x in blocks}
        # Fail exactly one new FEC trial block; all held blocks survive retry.
        fail = link.controller.fec_probe_from is not None and not failed_probe
        if fail:
            failed_probe.append(blocks[-1].sequence)
        for block in blocks[:-1] if fail else blocks:
            held[block.sequence] = block.payload
        return AckBitmap(header.block_count, frozenset(held), 12.5, .24)

    monkeypatch.setattr(link, "_build_burst", build)
    monkeypatch.setattr(link, "_await_bitmap", ack)
    payload = bytes(range(256)) * 512
    assert link.send_message(705, payload)
    assert failed_probe
    assert b"".join(held[x] for x in sorted(held)) == payload
    assert link.status.retransmitted_bytes == 256
    assert any(h.retransmission for h, _ in transmissions)
    assert any(probe for _, probe in transmissions)


def test_failed_candidate_waits_for_better_data_not_a_fixed_frame_count():
    link = field_link()
    feedback(link)  # FEC 2/3 trial
    feedback(link, good=False)
    assert link.controller.current_fec == FecProfile.LDPC_1_2
    # Isolate this FEC decision from MCS exploration for this regression.
    link._maximum_mcs_index = 16
    link._mcs_ladder = lambda: [sc_mcs(16)]
    for _ in range(20):
        feedback(link)
        assert link.controller.fec_probe_from is None
        assert link.controller.current_fec == FecProfile.LDPC_1_2
    feedback(link, snr=14.1, evm=.20)
    assert link.controller.fec_probe_from == FecProfile.LDPC_1_2
    assert link.controller.current_fec == FecProfile.LDPC_2_3


def test_directed_peer_histories_do_not_share_a_capacity_limit():
    backend = OfdmVhfBackend()
    tx = backend._controller_for_peer("OK2IPW", direction="send")
    rx = backend._controller_for_peer("OK2IPW", direction="receive")
    other = backend._controller_for_peer("OK2MTV", direction="send")
    assert tx is not rx and tx is not other
    link = backend._make_link(None, tx)
    for _ in range(25):
        feedback(link, snr=40, evm=.01)
    assert tx.mcs_index == 19
    assert rx.mcs_index == other.mcs_index == 1
    assert not rx.capacity_rejections and not other.capacity_rejections


def test_repeated_loss_never_returns_to_a_stale_faster_fallback():
    link = field_link()
    for _ in range(6):
        feedback(link)
    previous = sc_mcs(link.mcs_index).bits_per_symbol * fec_spec(link.controller.current_fec).rate
    for _ in range(12):
        feedback(link, good=False)
        current = sc_mcs(link.mcs_index).bits_per_symbol * fec_spec(link.controller.current_fec).rate
        assert current <= previous
        previous = current


def test_higher_mcs_can_trade_some_code_rate_for_more_capacity():
    link = field_link()
    link.controller.current_fec = FecProfile.LDPC_9_10
    link.controller.capacity_probe_next = "mcs"
    feedback(link)  # 32-QAM / 9/10
    assert link.mcs_index == 8
    feedback(link, good=False)
    assert link.mcs_index == 16
    feedback(link)  # Try the higher constellation with stronger FEC.
    assert link.mcs_index == 8
    assert link.controller.current_fec == FecProfile.LDPC_7_8
    assert link.controller.capacity_probe_profile.mcs_index == 16
    assert link.controller.capacity_probe_profile.fec == FecProfile.LDPC_9_10
    feedback(link, good=False)
    assert link.mcs_index == 16
    assert link.controller.current_fec == FecProfile.LDPC_9_10


def test_fixed_fec_is_preserved_during_capacity_trials_and_fallback():
    link = field_link()
    controller = link.controller
    controller.config = replace(controller.config, adaptive_fec=False,
                                fixed_fec=FecProfile.LDPC_4_5)
    controller.current_fec = FecProfile.LDPC_4_5
    for _ in range(10):
        feedback(link)
        assert controller.current_fec == FecProfile.LDPC_4_5
        feedback(link, good=False)
        assert controller.current_fec == FecProfile.LDPC_4_5
    for _ in range(10):
        feedback(link, good=False)
    assert link.mcs_index == 1
    assert controller.profile.fec == FecProfile.LDPC_4_5


@pytest.mark.parametrize("width", ["1K2", "2K7", "4K5", "5K", "10K", "20K"])
def test_every_bandwidth_and_radio_can_learn_capacity(width):
    for radio in ("", "guardian_k5"):
        link = OfdmVhfBackend(g2_bandwidth=width, radio_backend=radio)._make_link(None)
        assert link.controller.config.rapid_acquisition
        for _ in range(20):
            feedback(link, snr=42, evm=.005)
        assert link.mcs_index == 19
        assert link.controller.current_fec == FecProfile.LDPC_9_10


def test_airtime_limited_clean_window_does_not_require_2048_bytes_to_grow():
    link = OfdmVhfBackend(g2_bandwidth="1K2", radio_backend="guardian_k5")._make_link(None)
    link.mcs_index = link.controller.mcs_index = 7
    feedback(link, size=768, snr=9.0, evm=.35)
    assert link.controller.capacity_probe_profile is not None


@pytest.mark.parametrize("mcs", [17, 10, 18, 13, 19])
def test_capacity_probe_uses_real_codec_at_every_new_density(mcs):
    backend = OfdmVhfBackend()
    payload = np.random.default_rng(mcs).integers(0, 256, 2048, dtype=np.uint8).tobytes()
    blocks = [SubBlock(i, payload[i * 256:(i + 1) * 256]) for i in range(8)]
    header = PhyHeader(OfdmFrameType.DATA, 705, block_count=8, mcs=mcs,
                       fec=FecProfile.LDPC_4_5, version=4,
                       payload_len=len(payload), subblock_count=8)
    wave = backend.codec.build_burst(backend.profile, header, blocks=blocks)
    result = backend.codec.decode_burst(backend.profile, wave)
    assert result.blocks == {x.sequence: x.payload for x in blocks}


def test_adaptive_arq_with_real_dsp_and_a_degrading_noisy_path(monkeypatch):
    backend = OfdmVhfBackend()
    held, cache = {}, {}
    sent_profiles = []
    latest = [None]

    class Radio:
        def set_transmit_context(self, headers, control=False):
            self.header = headers[-1]

        def send(self, samples):
            if self.header.frame_type is not OfdmFrameType.DATA:
                return
            sent_profiles.append((self.header.mcs, self.header.fec))
            channel = Channel(backend.profile, ChannelSpec(
                snr_db=27.0 if len(sent_profiles) < 8 else 18.0,
                freq_offset_hz=1.0, ppm=7.0, trailing=1000,
            ), seed=len(sent_profiles))
            decoded = backend.codec.decode_burst(backend.profile, channel(samples), soft_cache=cache)
            held.update(decoded.blocks)
            latest[0] = decoded.metrics

    link = backend._make_link(Radio())

    def ack(msg_id, burst_id, total, **kwargs):
        metrics = latest[0]
        return AckBitmap(total, frozenset(held), metrics.residual_snr_db, metrics.evm_rms)

    monkeypatch.setattr(link, "_await_bitmap", ack)
    payload = np.random.default_rng(705).integers(0, 256, 32768, dtype=np.uint8).tobytes()
    assert link.send_message(705, payload)
    assert b"".join(held[k] for k in sorted(held)) == payload
    assert any(fec_spec(fec).rate > .5 for _, fec in sent_profiles)
    assert len(set(sent_profiles)) >= 4
