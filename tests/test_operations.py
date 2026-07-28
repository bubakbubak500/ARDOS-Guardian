import time
from types import SimpleNamespace

from guardian.config import StationConfig
from guardian.message import Folder, MailMessage, MessageStore, Status
from guardian.operations import Operations
from guardian.routing import HeardStations, Route, RouteTable
from guardian.services import EventBus, SnapshotStore, WorkerPool
from guardian.session import SessionState


def test_idle_operations_never_transmit_and_keep_mail_queued(tmp_path) -> None:
    config = StationConfig(callsign="OK7PS", radio_backend="none")
    events = EventBus()
    snapshots = SnapshotStore()
    workers = WorkerPool(max_workers=1)
    mailstore = MessageStore(tmp_path / "mail")
    routes = RouteTable()
    operations = Operations(
        config,
        events,
        snapshots,
        workers,
        mailstore,
        routes,
        HeardStations(),
    )
    message = MailMessage(
        msg_id=mailstore.next_id(config.callsign),
        source=config.callsign,
        final_dest="OK1AAA",
        subject="Safe smoke",
        body="No RF without operator action.",
        created=time.time(),
        folder=Folder.OUTBOX,
        status=Status.QUEUED,
    )
    mailstore.add(message)
    try:
        operations.tick()
        assert operations.audio_transport is None
        assert not operations.send_queued(message.msg_id)
        assert mailstore.get(message.msg_id).status == Status.QUEUED
        assert not snapshots.read().network.control_channel_active
    finally:
        operations.close()
        workers.close(wait=True)


def test_both_payload_modes_use_the_existing_backend_factory(tmp_path) -> None:
    for payload_backend in ("vara_p2p",):
        config = StationConfig(
            callsign="OK7PS",
            payload_backend=payload_backend,
        )
        workers = WorkerPool(max_workers=1)
        operations = Operations(
            config,
            EventBus(),
            SnapshotStore(),
            workers,
            MessageStore(tmp_path / payload_backend),
            RouteTable(),
            HeardStations(),
        )
        try:
            assert operations.net.payload is not None
            assert operations.config.payload_backend == payload_backend
        finally:
            operations.close()
            workers.close(wait=True)


def test_connect_vara_starts_the_selected_local_variant(
    tmp_path, monkeypatch
) -> None:
    executable = tmp_path / "VARAFM.exe"
    executable.touch()
    config = StationConfig(
        callsign="NOCALL",
        vara_mode="FM",
        vara_fm_path=str(executable),
    )
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
    attempts = []
    launches = []

    def connect(*, timeout):
        attempts.append(timeout)
        if len(attempts) == 1:
            raise ConnectionRefusedError("not running")

    class Process:
        def poll(self):
            return None

    monkeypatch.setattr(operations.vara, "connect", connect)
    monkeypatch.setattr(
        "guardian.operations.subprocess.Popen",
        lambda *args, **kwargs: launches.append((args, kwargs)) or Process(),
    )
    monkeypatch.setattr("guardian.operations.time.sleep", lambda _seconds: None)
    try:
        assert operations.connect_vara()
        while workers.is_active("vara-control"):
            time.sleep(0.001)
        results = workers.drain()

        assert results[0].succeeded
        assert len(attempts) == 2
        assert launches[0][0][0] == [str(executable.resolve())]
    finally:
        operations.close()
        workers.close(wait=True)


def test_direct_route_qsy_happens_before_message_announcement(
    tmp_path, monkeypatch
) -> None:
    config = StationConfig(callsign="OK7PS", auto_qsy=True)
    workers = WorkerPool(max_workers=1)
    mailstore = MessageStore(tmp_path / "mail-qsy")
    message = MailMessage(
        msg_id=mailstore.next_id(config.callsign),
        source=config.callsign,
        final_dest="OK1AAA",
        subject="Direct",
        body="QSY first",
        created=time.time(),
        folder=Folder.OUTBOX,
        status=Status.QUEUED,
    )
    mailstore.add(message)
    operations = Operations(
        config,
        EventBus(),
        SnapshotStore(),
        workers,
        mailstore,
        RouteTable([Route("OK1AAA", "", freq_hz=145_550_000, mode="FM")]),
        HeardStations(),
    )
    events = []

    class Radio:
        def get_state(self):
            return type("State", (), {"frequency_hz": 145_500_000})()

        def set_frequency(self, value):
            events.append(("frequency", value))

        def set_mode(self, value):
            events.append(("mode", value))

        def close(self):
            pass

    class Transport:
        def stop(self):
            pass

    operations.radio = Radio()
    operations.audio_transport = Transport()
    monkeypatch.setattr(
        operations.net,
        "send_message",
        lambda **kwargs: events.append(("announce", kwargs["final_dest"])),
    )
    try:
        assert operations.send_queued(message.msg_id)
        assert events[:3] == [
            ("frequency", 145_550_000),
            ("mode", "FM"),
            ("announce", "OK1AAA"),
        ]
        assert operations._qsy_previous == 145_500_000
        operations._session_event(
            SimpleNamespace(
                source="OK7PS",
                msg_id=message.msg_id,
                direction="out",
                state=SessionState.CONFIRMED,
            ),
            "peer confirmed RECEIVED",
        )
        assert events[-1] == ("frequency", 145_500_000)
        assert operations._qsy_previous is None
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)
