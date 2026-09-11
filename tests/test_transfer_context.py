from guardian.config import StationConfig
from guardian.message import MessageStore
from guardian.operations import Operations
from guardian.routing import HeardStations, RouteTable
from guardian.services import EventBus, SnapshotStore, WorkerPool


def test_operations_snapshot_carries_and_clears_vara_transfer_context(tmp_path) -> None:
    workers = WorkerPool(max_workers=1)
    snapshots = SnapshotStore()
    operations = Operations(
        StationConfig(callsign="OK7PS", radio_backend="none"),
        EventBus(),
        snapshots,
        workers,
        MessageStore(tmp_path / "mail"),
        RouteTable(),
        HeardStations(),
    )
    try:
        operations.vara.set_transfer_context(
            "OK1AAA",
            "OK2BBB",
            "OK3CCC",
        )
        operations._update_vara_snapshot()
        context = snapshots.read().vara
        assert (
            context.transfer_source,
            context.transfer_destination,
            context.transfer_via,
        ) == ("OK1AAA", "OK2BBB", "OK3CCC")

        operations.vara.prepare_data_transfer()
        operations._update_vara_snapshot()
        context = snapshots.read().vara
        assert (
            context.transfer_source,
            context.transfer_destination,
            context.transfer_via,
        ) == ("", "", "")
    finally:
        operations.close()
        workers.close(wait=True)
