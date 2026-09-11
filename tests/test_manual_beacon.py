import threading
import time
from types import SimpleNamespace

import pytest

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


def _sent(operations):
    sent = []
    original = operations.net.transport.send

    def record(frame):
        sent.append(frame)
        return original(frame)

    operations.net.transport.send = record
    return sent


def test_manual_beacon_works_when_automatic_beacons_are_disabled(tmp_path) -> None:
    operations, workers = _operations(
        tmp_path,
        beacon_enabled=False,
        beacon_interval=86_400.0,
        station_grid="jn89he",
        beacon_position=True,
    )
    operations.audio_transport = SimpleNamespace()
    sent = _sent(operations)
    try:
        assert operations.send_beacon_now() is True
        assert len(sent) == 1
        assert sent[0].type is FrameType.BEACON
        assert sent[0].source == "OK7PS"
        assert sent[0].destination == "JN89HE"
        assert "queued for transmission" in operations.events.history()[-1].message

        # A forced send skips the long automatic interval, but two clicks close
        # together do not create a burst of back-to-back beacons.
        assert operations.send_beacon_now() is False
        assert len(sent) == 1
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_manual_beacon_does_not_share_position_when_disabled(tmp_path) -> None:
    operations, workers = _operations(
        tmp_path,
        station_grid="JN89HE",
        beacon_position=False,
    )
    operations.audio_transport = SimpleNamespace()
    sent = _sent(operations)
    try:
        assert operations.send_beacon_now() is True
        assert sent[0].type is FrameType.BEACON
        assert sent[0].destination == ""
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


@pytest.mark.parametrize(
    "block, expected",
    [
        ("payload", "VARA payload"),
        ("session", "network session"),
        ("scanner", "channel scanner"),
        ("sweep", "frequency sweep"),
        ("radio", "radio control"),
        ("tx", "control burst"),
    ],
)
def test_manual_beacon_refuses_competing_work(tmp_path, monkeypatch, block, expected) -> None:
    operations, workers = _operations(tmp_path)
    operations.audio_transport = SimpleNamespace(
        wait_tx_idle=(lambda timeout: block != "tx"),
    )
    sent = _sent(operations)
    try:
        if block == "payload":
            operations._payload_active.set()
        elif block == "session":
            operations.net.sessions[1] = SimpleNamespace(state=SessionState.ANNOUNCING)
        elif block == "scanner":
            operations.scanner = SimpleNamespace(stop=lambda: None)
        elif block == "sweep":
            monkeypatch.setattr(
                operations.workers,
                "is_active",
                lambda name: name == "alert-sweep",
            )
        elif block == "radio":
            monkeypatch.setattr(
                operations.workers,
                "is_active",
                lambda name: name == "radio-control",
            )

        assert operations.send_beacon_now() is False
        assert sent == []
        assert expected in operations.events.history()[-1].message
    finally:
        operations._payload_active.clear()
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_auto_beacon_uses_the_same_gate_and_keeps_existing_interval(tmp_path) -> None:
    operations, workers = _operations(
        tmp_path,
        beacon_enabled=True,
        beacon_interval=60.0,
    )
    operations.audio_transport = SimpleNamespace()
    sent = _sent(operations)
    try:
        operations._tick_beacon(1_000.0)
        assert len(sent) == 1
        operations._tick_beacon(1_030.0)
        assert len(sent) == 1
        operations._tick_beacon(1_061.0)
        assert len(sent) == 2
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_manual_beacon_does_not_wait_for_a_busy_radio_command(tmp_path) -> None:
    operations, workers = _operations(tmp_path)
    operations.audio_transport = SimpleNamespace()
    sent = _sent(operations)
    entered = threading.Event()
    release = threading.Event()

    def hold_radio_lock() -> None:
        with operations._radio_lock:
            entered.set()
            release.wait(2.0)

    holder = threading.Thread(target=hold_radio_lock)
    holder.start()
    assert entered.wait(1.0)
    try:
        started = time.monotonic()
        assert operations.send_beacon_now() is False
        assert time.monotonic() - started < 0.1
        assert sent == []
    finally:
        release.set()
        holder.join(1.0)
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_network_workspace_exposes_a_bilingual_manual_beacon_action(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from guardian.i18n import Language, set_language
    from guardian.qt.network_workspace import NetworkWorkspace
    from guardian.qt.runtime import ShellRuntime

    app = QApplication.instance() or QApplication([])
    set_language(Language.ENGLISH)
    runtime = ShellRuntime()
    workspace = NetworkWorkspace(runtime)
    try:
        assert workspace.beacon_now.text() == "Send beacon now"
        assert workspace.beacon_now.isEnabled()
        assert "Start control channel" in workspace.beacon_now.toolTip()
    finally:
        workspace.close()
        runtime.close()

    set_language(Language.CZECH)
    runtime = ShellRuntime()
    workspace = NetworkWorkspace(runtime)
    try:
        assert workspace.beacon_now.text() == "Poslat maják nyní"
        assert workspace.beacon_now.isEnabled()
        assert "řídicí kanál" in workspace.beacon_now.toolTip()
    finally:
        workspace.close()
        runtime.close()
        set_language(Language.ENGLISH)
