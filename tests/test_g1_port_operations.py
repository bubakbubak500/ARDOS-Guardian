"""Focused contracts for the selective Operations/SC-FTN integration."""

from __future__ import annotations

import time
from types import SimpleNamespace

from guardian.config import StationConfig
from guardian.message import MessageStore
from guardian.operations import Operations
from guardian.protocol import FrameType
from guardian.routing import HeardStations, RouteTable
from guardian.services import EventBus, SnapshotStore, WorkerPool
from guardian.session import SessionState


def _operations(tmp_path, **overrides):
    config = StationConfig(callsign="OK7PS", radio_backend="none", **overrides)
    workers = WorkerPool(max_workers=1)
    operations = Operations(
        config,
        EventBus(),
        SnapshotStore(),
        workers,
        MessageStore(tmp_path / "mail"),
        RouteTable(),
        HeardStations(),
    )
    return operations, workers


def test_payload_dependencies_include_calibrated_path_and_handoff_hook(tmp_path):
    operations, workers = _operations(
        tmp_path, payload_backend="ofdm_vhf", g2_tx_scales={"sc_ftn": 0.42}
    )
    try:
        dependencies = operations._payload_dependencies()
        assert dependencies["radio_model"] == operations.config.radio
        assert dependencies["g2_tx_scale"] == 0.42
        assert dependencies["on_handoff_failed"] == operations._payload_handoff_failed
    finally:
        operations.close()
        workers.close(wait=True)


def test_ofdm_status_follows_active_negotiated_transport(tmp_path):
    operations, workers = _operations(tmp_path, payload_backend="ofdm_vhf")
    try:
        backend = operations.net.payload.backends["ofdm_vhf"]
        backend.status.state = "failed"
        message = SimpleNamespace(
            state=SessionState.TRANSFERRING, payload_transport="ofdm_vhf"
        )
        operations.net.sessions[91] = message
        operations._payload_active.set()

        assert operations.ofdm_status() is backend.status

        # A later hop can negotiate the established VARA path.  Its active
        # status must not display the stale failed SC-FTN backend.
        message.payload_transport = "vara_p2p"
        assert operations.ofdm_status() is None
    finally:
        operations.close()
        workers.close(wait=True)


def test_ofdm_status_exposes_active_message_identity_without_mutating_backend(
    tmp_path,
):
    operations, workers = _operations(tmp_path, payload_backend="ofdm_vhf")
    try:
        backend = operations.net.payload.backends["ofdm_vhf"]
        message = SimpleNamespace(
            state=SessionState.TRANSFERRING,
            payload_transport="ofdm_vhf",
            direction="out",
            source="OK7PS",
            final_dest="OK2XYZ",
            next_hop="RELAY1",
        )
        operations.net.sessions[92] = message
        operations._payload_active.set()

        status = operations.ofdm_status()

        assert status is not backend.status
        assert status.transfer_source == "OK7PS"
        assert status.transfer_destination == "OK2XYZ"
        assert status.transfer_via == "RELAY1"
        assert backend.status.transfer_source == ""
        assert backend.status.transfer_destination == ""
        assert backend.status.transfer_via == ""

        # The immediate inbound peer is a route hop, not a trusted origin.
        message.direction = "in"
        message.source = "RELAY1"
        message.final_dest = "OK7PS"
        message.next_hop = "OK7PS"
        inbound = operations.ofdm_status()
        assert inbound.transfer_source == ""
        assert inbound.transfer_destination == "OK7PS"
        assert inbound.transfer_via == "RELAY1"
    finally:
        operations.close()
        workers.close(wait=True)


def test_radio_ptt_rejection_reaches_native_transmitter(tmp_path):
    operations, workers = _operations(tmp_path)
    try:
        class RejectingRadio:
            def set_ptt(self, enabled):
                raise OSError("PTT refused")

        operations.radio = RejectingRadio()
        try:
            operations._radio_ptt(True)
        except OSError as exc:
            assert "PTT refused" in str(exc)
        else:
            raise AssertionError("a rejected PTT command must propagate")
        assert any("PTT error: PTT refused" in record.message for record in operations.events.history())
    finally:
        operations.close()
        workers.close(wait=True)


