import asyncio
from dataclasses import replace
import json
from queue import Empty, Queue
import threading
import time
from types import SimpleNamespace as NS
import zipfile

import pytest

from guardian.guard_mesh import (
    GuardianStatus, MeshBleClient, MeshDevice, PROGRESS_PACKET, PROGRESS_UUID,
    PROTOCOL_UUID, PROTOCOL_V2, REQUEST_UUID, RESPONSE_UUID, STATUS_UUID, guardian_status,
)
from guardian.guard_mesh_rpc import Assembler, HEADER, MeshApi, fragments
from guardian.message import Attachment, Folder, MailMessage, MessageStore
from guardian.routing import HeardStations, RouteTable
from guardian.routing.route_table import Route
from guardian.services.snapshots import MailboxSnapshot, SnapshotStore, VaraSnapshot
from guardian.session import SessionState


@pytest.fixture
def runtime(tmp_path):
    store = MessageStore(tmp_path / "mail")
    snapshots = SnapshotStore()
    snapshots.update(mailbox=MailboxSnapshot(inbox=2, unread=1))
    radio = NS(snapshots=snapshots, net=NS(sessions={}, discovery=NS(routes=NS(routes=lambda now: []))),
               payload_active=lambda: False, ofdm_status=lambda: None)
    return NS(config=NS(callsign="OK7PS"), mailstore=store, snapshots=snapshots,
              radio_coordinator=NS(radios=[radio]), heard=HeardStations(), routes=RouteTable())


def req(api, op, **kwargs):
    return api.handle({"id": "r1", "op": op, **kwargs}, "test-device")


def queued(api, **kwargs):
    return req(api, "message.queue", **dict({"token": "7c830595-5a8b-469b-a1b1-ebca00000001",
                                             "to": "ok1abc", "subject": "Žluťoučký", "body": "Příliš žluťoučký kůň."}, **kwargs))


def test_utf8_fragment_roundtrip_and_fixed_vector():
    parts = list(fragments({"id": "1", "op": "status.get", "test": "Ž🙂" * 200}, 7))
    assert parts[0][:4] == b"\x01\x07\x00\x00"
    assert parts[-1][0] == 2 and all(len(part) <= 20 for part in parts)
    assembler = Assembler()
    results = [assembler.feed(part) for part in parts]
    assert all(result is None for result in results[:-1])
    assert results[-1][0] == 7 and results[-1][1]["test"] == "Ž🙂" * 200
    assert list(fragments({}, 4)) == [b"\x03\x04\x00\x00{}"]


@pytest.mark.parametrize("packet", [b"", b"\x00" * 4, b"\x00" * 21,
                                    HEADER.pack(4, 1, 0) + b"a", HEADER.pack(0, 1, 0) + b"a",
                                    HEADER.pack(3, 1, 1) + b"{}", HEADER.pack(3, 1, 0) + b"[]",
                                    HEADER.pack(3, 1, 0) + b"{bad}"])
def test_malformed_fragments_are_rejected(packet):
    with pytest.raises(ValueError):
        Assembler().feed(packet)


def test_fragment_timeout_order_duplicate_and_size():
    parts = list(fragments({"body": "x" * 100}, 1))
    for bad in (parts[0], parts[2]):
        a = Assembler()
        a.feed(parts[0], 0)
        with pytest.raises(ValueError):
            a.feed(bad, 1)
    a = Assembler()
    a.feed(parts[0], 0)
    with pytest.raises(ValueError):
        a.feed(parts[1], 31)
    a = Assembler()
    a.feed(HEADER.pack(1, 0, 0) + b"x" * 16, 0)
    with pytest.raises(ValueError):
        for index in range(1, 2050):
            a.feed(HEADER.pack(0, 0, index) + b"x" * 16, 0)


def test_contacts_merge_live_saved_and_discovered(runtime):
    runtime.routes.add(Route("OK1ABC", "OK2XYZ"))
    runtime.routes.add(Route("OK3DEF", "OK3DEF"))
    runtime.heard.record("OK1ABC", time.monotonic(), grid="JN99CS")
    runtime.heard.record("OLD", time.monotonic() - 5000)
    runtime.radio_coordinator.radios[0].net.discovery.routes.routes = lambda now: [
        NS(destination="OK4IND", next_hop="OK1ABC", approved=False)]
    api = MeshApi(runtime)
    result = req(api, "contacts.list", limit=1)["result"]
    assert result["items"][0]["live"] and result["items"][0]["saved"]
    assert result["next_offset"] == 1 and result["total"] == 3
    page2 = req(api, "contacts.list", offset=1, limit=2, revision=result["revision"])["result"]
    assert [row["callsign"] for row in page2["items"]] == ["OK3DEF", "OK4IND"]
    assert page2["items"][1]["approved"] is False
    assert req(api, "contacts.list", source="live")["result"]["total"] == 2
    runtime.routes.add(Route("NEW", "NEW"))
    assert req(api, "contacts.list", revision=result["revision"])["error"]["code"] == "list_changed"


