"""Publish Guardian PC status to a Guard Mesh BLE display (no Wi-Fi)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from queue import Empty, Full, Queue
from collections import deque
import struct
import threading
import time


SERVICE_UUID = "f3641400-b000-4042-ba50-05ca45bf8abc"
STATUS_UUID = "f3641401-b000-4042-ba50-05ca45bf8abc"
PROTOCOL_UUID = "f3641402-b000-4042-ba50-05ca45bf8abc"
PROTOCOL_VERSION = b"GM\x01\x00"
PROTOCOL_V2 = b"GM\x02\x00"
PROGRESS_UUID = "f3641403-b000-4042-ba50-05ca45bf8abc"
REQUEST_UUID = "f3641404-b000-4042-ba50-05ca45bf8abc"
RESPONSE_UUID = "f3641405-b000-4042-ba50-05ca45bf8abc"
PROGRESS_PACKET = struct.Struct("<2sBBBI")
STATUS_PACKET = struct.Struct("<2sBBIIII")
STALE_SECONDS = 15.0
HEARTBEAT_SECONDS = 5.0


@dataclass(frozen=True)
class RadioProgress:
    radio: int
    direction: str
    percent: int | None


@dataclass(frozen=True)
class GuardianStatus:
    tx: bool = False
    rx: bool = False
    inbox: int = 0
    unread: int = 0
    outbox: int = 0
    radio_connected: bool = False
    vara_connected: bool = False
    control_active: bool = False
    tx_percent: int | None = None
    rx_percent: int | None = None
    transfers: tuple[RadioProgress, ...] = ()

    def encode(self, sequence: int) -> bytes:
        flags = (1 | int(self.tx) << 1 | int(self.rx) << 2
                 | int(self.radio_connected) << 3 | int(self.vara_connected) << 4
                 | int(self.control_active) << 5)
        counts = (max(0, min(0xFFFFFFFF, int(value)))
                  for value in (self.inbox, self.unread, self.outbox))
        return STATUS_PACKET.pack(b"GM", 1, flags, *counts, sequence & 0xFFFFFFFF)

    def encode_progress(self, sequence: int) -> bytes:
        return PROGRESS_PACKET.pack(b"GP", 2,
                                    255 if self.tx_percent is None else self.tx_percent,
                                    255 if self.rx_percent is None else self.rx_percent,
                                    sequence & 0xFFFFFFFF)


def guardian_status(runtime) -> GuardianStatus:
    """Called on the shell thread: read shared inbox once, combine both radios.

    Session phases are transport-independent (VARA, SC-FTN and ARDOP).
    PTT/control beacons alone do not mean a message is being sent.
    """
    from .session import SessionState
    from .qt.transfer_progress import transfer_state

    mailbox = runtime.snapshots.read().mailbox
    tx = rx = radio_connected = vara_connected = control_active = False
    transfers = []
    for radio_id, radio in enumerate(runtime.radio_coordinator.radios, 1):
        snapshot = radio.snapshots.read()
        radio_connected |= snapshot.radio.connected
        vara_connected |= snapshot.vara.command_connected
        control_active |= snapshot.network.control_channel_active
        for message in tuple(radio.net.sessions.values()):
            tx |= message.state in (SessionState.STARTING_VARA, SessionState.TRANSFERRING)
            rx |= message.state == SessionState.RECEIVING
            if message.state not in (SessionState.STARTING_VARA, SessionState.TRANSFERRING, SessionState.RECEIVING):
                continue
            direction = "receive" if message.state == SessionState.RECEIVING else "send"
            percent = None
            if getattr(message, "payload_transport", "") == "ardop":
                if direction == "send":
                    payload = getattr(message, "payload_bytes", None)
                    total = len(payload) if payload is not None else len(getattr(message, "body", "").encode("utf-8"))
                else:
                    from .payload.ardop import HEADER
                    total = max(0, getattr(message, "payload_wire_size", 0) - HEADER.size)
                if total:
                    percent = round(max(0, min(1, getattr(message, "payload_progress_bytes", 0) / total)) * 100)
            else:
                state = transfer_state(snapshot, getattr(radio, "payload_active", lambda: False)(),
                                       getattr(radio, "ofdm_status", lambda: None)())
                if state.active and state.direction == direction and state.total_bytes > 0:
                    percent = round(state.fraction * 100)
            transfers.append(RadioProgress(radio_id, direction, percent))
    tx_percent = next((item.percent for item in transfers if item.direction == "send"), None)
    rx_percent = next((item.percent for item in transfers if item.direction == "receive"), None)
    return GuardianStatus(tx, rx, mailbox.inbox, mailbox.unread, mailbox.outbox,
                          radio_connected, vara_connected, control_active,
                          tx_percent, rx_percent, tuple(transfers))


@dataclass(frozen=True)
class MeshDevice:
    name: str
    address: str
    rssi: int
    device: object


class MeshBleClient:
    """One cancellable asyncio worker; the GUI consumes events on its own thread.

    Imports Bleak only on explicit user action. Retain BLEDevice objects from
    discovery to avoid implicit rescanning during pairing. OS owns the bond.
    """

    def __init__(self, *, scanner=None, client_factory=None):
        self.events: Queue = Queue()
        self._scanner = scanner
        self._client_factory = client_factory
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._loop = None
        self._task = None
        self._stop = threading.Event()
        self._status: tuple[GuardianStatus, float] | None = None
        self._responses = Queue(maxsize=4)
        self._generation = 0
        self._session = None

    def session_active(self, session):
        with self._lock:
            return self._session == session and not self._stop.is_set()

    def reply(self, session, transfer_id, response):
        if not self.session_active(session):
            return False
        try:
            self._responses.put_nowait((session, transfer_id, response))
            return True
        except Full:
            self.stop()
            return False

    def publish(self, status: GuardianStatus) -> None:
        with self._lock:
            self._status = (status, time.monotonic())

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def scan(self) -> bool:
        return self._start(self._scan)

    def connect(self, device: MeshDevice) -> bool:
        return self._start(lambda: self._connect(device))

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        with self._lock:
            if self._loop is not None and self._task is not None:
                self._loop.call_soon_threadsafe(self._task.cancel)

    def _start(self, operation) -> bool:
        if self.busy:
            return False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(operation,), name="guard-mesh-ble", daemon=True,
        )
        self._thread.start()
        return True

    def _run(self, operation) -> None:
        async def run():
            with self._lock:
                self._loop = asyncio.get_running_loop()
                self._task = asyncio.current_task()
            try:
                if not self._stop.is_set():
                    await operation()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self.events.put(("error", str(exc) or type(exc).__name__))
            finally:
                with self._lock:
                    self._loop = self._task = None
                self.events.put(("finished", None))

        asyncio.run(run())

    async def _scan(self) -> None:
        if self._scanner is None:
            from bleak import BleakScanner
            self._scanner = BleakScanner
        found = await self._scanner.discover(
            timeout=6.0, return_adv=True, service_uuids=[SERVICE_UUID],
        )
        devices = [
            MeshDevice(adv.local_name or device.name or "Guard Mesh", device.address, adv.rssi, device)
            for device, adv in found.values()
            if SERVICE_UUID in [uuid.lower() for uuid in adv.service_uuids]
        ]
        self.events.put(("devices", sorted(devices, key=lambda device: device.rssi, reverse=True)))

    async def _connect(self, device: MeshDevice) -> None:
        if self._client_factory is None:
            from bleak import BleakClient
            self._client_factory = BleakClient
        disconnected = asyncio.Event()
        loop = asyncio.get_running_loop()
        def on_disconnected(_client):
            with self._lock:
                self._session = None
            try:
                loop.call_soon_threadsafe(disconnected.set)
            except RuntimeError:
                pass  # Late OS callback after the worker loop has closed.
        client = self._client_factory(
            device.device, pair=True, timeout=60.0,
            disconnected_callback=on_disconnected,
        )
        from .guard_mesh_rpc import Assembler, ASSEMBLY_TIMEOUT, fragments
        assembler = Assembler()
        pending = {}
        outgoing = deque()
        protocol_error = []
        with self._lock:
            self._generation += 1
            session = self._generation
            self._session = session
            self._responses = Queue(maxsize=4)

        def request_received(_characteristic, data):
            try:
                value = assembler.feed(data)
                if value is not None:
                    tid, request = value
                    if tid in pending or len(pending) >= 4:
                        raise ValueError("Too many pending BLE requests or duplicate transfer ID")
                    pending[tid] = time.monotonic()
                    self.events.put(("request", (session, tid, request, device.address)))
            except (ValueError, RecursionError) as exc:
                with self._lock:
                    self._session = None
                protocol_error.append(str(exc))
                disconnected.set()
        try:
            await asyncio.wait_for(client.connect(), timeout=65.0)
            service = client.services.get_service(SERVICE_UUID)
            characteristic = service.get_characteristic(STATUS_UUID) if service else None
            protocol = service.get_characteristic(PROTOCOL_UUID) if service else None
            if (characteristic is None or "write" not in characteristic.properties
                    or protocol is None or "read" not in protocol.properties):
                raise ValueError("Guard Mesh firmware must provide Guardian status Write and protocol Read characteristics")
            version = await asyncio.wait_for(client.read_gatt_char(protocol), timeout=10.0)
            if bytes(version) not in (PROTOCOL_VERSION, PROTOCOL_V2):
                raise ValueError("Unsupported Guard Mesh protocol version")
            progress = response_char = None
            if bytes(version) == PROTOCOL_V2:
                progress = service.get_characteristic(PROGRESS_UUID)
                request_char = service.get_characteristic(REQUEST_UUID)
                response_char = service.get_characteristic(RESPONSE_UUID)
                if (progress is None or "write" not in progress.properties
                        or request_char is None or "notify" not in request_char.properties
                        or response_char is None or "write" not in response_char.properties):
                    raise ValueError("Incomplete Guard Mesh v2 service")
                await asyncio.wait_for(client.start_notify(request_char, request_received), 10.0)
            self.events.put(("connected", device))
            previous = None
            last_sent = 0.0
            sequence = 0
            while not disconnected.is_set():
                now = time.monotonic()
                with self._lock:
                    current = self._status
                if current is None or now - current[1] >= STALE_SECONDS:
                    raise RuntimeError("Guardian status is unavailable or stale; BLE disconnected")
                status = current[0]
                if status != previous or now - last_sent >= HEARTBEAT_SECONDS:
                    await asyncio.wait_for(client.write_gatt_char(
                        characteristic, status.encode(sequence), response=True,
                    ), timeout=10.0)
                    if progress is not None:
                        await asyncio.wait_for(client.write_gatt_char(
                            progress, status.encode_progress(sequence), response=True), 10.0)
                    last_sent = time.monotonic()
                    self.events.put(("sent", (status, last_sent)))
                    previous = status
                    sequence = (sequence + 1) & 0xFFFFFFFF
                if assembler.transfer_id is not None and now - assembler.started > ASSEMBLY_TIMEOUT:
                    raise ValueError("BLE request assembly timed out")
                if any(now - started > 60 for started in pending.values()):
                    raise ValueError("BLE request response timed out")
                while True:
                    try:
                        reply_session, tid, response = self._responses.get_nowait()
                    except Empty:
                        break
                    if reply_session == session and tid in pending:
                        outgoing.append((tid, iter(fragments(response, tid))))
                # Interleave long replies with status/heartbeat, preserving JSON order.
                for _ in range(8):
                    if not outgoing or disconnected.is_set():
                        break
                    tid, parts = outgoing[0]
                    part = next(parts, None)
                    if part is None:
                        outgoing.popleft()
                        pending.pop(tid, None)
                        continue
                    if part[0] & 2:
                        # The peer may reuse this ID as soon as it sees the final
                        # fragment, even before WinRT completes our write await.
                        outgoing.popleft()
                        pending.pop(tid, None)
                    await asyncio.wait_for(client.write_gatt_char(response_char, part, response=True), 10.0)
                try:
                    await asyncio.wait_for(disconnected.wait(), timeout=0.01 if outgoing else 0.1)
                except asyncio.TimeoutError:
                    pass
            if protocol_error:
                raise ValueError(protocol_error[0])
        finally:
            with self._lock:
                self._session = None
            try:
                await asyncio.wait_for(client.disconnect(), timeout=10.0)
            finally:
                self.events.put(("disconnected", None))
