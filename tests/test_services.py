import threading
import time

from guardian.services import (
    EventBus,
    LogLevel,
    MailboxSnapshot,
    RadioSnapshot,
    SnapshotStore,
    WorkerPool,
)


def test_event_bus_is_bounded_and_drained_in_order() -> None:
    bus = EventBus(history_limit=2)
    bus.publish("one")
    bus.publish("two", LogLevel.WARNING, source="radio")
    bus.publish("three")

    assert [event.message for event in bus.history()] == ["two", "three"]
    pending = bus.drain()
    assert [event.message for event in pending] == ["one", "two", "three"]
    assert pending[1].source == "radio"
    assert bus.drain() == []


def test_snapshot_store_replaces_immutable_sections_atomically() -> None:
    store = SnapshotStore()
    first = store.update(radio=RadioSnapshot(connected=True, name="test"))
    second = store.update(mailbox=MailboxSnapshot(inbox=3, unread=2))

    assert first.revision == 1
    assert second.revision == 2
    assert second.radio.connected
    assert second.mailbox.unread == 2
    assert store.read() is second


def test_worker_completion_runs_only_when_caller_drains() -> None:
    pool = WorkerPool(max_workers=1, thread_name_prefix="test-guardian")
    completed = threading.Event()
    release = threading.Event()
    callback_threads = []
    caller_thread = threading.get_ident()

    try:
        assert pool.submit(
            "operation",
            lambda: (release.wait(1), 42)[1],
            lambda result: (
                callback_threads.append((threading.get_ident(), result.value)),
                completed.set(),
            ),
        )
        assert not pool.submit("operation", lambda: 0)
        release.set()

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not callback_threads:
            pool.drain()
            time.sleep(0.005)

        assert completed.is_set()
        assert callback_threads == [(caller_thread, 42)]
    finally:
        pool.close(wait=True)


def test_worker_surfaces_operation_errors() -> None:
    pool = WorkerPool(max_workers=1)

    def fail():
        raise RuntimeError("offline")

    try:
        assert pool.submit("failure", fail)
        deadline = time.monotonic() + 2
        results = []
        while time.monotonic() < deadline and not results:
            results = pool.drain()
            time.sleep(0.005)
        assert isinstance(results[0].error, RuntimeError)
        assert not results[0].succeeded
    finally:
        pool.close(wait=True)
