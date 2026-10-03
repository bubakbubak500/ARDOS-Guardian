"""Regression coverage for the 1.1.14 SC receive field report."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from guardian.ofdm.coding import FecProfile, fec_spec
from guardian.ofdm.adaptation import AdaptationConfig, LinkAdaptationController
from guardian.ofdm.framing import (
    CAPACITY_FRAME_VERSION, DEFER_ACK_FLAG, AckBitmap, DecodedBurst,
    OfdmFrameType, PhyHeader, split_blocks,
)
from guardian.ofdm.link import OfdmLink, RxBurstState
from guardian.ofdm.metrics import LinkMetrics
from guardian.payload.ofdm_vhf import OfdmVhfBackend
from guardian.qt.transfer_progress import transfer_state
from guardian.session import LoopbackBus, Message, Orchestrator, SessionState
from guardian.session.orchestrator import (
    session_transfer_hard_timeout_for, session_transfer_timeout_for,
)
from guardian.waveforms.config import SC_FTN_2K7


class ScriptedCodec:
    """Supply decoded RF frames; retain the production ARQ/backend/UI path."""
    def __init__(self, batches=()):
        self.batches = list(batches)
        self.replies = []

    def decode_many(self, profile, samples, **kwargs):
        return self.batches[int(samples[0])]

    def build_burst(self, profile, header, payload=b"", **kwargs):
        self.replies.append((header, payload))
        return np.zeros(1)

    def burst_duration(self, profile, header, block_lengths=None):
        return 0.01


class ScriptedPipe:
    def __init__(self, count, advance=lambda: None):
        self.count = count
        self.index = 0
        self.advance = advance

    def receive(self, timeout):
        if self.index >= self.count:
            return None
        self.advance()
        samples = np.array([self.index], dtype=float)
        self.index += 1
        return samples

    def send(self, samples):
        pass

    def start(self):
        pass

    def stop(self):
        pass


def data_frame(blocks, total, *, mcs=1, fec=FecProfile.LDPC_1_2,
               msg_id=115, defer=False, failed=()):
    return DecodedBurst(
        header=PhyHeader(
            OfdmFrameType.DATA, msg_id, block_seq=min(blocks), block_count=total,
            mcs=mcs, fec=fec, version=CAPACITY_FRAME_VERSION,
            payload_len=sum(map(len, blocks.values())), subblock_count=len(blocks),
            flags=DEFER_ACK_FLAG if defer else 0,
        ),
        blocks={k: v for k, v in blocks.items() if k not in failed},
        failed_blocks=set(failed), block_order=tuple(blocks),
        block_lengths={k: len(v) for k, v in blocks.items()},
        metrics=LinkMetrics(frame_ok=not failed),
    )


@pytest.mark.parametrize("block_size", [256, 512, 1024, 16384])
def test_rx_size_uses_remote_arq_stride_and_short_tail(block_size):
    state = RxBurstState(115, 5)
    # A short final block received out of order cannot define the stride.
    assert state.observe_lengths({4: 17}) == 0
    assert state.observe_lengths({2: block_size}) == 4 * block_size + 17
    assert state.observe_lengths({2: block_size}) == 4 * block_size + 17
    state = RxBurstState(115, 5)
    assert state.observe_lengths({0: block_size}) == 5 * block_size
    assert state.observe_lengths({4: 17}) == 4 * block_size + 17
    assert RxBurstState(115, 1).observe_lengths({0: 17}) == 17


def test_800kb_receive_progress_and_deadline_match_ack_bitmap(monkeypatch):
    payload = bytes(range(256)) * 3130 + b"short final block"
    blocks = dict(enumerate(split_blocks(payload, 256)))
    total = len(blocks)
    assert total == 3131  # the field-report block count
    batches = []
    for start in range(0, total, 50):
        batch = []
        for offset in range(start, min(start + 50, total), 25):
            members = {i: blocks[i] for i in range(offset, min(offset + 25, total))}
            batch.append(data_frame(members, total, mcs=1 if start == 0 else 8,
                                    fec=FecProfile.LDPC_1_2 if start == 0 else FecProfile.LDPC_3_4,
                                    defer=offset + 25 < min(start + 50, total)))
        batches.append(batch)
    clock = [100.0]
    monkeypatch.setattr("guardian.ofdm.link.time.monotonic", lambda: clock[0])
    backend = OfdmVhfBackend()
    station = Orchestrator("OK7PS", LoopbackBus().endpoint("rx"), payload=backend)
    message = Message(115, "OK2IPW", "OK7PS", "OK7PS", direction="in",
                      payload_transport="ofdm_vhf")
    station.sessions[115] = message
    station.tick(clock[0])
    station._enter(message, SessionState.RECEIVING)
    original_cap = session_transfer_hard_timeout_for(message)
    snapshots = []
    publish = backend._publish

    def observe(status):
        publish(status)
        snapshots.append(replace(status))

    def advance():
        clock[0] += 25.0
        station.tick(clock[0], control_available=False)
        assert message.state is SessionState.RECEIVING, message.error

    codec = ScriptedCodec(batches)
    backend.codec = codec
    monkeypatch.setattr(backend, "_publish", observe)
    monkeypatch.setattr(backend, "_make_pipe", lambda: ScriptedPipe(len(batches), advance))
    monkeypatch.setattr(OfdmLink, "_linger_bitmap", lambda self, state: None)
    backend._begin_transfer(message)
    results = []
    backend._receive(message, results.append)
    assert results == [True]
    assert message.payload_bytes == payload
    assert clock[0] - 100 > original_cap
    assert message.payload_wire_size == total * 256
    assert message.payload_progress_bytes == len(payload)
    ack_counts = [len(AckBitmap.decode(raw).received) for header, raw in codec.replies]
    assert ack_counts[-1] == total
    for status in snapshots:
        if not status.rx_bytes or status.state == "idle":
            continue
        view = transfer_state(SimpleNamespace(vara=None), True, status)
        assert view.fraction == pytest.approx(status.rx_bytes / len(payload), abs=0.0004)
        assert status.percent == int(100 * status.rx_bytes / status.total_bytes)
        assert status.arq_block_bytes == 256
        assert status.total_bytes_exact == (status.total_bytes == len(payload))
        if status.rx_bytes > 50 * 256:
            assert view.mcs == 8
            assert view.fec == fec_spec(FecProfile.LDPC_3_4).label
    # The corrected size must not disable the independent safety cap.
    station.tick(message.transfer_started_at + session_transfer_hard_timeout_for(message) + 1,
                 control_available=False)
    assert message.state is SessionState.FAILED
    assert "absolute safety limit" in message.error


def test_rx_duplicates_failed_blocks_and_control_do_not_inflate_or_reset_status():
    first = data_frame({0: b"a" * 256, 1: b"b" * 256}, 4, mcs=8,
                       fec=FecProfile.LDPC_3_4, failed=(1,))
    poll = DecodedBurst(header=PhyHeader(OfdmFrameType.POLL, 115, block_count=4),
                        metrics=LinkMetrics(frame_ok=True))
    foreign = data_frame({0: b"z" * 1024}, 20, msg_id=999, mcs=17)
    recovery = data_frame({1: b"b" * 256}, 4, mcs=0, fec=FecProfile.FEC_1_2)
    codec = ScriptedCodec([[first], [first], [poll], [foreign], [recovery]])
    snapshots = []
    link = OfdmLink(SC_FTN_2K7, ScriptedPipe(5), codec=codec,
                    on_status=lambda s: snapshots.append(replace(s)))
    assert link.receive_message(115) is None
    assert link.status.rx_bytes == 512
    assert link.status.total_bytes == 1024
    assert link.status.mcs == 0
    assert link.status.fec == fec_spec(FecProfile.FEC_1_2).label
    middle = [s for s in snapshots if s.rx_bytes == 256]
    assert middle
    assert all(s.mcs == 8 and s.total_bytes == 1024 for s in middle)
    assert all(s.burst_bytes == 512 for s in middle)


@pytest.mark.parametrize("train", [False, True])
def test_tx_shows_actual_rescue_profile_through_control_frames(train):
    snapshots = []
    link = OfdmLink(SC_FTN_2K7, ScriptedPipe(0), codec=ScriptedCodec(), mcs_index=8,
                    on_status=lambda s: snapshots.append(replace(s)))
    for mcs, fec in [(8, FecProfile.LDPC_3_4), (0, FecProfile.FEC_1_2)]:
        header = PhyHeader(OfdmFrameType.DATA, 115, mcs=mcs, fec=fec, payload_len=512)
        if train:
            link._transmit_train(np.zeros(1), [header])
        else:
            link._transmit(np.zeros(1), header=header)
        link._transmit(np.zeros(1), header=PhyHeader(OfdmFrameType.POLL, 115), control=True)
        link._publish("waiting_ack")
        assert all(s.mcs == mcs and s.fec == fec_spec(fec).label for s in snapshots[-3:])
        assert link.status.burst_bytes == 512


def test_sc_stall_still_expires_without_new_bytes():
    backend = OfdmVhfBackend()
    station = Orchestrator("OK7PS", LoopbackBus().endpoint("rx"), payload=backend)
    msg = Message(115, "OK2IPW", "OK7PS", "OK7PS", direction="in", payload_transport="ofdm_vhf")
    station.sessions[115] = msg
    station._enter(msg, SessionState.RECEIVING)
    backend._begin_transfer(msg)
    status = replace(backend.status, total_bytes=1024, rx_bytes=256)
    backend._publish(status)
    station.tick(10, control_available=False)
    # Repeated status/ACKs without additional unique data do not reset the timer.
    backend._publish(status)
    station.tick(11 + session_transfer_timeout_for(msg), control_available=False)
    assert msg.state is SessionState.FAILED
    assert "no progress" in msg.error


def test_panel_refreshes_live_mcs_fec_without_losing_progress():
    from PySide6.QtWidgets import QApplication
    from guardian.qt.transfer_progress import TransferPanel
    from guardian.ofdm.metrics import OfdmStatus

    app = QApplication.instance() or QApplication([])
    panel = TransferPanel()
    for mcs, fec in [(1, FecProfile.LDPC_1_2), (8, FecProfile.LDPC_3_4), (0, FecProfile.FEC_1_2)]:
        status = OfdmStatus(state="receiving", direction="receive", profile="SC_FTN_2K7",
                            rx_bytes=400_000, total_bytes=800_000, mcs=mcs,
                            fec=fec_spec(fec).label)
        panel.apply(transfer_state(SimpleNamespace(vara=None), True, status))
        assert panel.bar.fraction == 0.5
        assert f"MCS{mcs}" in panel.detail.text()
        assert f"FEC {fec_spec(fec).label}" in panel.detail.text()
        assert "SC_FTN_2K7" in panel.detail.text()
    panel.close()


def test_receive_panel_names_wire_bytes_and_marks_manifest_estimate(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from guardian import i18n
    from guardian.ofdm.metrics import OfdmStatus
    from guardian.qt.transfer_progress import TransferPanel

    monkeypatch.setattr(i18n, "_language", i18n.Language.ENGLISH)
    app = QApplication.instance() or QApplication([])
    panel = TransferPanel()
    status = OfdmStatus(
        state="receiving", direction="receive", profile="SC_FTN_2K7",
        rx_bytes=111_616, total_bytes=832 * 256,
        data_airtime_seconds=190.0, keyed_seconds=4.0,
        elapsed_seconds=320.0, est_bitrate_bps=4_162.0,
    )
    view = transfer_state(SimpleNamespace(vara=None), True, status)
    panel.apply(view)
    assert round(view.fraction * 100) == 52
    assert "111616" in panel.detail.text()
    assert "212992" in panel.detail.text()
    assert "about" in panel.detail.text()
    assert "modeled channel" in panel.detail.text()
    assert "data airtime" in panel.detail.text()
    assert "ACK TX" in panel.detail.text()

    i18n.set_language("cs")
    panel.apply(view)
    assert "přibližně 212992 B" in panel.detail.text()
    assert "modelovaný kanál" in panel.detail.text()

    status.total_bytes = 212_917
    status.total_bytes_exact = True
    panel.apply(transfer_state(SimpleNamespace(vara=None), True, status))
    assert "212917" in panel.detail.text()
    assert "přibližně" not in panel.detail.text()
    panel.close()


def test_long_clean_superframes_do_not_downgrade_on_snr_alone():
    controller = LinkAdaptationController(
        AdaptationConfig(
            modern_ldpc=True, rapid_acquisition=True,
            initial_fec=FecProfile.LDPC_1_2,
        ),
        mcs_index=16,
    )
    link = OfdmLink(
        SC_FTN_2K7, ScriptedPipe(0), mcs_index=17,
        adaptive_mcs=True, superframe=True, controller=controller,
    )
    assert link.mcs_index == 16
    for _ in range(3):
        link._report_mcs_feedback(34, 34, 11.0, 0.28)
    assert link.mcs_index == 16

    link._report_mcs_feedback(34, 31, 11.0, 0.28)
    assert link.mcs_index == 7
    assert controller.mcs_upgrade_cooldown == 0
    for _ in range(2):
        link._report_mcs_feedback(34, 34, 18.0, 0.15)
        assert link.mcs_index == 7
    link._report_mcs_feedback(34, 34, 18.0, 0.15)
    assert link.mcs_index == 16


def test_sender_distinguishes_remote_data_quality_from_local_ack(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from guardian.ofdm.metrics import OfdmStatus
    from guardian.qt.transfer_progress import TransferPanel

    app = QApplication.instance() or QApplication([])
    panel = TransferPanel()
    status = OfdmStatus(state="waiting_ack", direction="send",
                        profile="SC_FTN_2K7", snr_db=19.8, evm_rms=.10,
                        remote_snr_db=12.5, remote_evm_rms=.24)
    panel.apply(transfer_state(SimpleNamespace(vara=None), True, status))
    assert "DATA SNR 12.5 dB" in panel.detail.text()
    assert "ACK SNR 19.8 dB" in panel.detail.text()
    assert "DATA EVM 24.0%" in panel.detail.text()
    panel.close()


def test_receiver_control_quality_cannot_replace_data_quality():
    link = OfdmLink(SC_FTN_2K7, ScriptedPipe(0), codec=ScriptedCodec())
    link.status.direction = "receive"
    frame = data_frame({0: b"x" * 256}, 2)
    frame.metrics = LinkMetrics(residual_snr_db=12.5, evm_rms=.24)
    link._record(frame)
    link._data_header = frame.header
    poll = DecodedBurst(header=PhyHeader(OfdmFrameType.POLL, 115),
                        metrics=LinkMetrics(residual_snr_db=30.0, evm_rms=.03))
    link._record(poll)
    assert link.status.snr_db == 12.5
    assert link.status.evm_rms == .24
