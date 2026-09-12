"""End-to-end QA contracts for the G1 SC-FTN Operations/AutoTune port.

These tests deliberately use a queued in-memory control bus.  They exercise the
same Operations calibration callbacks that a live audio channel uses while
keeping RF, serial ports, and real audio devices out of the test process.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
import numpy as np

from guardian.config import StationConfig
from guardian.message import Folder, MailMessage, MessageStore, Status
from guardian.operations import (
    Operations,
    QuickSweepMeasurement,
    StationLabStatus,
)
from guardian.protocol import ControlFrame, Flags, FrameType
from guardian.routing import HeardStations, RouteTable
from guardian.services import EventBus, SnapshotStore
from guardian.session import SessionState
from guardian.station_lab import (
    CalibrationReport,
    CalibrationState,
    ProbeCommand,
    ProbeResult,
)


class _Pending:
    """A worker value whose completion is released by the paired bus."""

    def __init__(self, value):
        self.value = value


class _NeverReady:
    """Event-shaped probe result gate for deterministic timeout coverage."""

    def clear(self):
        return None

    def wait(self, _timeout):
        return False


class _QueuedWorkers:
    """Small deterministic WorkerPool substitute for paired callbacks."""

    def __init__(self):
        self._active: set[str] = set()
        self._completed: deque[tuple[str, object, object]] = deque()
        self._pending: dict[_Pending, tuple[str, object]] = {}

    def submit(self, name, operation, on_complete=None, *, replace=False):
        if name in self._active and not replace:
            return False
        self._active.add(name)
        try:
            value = operation()
        except BaseException as exc:  # match WorkerPool's delivery contract
            value = SimpleNamespace(name=name, value=None, error=exc)
            self._active.discard(name)
            self._completed.append((name, value, on_complete))
            return True
        if isinstance(value, _Pending):
            self._pending[value] = (name, on_complete)
        else:
            self._active.discard(name)
            self._completed.append(
                (name, SimpleNamespace(name=name, value=value, error=None), on_complete)
            )
        return True

    def resolve(self, pending: _Pending, value=None):
        name, callback = self._pending.pop(pending)
        self._active.discard(name)
        self._completed.append(
            (name, SimpleNamespace(name=name, value=value, error=None), callback)
        )

    def drain(self) -> int:
        count = 0
        while self._completed:
            _name, result, callback = self._completed.popleft()
            count += 1
            if callback is not None:
                callback(result)
        return count

    def is_active(self, name: str) -> bool:
        return name in self._active


@dataclass
class _CalBus:
    """Queue direct CAL frames so worker callbacks cannot re-enter each other."""

    queue: deque = None

    def __post_init__(self):
        self.queue = deque()
        self.frames: list[ControlFrame] = []
        self.targets: dict[str, Operations] = {}

    def attach(self, sender: Operations, peer: Operations) -> None:
        self.targets[sender.config.callsign] = sender

        def send(frame_type, destination, session_id, token=""):
            frame = ControlFrame(
                frame_type,
                sender.config.callsign,
                destination,
                token,
                session_id,
            )
            self.frames.append(frame)
            target = self.targets.get(str(destination).strip().upper())
            assert target is peer
            self.queue.append((target, frame))
            return frame

        sender.net.send_calibration_frame = send


def _operations(tmp_path, callsign: str, workers: _QueuedWorkers, **overrides):
    config = StationConfig(callsign=callsign, radio_backend="none", **overrides)
    # Tests must not write a real operator configuration while exercising the
    # report/apply callbacks.
    config.save = lambda: None
    operations = Operations(
        config,
        EventBus(),
        SnapshotStore(),
        workers,
        MessageStore(tmp_path / callsign.lower()),
        RouteTable(),
        HeardStations(),
    )
    operations.audio_transport = SimpleNamespace(
        wait_tx_idle=lambda **_kwargs: True,
        stop=lambda: None,
        start=lambda: None,
        pump=lambda: None,
    )
    return operations


def _good_result(command: ProbeCommand) -> ProbeResult:
    return ProbeResult(
        sequence=command.sequence,
        tx_scale=command.tx_scale,
        waveform=command.waveform,
        mcs=command.mcs,
        fec=command.fec,
        frame_ok=True,
        header_ok=True,
        snr_db=15.0,
        evm_rms=0.1,
        audio_peak=0.5,
        clipped_samples=0,
        sync_confidence=0.95,
        payload_bytes=512,
        wall_seconds=1.0,
        bandwidth=command.bandwidth,
    )


def _report(operations: Operations, direction: str) -> CalibrationReport:
    plan = operations._quick_calibration_plan()
    report = CalibrationReport(
        operations.station_lab.session_id,
        operations.station_lab.peer,
        "quick",
        direction,
        "2026-01-01T00:00:00Z",
    )
    # Two identical valid samples make the v5 Quick recommendation eligible.
    report.results.extend([_good_result(plan[0]), _good_result(plan[0])])
    report.finish("complete")
    return report


def _measurement(operations: Operations) -> QuickSweepMeasurement:
    plan = operations._quick_calibration_plan()
    report = _report(operations, "inbound")
    return QuickSweepMeasurement(report, _good_result(plan[0]))


def _drive_pair(a: Operations, b: Operations, bus: _CalBus, wa, wb) -> None:
    """Drain worker completions and direct CAL frames until both queues settle."""
    while True:
        progressed = bool(wa.drain() or wb.drain())
        if bus.queue:
            target, frame = bus.queue.popleft()
            target._on_calibration_frame(frame)

            # The production runner only advances to TURN/DONE after the
            # report for that direction has arrived.  Release the deferred
            # worker at the same point to preserve that ordering in this bus.
            if target is a and frame.type is FrameType.CAL_REPORT:
                pending = getattr(a, "_review_initial_pending", None)
                if pending is not None:
                    a._signal_calibration_direction_complete()
                    wa.resolve(pending, _report(a, "outbound"))
                    a._review_initial_pending = None
            if target is b and frame.type is FrameType.CAL_REPORT:
                pending = getattr(b, "_review_reverse_pending", None)
                if pending is not None:
                    b._signal_calibration_direction_complete()
                    wb.resolve(pending, _report(b, "outbound"))
                    b._review_reverse_pending = None
            progressed = True
            continue
        if not progressed:
            return


def test_paired_v5_quick_flow_covers_turn_done_reverse_and_reports(
    tmp_path, monkeypatch
):
    """Both stations complete the actual v5 Quick state machine in both directions."""
    # The real receiver guards a report against an audio key-up transient.
    # The guard is intentionally bypassed here because this is a no-audio
    # loopback; the state and report ordering remain production callbacks.
    monkeypatch.setattr("guardian.operations.time.sleep", lambda _seconds: None)
    wa, wb = _QueuedWorkers(), _QueuedWorkers()
    a = _operations(tmp_path, "OK7PS", wa, payload_backend="ofdm_vhf")
    b = _operations(tmp_path, "OK1ABC", wb, payload_backend="ofdm_vhf")
    bus = _CalBus()
    bus.attach(a, b)
    bus.attach(b, a)

    def initiator_run():
        plan = a._quick_calibration_plan()
        a._calibration_commands = {item.sequence: item for item in plan}
        a.station_lab.state = CalibrationState.MEASURING.value
        a.net.send_calibration_frame(
            FrameType.CAL_PROBE, a.station_lab.peer, a.station_lab.session_id,
            plan[0].encode(),
        )
        pending = _Pending(_report(a, "outbound"))
        a._review_initial_pending = pending
        return pending

    def responder_run():
        plan = b._quick_calibration_plan()
        b._calibration_commands = {item.sequence: item for item in plan}
        b.station_lab.state = CalibrationState.MEASURING.value
        b.net.send_calibration_frame(
            FrameType.CAL_PROBE, b.station_lab.peer, b.station_lab.session_id,
            plan[0].encode(),
        )
        pending = _Pending(_report(b, "outbound"))
        b._review_reverse_pending = pending
        return pending

    a._run_station_calibration = initiator_run
    b._run_station_calibration = responder_run
    a._receive_quick_calibration = lambda _command: _measurement(a)
    b._receive_quick_calibration = lambda _command: _measurement(b)

    try:
        assert a.start_station_calibration("OK1ABC", mode="quick")
        _drive_pair(a, b, bus, wa, wb)
        assert b.station_lab.pending_offer
        assert b.accept_station_calibration()
        _drive_pair(a, b, bus, wa, wb)

        assert a.station_lab.state == CalibrationState.COMPLETE.value
        assert b.station_lab.state == CalibrationState.COMPLETE.value
        assert a.station_lab.report is not None
        assert b.station_lab.report is not None
        assert b._calibration_reverse_started is True
        assert not a._calibration_turn_pending
        assert not b._calibration_done_pending

        sequence = [
            (frame.source, frame.type)
            for frame in bus.frames
        ]
        assert sequence == [
            ("OK7PS", FrameType.CAL_OFFER),
            ("OK1ABC", FrameType.CAL_ACCEPT),
            ("OK7PS", FrameType.CAL_PROBE),
            ("OK1ABC", FrameType.CAL_REPORT),
            ("OK7PS", FrameType.CAL_DONE),
            ("OK1ABC", FrameType.CAL_PROBE),
            ("OK7PS", FrameType.CAL_REPORT),
            ("OK1ABC", FrameType.CAL_DONE),
            ("OK7PS", FrameType.CAL_DONE),
        ]
        assert bus.frames[0].next_hop == "Q5-1"
        assert bus.frames[1].next_hop == "Q5"
        assert all(
            frame.next_hop
            for frame in bus.frames
            if frame.type in {FrameType.CAL_PROBE, FrameType.CAL_REPORT}
        )
        assert [frame.next_hop for frame in bus.frames if frame.type is FrameType.CAL_DONE] == [
            "TURN", "DONE", "COMPLETE"
        ]
        assert all(frame.destination in {"OK7PS", "OK1ABC"} for frame in bus.frames)
        assert not any(frame.type is FrameType.CAL_CANCEL for frame in bus.frames)
    finally:
        a.close()
        b.close()


def test_quick_report_timeout_retries_probe_before_cancelling(tmp_path, monkeypatch):
    """A missing v5 report retries the same probe and never turns early."""
    workers = _QueuedWorkers()
    operations = _operations(tmp_path, "OK7PS", workers)
    sent: list[tuple[FrameType, str]] = []
    operations.net.send_calibration_frame = (
        lambda frame_type, peer, _session_id, token="": sent.append((frame_type, token))
    )
    operations.station_lab = StationLabStatus(
        state=CalibrationState.PREPARING.value,
        peer="OK1ABC",
        session_id=77,
        mode="quick",
    )
    operations._calibration_initiator = True
    operations._quick_calibration_plan = lambda: [
        ProbeCommand(0, "sc_ftn", 1, 1, 0.25, "2K7")
    ]
    operations._transmit_quick_calibration = lambda _plan: 1
    operations._calibration_report_ready = _NeverReady()
    operations.audio_transport.wait_tx_idle = lambda **_kwargs: True
    monkeypatch.setattr("guardian.operations.time.sleep", lambda _seconds: None)
    report = CalibrationReport(77, "OK1ABC", "quick", "outbound", "2026-01-01")
    monkeypatch.setattr(report, "save", lambda: (None, None))

    try:
        with pytest.raises(RuntimeError, match="did not return a Quick Tune result"):
            operations._run_quick_station_calibration(report)
        probes = [token for kind, token in sent if kind is FrameType.CAL_PROBE]
        assert len(probes) == 3
        assert sent[-1] == (FrameType.CAL_CANCEL, "NO_REPORT")
        assert (FrameType.CAL_DONE, "TURN") not in sent
    finally:
        operations.close()


def test_quick_receiver_ignores_keyup_and_reports_best_repeated_level(
    tmp_path, monkeypatch
):
    """The v5 receiver filters a key-up capture and returns one best report."""
    from guardian.ofdm.framing import OfdmFrameType, PhyHeader

    workers = _QueuedWorkers()
    operations = _operations(tmp_path, "OK7PS", workers)
    operations.station_lab = StationLabStatus(
        state=CalibrationState.MEASURING.value,
        peer="OK1ABC",
        session_id=77,
        mode="quick",
    )
    plan = operations._quick_calibration_plan()
    profile = SimpleNamespace(sample_rate=100, block_size=512)

    class Codec:
        def decode_burst(self, _profile, samples):
            marker = round(float(samples[0]) * 100)
            metrics = SimpleNamespace(
                residual_snr_db=None,
                snr_db=None if marker == 0 else 20.0 - abs((marker - 1) - 12.5),
                evm_rms=None if marker == 0 else 0.08,
                audio_rms=None if marker == 0 else 0.55,
                crest_factor_db=0.0,
                sync_confidence=None if marker == 0 else 0.95,
                cfo_hz=0.0,
                error="no header" if marker == 0 else "",
            )
            header = None if marker == 0 else PhyHeader(
                OfdmFrameType.DATA,
                77,
                block_seq=marker - 1,
                block_count=len(plan),
                mcs=1,
                fec=5,
                payload_len=512,
            )
            return SimpleNamespace(
                header=header,
                metrics=metrics,
                ok=header is not None,
            )

    codec = Codec()
    captures = [np.asarray([0.0])] + [
        np.asarray([index / 100.0]) for index in range(1, len(plan) + 1)
    ]

    class Pipe:
        def __init__(self, *args, **kwargs):  # noqa: ARG002
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def receive(self, timeout):  # noqa: ARG002
            return captures.pop(0) if captures else None

    monkeypatch.setattr(
        operations,
        "_calibration_profile_codec",
        lambda _command: (profile, codec),
    )
    monkeypatch.setattr(
        operations,
        "_build_calibration_waveform",
        lambda command, total: (
            profile,
            codec,
            np.ones(10, dtype=np.float64) * (command.sequence + 1),
        ),
    )
    monkeypatch.setattr("guardian.payload.ofdm_vhf.RadioAudioPipe", Pipe)
    monkeypatch.setattr(
        "guardian.ofdm.bench.write_wav",
        lambda path, samples, sample_rate: tmp_path / f"{samples[0]:.2f}.wav",
    )
    monkeypatch.setattr(
        CalibrationReport,
        "save",
        lambda self, directory=None: (tmp_path / "cal.json", tmp_path / "cal.csv"),
    )
    monkeypatch.setattr("guardian.operations.time.sleep", lambda _seconds: None)
    sent: list[ControlFrame] = []
    operations.net.send_calibration_frame = (
        lambda frame_type, peer, session_id, token="": sent.append(
            ControlFrame(frame_type, "OK7PS", peer, token, session_id)
        )
    )

    try:
        measurement = operations._receive_quick_calibration(plan[0])

        assert isinstance(measurement, QuickSweepMeasurement)
        assert len(measurement.report.results) == len(plan)
        assert measurement.report.recommendation is not None
        assert measurement.selected.tx_scale == pytest.approx(0.063)
        assert any(
            "ignored a key-up transient" in event.message
            for event in operations.events.history()
        )

        operations._station_calibration_rx_finished(
            SimpleNamespace(value=measurement, error=None)
        )
        reports = [frame for frame in sent if frame.type is FrameType.CAL_REPORT]
        assert len(reports) == 1
        decoded = ProbeResult.decode_report(
            reports[0].next_hop,
            plan[measurement.selected.sequence],
        )
        assert decoded.sequence == measurement.selected.sequence
        assert decoded.frame_ok
        assert operations.station_lab.state == CalibrationState.PREPARING.value
    finally:
        operations.audio_transport = None
        operations.close()


def test_sc_payload_prepares_compression_before_announcing_bundle(tmp_path):
    """SC selection keeps the prepare→announce boundary and bundle flags intact."""
    workers = _QueuedWorkers()
    operations = _operations(
        tmp_path,
        "OK7PS",
        workers,
        payload_backend="ofdm_vhf",
        guardian_compression=True,
    )
    mail = MailMessage(
        msg_id=operations.mailstore.next_id("OK7PS"),
        source="OK7PS",
        final_dest="OK1ABC",
        subject="SC report",
        body="same payload line\n" * 10_000,
        folder=Folder.OUTBOX,
        status=Status.QUEUED,
    )
    operations.mailstore.add(mail)
    announced: list[dict] = []
    operations.net.send_message = lambda **kwargs: announced.append(kwargs)

    try:
        assert operations.send_queued(mail.msg_id)
        assert announced == []
        assert operations._mail_preparing == {mail.msg_id}
        workers.drain()
        assert len(announced) == 1
        assert operations._mail_preparing == set()
        assert announced[0]["flags"] & Flags.COMPRESSED
        restored = MailMessage.from_bundle(announced[0]["payload_bytes"])
        assert restored.body == mail.body
        assert operations.config.payload_backend == "ofdm_vhf"
        assert operations.net.payload.backends["ofdm_vhf"].name == "ofdm_vhf"
    finally:
        operations.audio_transport = None
        operations.close()


def test_stop_control_cancels_active_sc_pipe_before_replacing_orchestrator(tmp_path):
    """Stopping control releases an active SC pipe before the net is rebuilt."""
    workers = _QueuedWorkers()
    operations = _operations(tmp_path, "OK7PS", workers, payload_backend="ofdm_vhf")
    backend = operations.net.payload.backends["ofdm_vhf"]
    stopped: list[bool] = []

    class Pipe:
        def stop(self):
            stopped.append(True)

    message = SimpleNamespace(
        msg_id=82,
        source="OK7PS",
        final_dest="OK1ABC",
        next_hop="OK1ABC",
        direction="out",
        state=SessionState.TRANSFERRING,
        payload_transport="ofdm_vhf",
    )
    operations.net.sessions[message.msg_id] = message
    backend._active_msg_id = message.msg_id
    backend._active_message = message
    backend._active_pipe = Pipe()
    old_net = operations.net
    operations.audio_transport = SimpleNamespace(stop=lambda: None)

    try:
        operations.stop_control_channel()
        assert stopped == [True]
        assert backend._cancelled.is_set()
        assert message.state is SessionState.CANCELLED
        assert operations.net is not old_net
    finally:
        operations.close()


def test_cancelled_receiver_does_not_send_late_report(tmp_path):
    """A worker callback racing cancellation cannot reopen the calibration flow."""
    workers = _QueuedWorkers()
    operations = _operations(tmp_path, "OK7PS", workers)
    sent: list[tuple[FrameType, str]] = []
    operations.net.send_calibration_frame = (
        lambda frame_type, peer, _session_id, token="": sent.append((frame_type, token))
    )
    operations.station_lab = StationLabStatus(
        state=CalibrationState.MEASURING.value,
        peer="OK1ABC",
        session_id=78,
        mode="quick",
    )
    operations._calibration_cancel.set()
    command = ProbeCommand(0, "sc_ftn", 1, 1, 0.25, "2K7")

    try:
        operations._station_calibration_rx_finished(
            SimpleNamespace(value=QuickSweepMeasurement(_report(operations, "inbound"),
                                                         _good_result(command)),
                            error=None)
        )
        assert sent == []
        assert operations.station_lab.state == CalibrationState.MEASURING.value
    finally:
        operations.close()


def test_restart_control_refuses_live_payload_without_tearing_down_audio(tmp_path):
    """Reconfiguring an active control channel must preserve the payload owner."""
    workers = _QueuedWorkers()
    operations = _operations(tmp_path, "OK7PS", workers)
    transport = operations.audio_transport
    old_net = operations.net
    operations._payload_active.set()

    try:
        assert operations.restart_control_channel() is False
        assert operations.audio_transport is transport
        assert operations.net is old_net
    finally:
        operations._payload_active.clear()
        operations.close()


def test_network_settings_guard_covers_profile_negotiation_before_payload_start(
    tmp_path,
):
    """A live working/profile handshake also owns the current payload backend."""
    workers = _QueuedWorkers()
    operations = _operations(tmp_path, "OK7PS", workers, payload_backend="ofdm_vhf")
    old_net = operations.net
    operations.net.sessions[80] = SimpleNamespace(
        msg_id=80,
        state=SessionState.NEGOTIATING_PROFILE,
        payload_transport="ofdm_vhf",
    )

    try:
        operations.apply_network_settings()
        assert operations.net is old_net
        assert operations.net.payload.backends["ofdm_vhf"] is not None
    finally:
        operations.close()


def test_unknown_calibration_done_marker_cannot_complete_session(tmp_path):
    """Malformed v5 DONE control cannot turn an in-flight session complete."""
    workers = _QueuedWorkers()
    operations = _operations(tmp_path, "OK7PS", workers)
    operations.station_lab = StationLabStatus(
        state=CalibrationState.PREPARING.value,
        peer="OK1ABC",
        session_id=79,
        mode="quick",
    )
    operations._calibration_initiator = True
    operations._calibration_turn_pending = True
    frame = ControlFrame(
        FrameType.CAL_DONE,
        "OK1ABC",
        "OK7PS",
        "NOT-A-MARKER",
        79,
    )

    try:
        operations._on_calibration_frame(frame)
        assert operations.station_lab.state != CalibrationState.COMPLETE.value
    finally:
        operations.close()


def test_negotiated_ptt_delay_updates_sc_backend_turnaround(tmp_path):
    """A negotiated slow keying delay must reach the SC ARQ timing budget."""
    workers = _QueuedWorkers()
    operations = _operations(tmp_path, "OK7PS", workers, payload_backend="ofdm_vhf")
    backend = operations.net.payload.backends["ofdm_vhf"]
    before = backend.ptt_turnaround
    message = SimpleNamespace(
        msg_id=81,
        source="OK1ABC",
        final_dest="OK1ABC",
        next_hop="OK1ABC",
        direction="out",
        state=SessionState.TRANSFERRING,
        payload_transport="ofdm_vhf",
        ptt_delay_ms=700,
    )

    try:
        operations.net.sessions[81] = message
        operations._session_event(message, "slow keying negotiated")
        assert operations._payload_ptt_delay_ms == 700
        assert backend.ptt_turnaround >= 0.7
        assert backend.ptt_turnaround > before
    finally:
        operations.close()
