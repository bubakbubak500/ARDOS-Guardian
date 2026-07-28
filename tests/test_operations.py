import time
from types import SimpleNamespace

from guardian.config import StationConfig
from guardian.message import Folder, MailMessage, MessageStore, Status
from guardian.operations import Operations
from guardian.protocol import FrameType
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


def _operations(tmp_path, **overrides) -> tuple[Operations, WorkerPool, MessageStore]:
    config = StationConfig(callsign="OK7PS", radio_backend="none", **overrides)
    workers = WorkerPool(max_workers=1)
    mailstore = MessageStore(tmp_path / "mail")
    operations = Operations(
        config,
        EventBus(),
        SnapshotStore(),
        workers,
        mailstore,
        RouteTable(),
        HeardStations(),
    )
    return operations, workers, mailstore


def _spy_transmissions(operations) -> list:
    """Capture what actually reaches the transport.

    Replacing an orchestrator method instead would create the attribute it is
    meant to exercise: 0.6.27 called a non-existent `send_beacon()` and the
    test passed anyway because it had just invented one.
    """
    sent = []
    transport = operations.net.transport
    original = transport.send

    def record(frame):
        sent.append(frame)
        return original(frame)

    transport.send = record
    return sent


def test_beacon_and_auto_delivery_stay_silent_without_a_control_channel(
    tmp_path,
) -> None:
    # Both behaviours key the radio with no operator asking. Neither may fire
    # while the control channel is down -- there is nothing to transmit on.
    operations, workers, mailstore = _operations(
        tmp_path, beacon_enabled=True, beacon_interval=15.0, auto_deliver=True
    )
    sent = _spy_transmissions(operations)
    operations.heard.record("OK1AAA", 1_000.0)
    mailstore.add(
        MailMessage(
            msg_id=1,
            source="OK7PS",
            final_dest="OK1AAA",
            body="waiting",
            folder=Folder.OUTBOX,
            status=Status.QUEUED,
        )
    )
    try:
        assert operations.audio_transport is None
        for _ in range(5):
            operations.tick()
        assert sent == []
        assert mailstore.get(1).status == Status.QUEUED
    finally:
        operations.close()
        workers.close(wait=True)


def test_the_beacon_switch_actually_beacons_on_its_interval(tmp_path) -> None:
    # beacon_enabled was a dead checkbox until 0.6.27: send_beacon() existed
    # but nothing ever called it.
    operations, workers, _ = _operations(
        tmp_path, beacon_enabled=True, beacon_interval=60.0
    )
    sent = _spy_transmissions(operations)
    operations.audio_transport = SimpleNamespace(pump=lambda: 0)
    try:
        operations._tick_beacon(1_000.0)
        assert len(sent) == 1
        assert sent[0].type is FrameType.BEACON
        assert sent[0].source == "OK7PS"

        operations._tick_beacon(1_030.0)      # inside the interval
        assert len(sent) == 1

        operations._tick_beacon(1_061.0)      # past it
        assert len(sent) == 2

        operations.config.beacon_enabled = False
        operations._tick_beacon(2_000.0)
        assert len(sent) == 2
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_auto_delivery_waits_for_the_next_hop_to_be_heard(tmp_path) -> None:
    operations, workers, mailstore = _operations(tmp_path, auto_deliver=True)
    attempts = []
    operations.send_queued = lambda msg_id: attempts.append(msg_id) or True
    operations.audio_transport = SimpleNamespace(pump=lambda: 0)
    for msg_id, dest, status in (
        (1, "OK1AAA", Status.QUEUED),
        (2, "OK9XXX", Status.QUEUED),
        (3, "OK1AAA", Status.FAILED),
    ):
        mailstore.add(
            MailMessage(
                msg_id=msg_id,
                source="OK7PS",
                final_dest=dest,
                body="waiting",
                folder=Folder.OUTBOX,
                status=status,
            )
        )
    try:
        # Nobody heard yet: nothing goes out.
        operations._tick_auto_deliver(1_000.0)
        assert attempts == []

        operations.heard.record("OK1AAA", 1_100.0)
        operations._tick_auto_deliver(1_100.0)
        assert attempts == [1]

        # #2's hop is still unheard and #3 failed, so the operator owns it.
        operations._tick_auto_deliver(1_200.0)
        assert attempts == [1]

        operations.config.auto_deliver = False
        operations.heard.record("OK9XXX", 1_300.0)
        operations._tick_auto_deliver(1_300.0)
        assert attempts == [1]
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_hf_bandwidth_is_only_sent_in_hf_mode(tmp_path) -> None:
    config = StationConfig(callsign="OK7PS", radio_backend="none")

    assert config.vara_hf_bandwidth == "BW2300"
    config.apply_vara_mode("HF")
    assert config.vara_mode == "HF"
    # FM has no bandwidth command in VARA's reference; the setting is HF-only.
    config.apply_vara_mode("FM")
    assert config.vara_hf_bandwidth == "BW2300"


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