def test_text_browsing_does_not_read_attachments_or_mark_read(runtime, monkeypatch):
    store = runtime.mailstore
    mail = MailMessage(123, "OK1ABC", "OK7PS", subject="Ahoj", body="Ž🙂abc" * 300,
                       attachments=[Attachment("photo.jpg", b"secret image")], folder=Folder.INBOX, read=False)
    store.add(mail)
    original_read = zipfile.ZipFile.read
    def only_body(bundle, name, *args, **kwargs):
        assert name == "body.txt"
        return original_read(bundle, name, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, "read", only_body)
    api = MeshApi(runtime)
    listing = req(api, "messages.list")["result"]
    assert listing["items"][0]["msg_id"] == 123
    first = req(api, "message.get", msg_id=123, limit=4)["result"]
    assert first["body"] == "Ž🙂ab" and first["next_offset"] == 4
    second = req(api, "message.get", msg_id=123, offset=4, revision=first["revision"])["result"]
    assert second["body"].startswith("cŽ🙂")
    assert store.unread(Folder.INBOX) == 1
    assert "attachments" not in first and "att" not in listing["items"][0]
    assert req(api, "message.get", msg_id=456)["error"]["code"] == "not_found"
    store.add(replace(mail, body="Changed"))
    assert req(api, "message.get", msg_id=123, revision=first["revision"])["error"]["code"] == "message_changed"


def test_queue_is_durable_idempotent_and_uses_guardian_identity(runtime):
    api = MeshApi(runtime)
    first = queued(api)
    assert first["ok"] and not first["result"]["duplicate"]
    mid = first["result"]["msg_id"]
    mail = runtime.mailstore.get(mid)
    assert (mail.source, mail.final_dest, mail.folder, mail.status) == ("OK7PS", "OK1ABC", "outbox", "queued")
    assert not mail.attachments and mail.subject == "Žluťoučký"
    # Token is local index metadata, never included in the RF message.
    with zipfile.ZipFile(runtime.mailstore.root / f"{mid}.bundle") as bundle:
        assert "remote_request" not in json.loads(bundle.read("manifest.json"))
    runtime.mailstore = MessageStore(runtime.mailstore.root)
    again = queued(api)
    assert again["result"] == {"msg_id": mid, "accepted": True, "duplicate": True}
    assert len(runtime.mailstore.list()) == 1
    assert queued(api, body="different")["error"]["code"] == "token_conflict"
    # Normal status/move/re-save must retain duplicate suppression.
    runtime.mailstore.set_status(mid, folder=Folder.SENT, status="delivered")
    runtime.mailstore.add(runtime.mailstore.get(mid))
    assert queued(api)["result"]["duplicate"]


@pytest.mark.parametrize("change", [{"to": "../x "}, {"to": "X" * 17}, {"body": " "},
                                   {"body": "x" * 4097}, {"subject": "x" * 257}, {"priority": True},
                                   {"priority": 4}, {"attachments": []}, {"source": "OTHER"},
                                   {"token": "not-a-uuid"}, {"body": None}])
def test_invalid_messages_never_enter_outbox(runtime, change):
    assert queued(MeshApi(runtime), **change)["error"]["code"] == "invalid_request"
    assert runtime.mailstore.list() == []


def test_api_rejects_unknown_commands_and_bad_pagination(runtime):
    api = MeshApi(runtime)
    assert req(api, "radio.connect")["error"]["code"] == "unknown_operation"
    assert req(api, "messages.list", limit=21)["error"]["code"] == "invalid_request"
    assert req(api, "message.get", msg_id="../etc")["error"]["code"] == "invalid_request"
    assert not api.handle({"id": [], "op": "status.get"}, "p")["ok"]
    runtime.config.callsign = "NOCALL"
    assert queued(api)["error"]["code"] == "station_not_configured"


