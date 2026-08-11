from __future__ import annotations

import json

import pytest

from guardian.station_lab import (
    CalibrationReport,
    GainJournal,
    GainSnapshot,
    ProbeCommand,
    ProbeResult,
    full_plan,
    quick_plan,
    recommend,
)
from guardian.protocol import FrameType
from guardian.session import LoopbackBus, Orchestrator


def _result(scale: float, goodput: float, *, sequence: int = 1,
            safe: bool = True, ok: bool = True) -> ProbeResult:
    return ProbeResult(
        sequence=sequence, tx_scale=scale, waveform="sc_ftn", mcs=6, fec=3,
        frame_ok=ok, header_ok=safe, audio_peak=0.7 if safe else 1.0,
        clipped_samples=0 if safe else 4, payload_bytes=1000,
        wall_seconds=8000.0 / goodput if goodput else 1.0,
    )


def test_probe_and_compact_report_round_trip():
    command = ProbeCommand(29, "sc_ftn", 6, 3, 0.563)
    assert ProbeCommand.decode(command.encode()) == command

    result = ProbeResult(
        sequence=29, tx_scale=0.563, waveform="sc_ftn", mcs=6, fec=3,
        frame_ok=True, header_ok=True, snr_db=18.5, evm_rms=0.126,
        audio_peak=0.82, audio_rms=0.24, clipped_samples=0,
        sync_confidence=0.91, cfo_hz=-4.2,
    )
    token = result.encode_report()
    assert len(token) <= 16
    decoded = ProbeResult.decode_report(token, command)
    assert decoded.frame_ok and decoded.header_ok
    assert decoded.snr_db == pytest.approx(18.5, abs=0.5)
    assert decoded.evm_rms == pytest.approx(0.126, rel=0.08)
    assert decoded.audio_peak == pytest.approx(0.82, abs=0.01)


def test_recommendation_is_lowest_point_within_half_db():
    results = []
    for seq, (scale, goodput) in enumerate(((0.4, 8800), (0.55, 9800), (0.8, 10000))):
        results.extend(_result(scale, goodput, sequence=seq) for _ in range(3))
    selected = recommend(results)
    assert selected is not None
    assert selected.tx_scale == 0.55
    assert selected.score_bps == pytest.approx(9800)


def test_unsafe_or_unreliable_points_are_rejected():
    results = [_result(0.4, 8000, sequence=i) for i in range(3)]
    results += [_result(0.8, 15000, sequence=4, safe=False) for _ in range(3)]
    results += [_result(0.6, 12000, sequence=5, ok=False) for _ in range(2)]
    results += [_result(0.6, 12000, sequence=5, ok=True)]
    selected = recommend(results)
    assert selected is not None
    assert selected.tx_scale == 0.4


def test_plans_are_bounded_and_start_low():
    quick = quick_plan("sc_ftn", 6, 3)
    assert quick[0].tx_scale == 0.2
    assert all(a.tx_scale < b.tx_scale for a, b in zip(quick, quick[1:]))
    assert len(full_plan()) <= 96


def test_report_persists_raw_json_and_csv(tmp_path):
    report = CalibrationReport(0x1234, "OK1AAA", "quick", "outbound",
                               "2026-08-11T12:00:00Z")
    report.results.append(_result(0.5, 9000))
    report.finish("complete")
    json_path, csv_path = report.save(tmp_path)
    assert json.loads(json_path.read_text(encoding="utf-8"))["peer"] == "OK1AAA"
    assert "tx_scale" in csv_path.read_text(encoding="utf-8-sig")


def test_gain_journal_round_trip_and_clear(tmp_path):
    journal = GainJournal(tmp_path / "restore.json")
    snapshot = GainSnapshot(0.7, False, 0.9, True, "USB Audio", "WASAPI")
    journal.arm(snapshot)
    assert journal.load() == snapshot
    journal.clear()
    assert journal.load() is None


def test_calibration_frames_are_direct_and_do_not_create_mail_sessions():
    bus = LoopbackBus()
    sender = Orchestrator("OK7PS", bus.endpoint("sender"), auto_route=False)
    receiver = Orchestrator("OK1AAA", bus.endpoint("receiver"), auto_route=False)
    seen = []
    receiver.on_calibration_frame = seen.append

    frame = sender.send_calibration_frame(
        FrameType.CAL_OFFER, "OK1AAA", 0x12345678, "Q1"
    )
    for _ in range(4):
        bus.pump()
        sender.tick(1.0)
        receiver.tick(1.0)

    assert seen == [frame]
    assert sender.sessions == {}
    assert receiver.sessions == {}


def test_worst_case_calibration_report_stays_inside_control_frame_limit():
    from guardian.protocol import ControlFrame, MAX_CONTROL_FRAME_BYTES

    command = ProbeCommand(255, "sefdm", 6, 4, 1.0)
    result = ProbeResult(
        255, 1.0, "sefdm", 6, 4, True, header_ok=True,
        snr_db=40.0, evm_rms=0.01, audio_peak=0.9,
        clipped_samples=0, sync_confidence=1.0,
    )
    frame = ControlFrame(
        FrameType.CAL_REPORT, "OK1234567", "OL1234567",
        result.encode_report(), 0xFFFFFFFF,
    )
    assert len(frame.encode()) <= MAX_CONTROL_FRAME_BYTES
    assert ProbeCommand.decode(command.encode()) == command