def test_pending_vara_handoff_is_retried_from_tick_without_releasing_early(
    tmp_path,
):
    operations, workers = _operations(tmp_path)
    calls: list[str] = []

    def resume() -> bool:
        calls.append("resume")
        return True

    try:
        operations._payload_handoff_failed(resume)
        assert operations.payload_handoff_pending()
        assert operations.payload_handoff_error()

        operations.tick()
        deadline = time.monotonic() + 2.0
        while workers.is_active("payload-handoff") and time.monotonic() < deadline:
            time.sleep(0.001)
        workers.drain()

        assert calls == ["resume"]
        assert not operations.payload_handoff_pending()
    finally:
        operations.close()
        workers.close(wait=True)


def test_beacon_gate_blocks_every_active_calibration_state(tmp_path):
    operations, workers = _operations(tmp_path)
    try:
        operations.audio_transport = SimpleNamespace()
        for state in (
            "offering",
            "waiting_approval",
            "preparing",
            "measuring",
            "waiting_report",
        ):
            operations.station_lab.state = state
            reason = operations._beacon_block_reason(manual=True)
            assert reason and "calibration" in reason.lower()
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_station_lab_status_is_published_as_an_immutable_snapshot(tmp_path):
    operations, workers = _operations(tmp_path, payload_backend="ofdm_vhf")
    try:
        operations.audio_transport = SimpleNamespace()
        operations.net.send_calibration_frame = lambda *args: args
        assert operations.start_station_calibration("OK1AAA")
        snapshot = operations.snapshots.read().station_lab
        assert snapshot.state == "offering"
        assert snapshot.peer == "OK1AAA"
        assert snapshot.pending_offer is False
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_close_cancels_active_calibration_before_tearing_down_control(tmp_path):
    operations, workers = _operations(tmp_path)
    try:
        operations.audio_transport = SimpleNamespace(stop=lambda: None)
        operations.station_lab.state = "measuring"
        operations.station_lab.peer = "OK1AAA"
        operations.station_lab.session_id = 123
        operations.close()
        assert operations._calibration_cancel.is_set()
        assert operations.station_lab.state == "cancelled"
    finally:
        workers.close(wait=True)


def test_close_drops_deferred_handoff_and_cannot_reopen_audio(tmp_path):
    operations, workers = _operations(tmp_path)
    resumed: list[str] = []
    try:
        operations.audio_transport = SimpleNamespace(
            stop=lambda: None,
            start=lambda: resumed.append("start"),
        )
        operations._payload_handoff_failed(lambda: resumed.append("resume") or True)
        operations.close()
        operations._resume_control()
        assert not operations.payload_handoff_pending()
        assert resumed == []
    finally:
        workers.close(wait=True)


def test_stop_control_cancels_live_payload_before_replacing_net(tmp_path):
    operations, workers = _operations(tmp_path)
    try:
        stopped: list[str] = []
        cancelled: list[int] = []
        shutdowns: list[str] = []
        operations.audio_transport = SimpleNamespace(
            stop=lambda: stopped.append("audio")
        )
        old_net = operations.net
        old_net.cancel = lambda msg_id, notify=False: cancelled.append(msg_id)
        old_net.payload = SimpleNamespace(
            shutdown=lambda: shutdowns.append("payload")
        )
        old_net.sessions[93] = SimpleNamespace(
            msg_id=93,
            state=SessionState.TRANSFERRING,
        )

        operations.stop_control_channel()

        assert stopped == ["audio"]
        assert cancelled == [93]
        assert shutdowns == ["payload"]
        assert operations.net is not old_net
    finally:
        operations.close()
        workers.close(wait=True)


def test_network_settings_busy_covers_control_handshake_before_audio_ownership(
    tmp_path,
):
    """Settings cannot replace the backend while a session is negotiating."""
    operations, workers = _operations(tmp_path)
    try:
        old_net = operations.net
        operations.net.sessions[94] = SimpleNamespace(
            msg_id=94,
            state=SessionState.NEGOTIATING_WORKING,
        )

        assert operations.network_settings_busy()
        operations.apply_network_settings()

        assert operations.net is old_net
    finally:
        operations.close()
        workers.close(wait=True)
