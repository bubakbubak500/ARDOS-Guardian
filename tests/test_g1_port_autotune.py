"""Focused SC-FTN Pair/Quick AutoTune contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from guardian.config import SC_FTN_BANDWIDTHS, StationConfig
from guardian.operations import Operations, StationLabStatus
from guardian.protocol import ControlFrame, FrameType, MAX_CONTROL_FRAME_BYTES
from guardian.routing import HeardStations, RouteTable
from guardian.services import EventBus, SnapshotStore, WorkerPool
from guardian.message import MessageStore
from guardian.station_lab import (
    GainSnapshot,
    ProbeCommand,
    ProbeResult,
    full_plan,
    quick_plan,
)


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


def test_probe_wire_token_is_sc_only_and_normalizes_case():
    command = ProbeCommand(3, "SC_FTN", 1, 1, 0.25, "2k7")
    decoded = ProbeCommand.decode(command.encode())
    assert decoded.waveform == "sc_ftn"
    assert decoded.bandwidth == "2K7"

    with pytest.raises(ValueError, match="SC-FTN"):
        ProbeCommand(0, "ofdm", 1, 1, 0.5).encode()


def test_vara_cannot_start_or_accept_sc_calibration(tmp_path):
    operations, workers = _operations(tmp_path, payload_backend="vara_p2p")
    sent = []
    operations.net.send_calibration_frame = lambda *args: sent.append(args)
    try:
        assert not operations.start_station_calibration("OK1ABC")
        operations.station_lab.pending_offer = True
        operations.station_lab.peer = "OK1ABC"
        operations.station_lab.session_id = 1
        assert not operations.accept_station_calibration()
        assert not sent
    finally:
        operations.close()
        workers.close()


def test_quick_and_full_plans_never_generate_non_sc_commands():
    quick = quick_plan("SC_FTN", 1, 1, "2K7")
    full = full_plan()
    assert quick
    assert full
    assert {item.waveform for item in quick + full} == {"sc_ftn"}
    assert {item.bandwidth for item in full} <= set(SC_FTN_BANDWIDTHS)

    with pytest.raises(ValueError, match="SC-FTN"):
        full_plan(["ofdm"])


def test_calibration_identity_includes_rigctld_endpoint_even_with_a_cat_port(
    tmp_path,
):
    operations, workers = _operations(
        tmp_path,
        cat_port="COM3",
        rigctld_host="127.0.0.1",
        rigctld_port=4532,
    )
    try:
        first = operations._g2_calibration_key()
        operations.config.rigctld_host = "192.0.2.44"
        assert operations._g2_calibration_key() != first
    finally:
        operations.close()
        workers.close(wait=True)


def test_old_calibration_key_is_not_reused_as_a_verified_scale(tmp_path):
    operations, workers = _operations(tmp_path)
    try:
        operations.config.g2_tx_scales["sc_ftn"] = 0.83
        operations.config.g2_tx_calibrations[
            operations._g2_calibration_key_v2()
        ] = {"tx_scale": 1.7}
        assert operations._current_g2_tx_scale() == pytest.approx(0.83)
    finally:
        operations.close()
        workers.close(wait=True)


def test_worst_case_sc_report_stays_inside_control_frame_limit():
    command = ProbeCommand(255, "sc_ftn", 17, 9, 1.0, "20K")
    result = ProbeCommand.decode(command.encode())
    assert result.waveform == "sc_ftn"
    report = ProbeResult(
        255,
        1.0,
        "sc_ftn",
        17,
        9,
        True,
        header_ok=True,
        snr_db=40.0,
        evm_rms=0.01,
        audio_peak=0.9,
        clipped_samples=0,
        sync_confidence=1.0,
        bandwidth="20K",
    )
    frame = ControlFrame(
        FrameType.CAL_REPORT,
        "OK1234567",
        "OL1234567",
        report.encode_report(),
        0xFFFFFFFF,
    )
    assert len(frame.encode()) <= MAX_CONTROL_FRAME_BYTES


def test_failed_gain_restore_keeps_crash_journal_for_retry(monkeypatch):
    """A mixer failure must leave the recovery marker armed."""
    snapshot = GainSnapshot(
        endpoint_volume=0.42,
        endpoint_muted=False,
        session_volume=0.37,
        supported=True,
        device="guardian output",
        host_api="WASAPI/Core Audio",
    )

    class Journal:
        def __init__(self):
            self.cleared = False

        def load(self):
            return snapshot

        def clear(self):
            self.cleared = True

    class FailingGain:
        def snapshot(self):
            return snapshot

        def restore(self, _snapshot):
            raise OSError("mixer endpoint disappeared")

    journal = Journal()
    monkeypatch.setattr("guardian.operations.GainJournal", lambda: journal)
    monkeypatch.setattr(
        "guardian.operations.WindowsGainController",
        lambda _hint: FailingGain(),
    )
    operations = SimpleNamespace(
        config=SimpleNamespace(audio_output="guardian output"),
        station_lab=StationLabStatus(),
        _log=lambda *args, **kwargs: None,
    )

    Operations._restore_calibration_gain_after_crash(operations)

    assert not journal.cleared
    assert "mixer endpoint disappeared" in operations.station_lab.error