def test_progress_uses_same_vara_numbers_as_main_window(runtime):
    radio = runtime.radio_coordinator.radios[0]
    radio.net.sessions = {1: NS(state=SessionState.TRANSFERRING)}
    radio.payload_active = lambda: True
    radio.snapshots.update(vara=VaraSnapshot(data_bytes_written=1000, tx_buffer_bytes=275, transfer_direction="send"))
    status = guardian_status(runtime)
    assert status.tx_percent == 72 and status.rx_percent is None
    assert PROGRESS_PACKET.unpack(status.encode_progress(7)) == (b"GP", 2, 72, 255, 7)
    radio.net.sessions[1].state = SessionState.RECEIVING
    radio.snapshots.update(vara=VaraSnapshot(transfer_direction="receive", rx_transfer_bytes=25, rx_transfer_total=100))
    assert guardian_status(runtime).rx_percent == 25
    radio.snapshots.update(vara=VaraSnapshot(transfer_direction="receive"))
    assert guardian_status(runtime).rx_percent is None


def test_sc_ftn_and_ardop_progress(runtime):
    radio = runtime.radio_coordinator.radios[0]
    radio.payload_active = lambda: True
    radio.net.sessions = {1: NS(state=SessionState.RECEIVING)}
    radio.ofdm_status = lambda: NS(state="receiving", direction="receive", total_bytes=200, rx_bytes=100)
    assert guardian_status(runtime).rx_percent == 50
    radio.net.sessions[1] = NS(state=SessionState.TRANSFERRING, payload_transport="ardop",
                                payload_bytes=b"a" * 100, payload_progress_bytes=40)
    assert guardian_status(runtime).tx_percent == 40


class V2Client:
    def __init__(self, device, **kwargs):
        self.disconnected_callback = kwargs["disconnected_callback"]
        self.services = NS(get_service=lambda uuid: NS(get_characteristic=self.characteristic))
        self.writes = []
        self.response_done = threading.Event()
    def characteristic(self, uuid):
        return NS(uuid=uuid, properties=["read"] if uuid == PROTOCOL_UUID else ["notify"] if uuid == REQUEST_UUID else ["write"])
    async def connect(self): pass
    async def disconnect(self): pass
    async def read_gatt_char(self, char): return PROTOCOL_V2
    async def start_notify(self, char, callback):
        assert char.uuid == REQUEST_UUID
        self.notify = callback
    async def write_gatt_char(self, char, data, response):
        assert response
        self.writes.append((char.uuid, bytes(data)))
        if char.uuid == RESPONSE_UUID and data[0] & 2:
            self.response_done.set()


def test_v2_gatt_request_response_and_late_request_guard(runtime):
    captured = []
    def factory(*args, **kwargs):
        value = V2Client(*args, **kwargs)
        captured.append(value)
        return value
    client = MeshBleClient(client_factory=factory)
    device = MeshDevice("Guard Mesh", "device", -40, object())
    client.publish(GuardianStatus(tx=True, tx_percent=30))
    assert client.connect(device)
    try:
        while client.events.get(timeout=3)[0] != "sent": pass
        fake = captured[0]
        for part in fragments({"id": "s1", "op": "status.get"}, 4):
            client._loop.call_soon_threadsafe(fake.notify, None, part)
        while True:
            kind, payload = client.events.get(timeout=3)
            if kind == "request": break
        session, tid, request, peer = payload
        assert client.session_active(session)
        assert client.reply(session, tid, MeshApi(runtime).handle(request, peer))
        assert fake.response_done.wait(5)
        assembler = Assembler()
        values = [assembler.feed(data) for uuid, data in fake.writes if uuid == RESPONSE_UUID]
        assert values[-1][1]["id"] == "s1" and values[-1][1]["ok"]
        assert {uuid for uuid, _ in fake.writes} >= {STATUS_UUID, PROGRESS_UUID, RESPONSE_UUID}
        fake.disconnected_callback(fake)
        assert not client.session_active(session)
        assert not client.reply(session, 4, {})
    finally:
        client.stop()
        client._thread.join(4)
        assert not client.busy


def test_disconnected_request_cannot_create_mail(runtime):
    from PySide6.QtWidgets import QApplication
    from guardian.qt.guard_mesh_dialog import GuardMeshPanel
    app = QApplication.instance() or QApplication([])
    events = Queue()
    fake = NS(events=events, busy=False, session_active=lambda _: False, stop=lambda: None)
    panel = GuardMeshPanel(client=fake, runtime=runtime)
    events.put(("request", (1, 0, {"id": "old", "op": "message.queue"}, "peer")))
    try:
        panel._poll()
        assert runtime.mailstore.list() == []
    finally:
        panel.shutdown()
