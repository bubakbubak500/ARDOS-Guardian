import time

from guardian.config import StationConfig
from guardian.message import Folder, MailMessage, MessageStore, Status
from guardian.operations import Operations
from guardian.routing import HeardStations, RouteTable
from guardian.services import EventBus, SnapshotStore, WorkerPool


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
    for payload_backend in ("vara_p2p", "winlink_manual"):
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
