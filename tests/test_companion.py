from __future__ import annotations

import http.cookiejar
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

import pytest

from guardian.companion.controller import CompanionController, CompanionError
from guardian.companion.hotspot import suggested_credentials
from guardian.message import Folder, MessageStore
from guardian.services import EventBus, MailboxSnapshot, SnapshotStore


@dataclass
class _Config:
    callsign: str = "OK7PS"


class _Operations:
    def __init__(self) -> None:
        self.sent: list[int] = []
        self.allow_send = True

    def send_queued(self, message_id: int) -> bool:
        self.sent.append(message_id)
        return self.allow_send


class _Runtime:
    def __init__(self, root) -> None:
        self.config = _Config()
        self.events = EventBus()
        self.snapshots = SnapshotStore()
        self.mailstore = MessageStore(root / "mail")
        self.operations = _Operations()

    def refresh(self) -> None:
        counts = self.mailstore.counts()
        self.snapshots.update(
            mailbox=MailboxSnapshot(
                inbox=counts.get(Folder.INBOX, 0),
                unread=self.mailstore.unread(Folder.INBOX),
                outbox=counts.get(Folder.OUTBOX, 0),
                transit=counts.get(Folder.TRANSIT, 0),
            )
        )


def _submit(controller, name, payload):
    box = {}

    def work():
        try:
            box["value"] = controller.submit(name, payload, timeout=2)
        except Exception as exc:  # asserted by caller
            box["error"] = exc

    thread = threading.Thread(target=work)
    thread.start()
    for _ in range(20):
        controller.poll()
        if not thread.is_alive():
            break
        time.sleep(0.01)
    thread.join(1)
    return box


@pytest.fixture
def controller(tmp_path):
    value = CompanionController(_Runtime(tmp_path), notes_path=tmp_path / "notes.json")
    yield value
    value.stop()


def test_pairing_is_one_use_and_session_can_be_revoked(controller):
    token = controller.new_pairing()
    session = controller.pair(token, name=" Field   iPhone ", address="192.168.137.2")
    assert controller.authenticate(session, address="192.168.137.2")
    assert controller.clients()[0]["name"] == "Field iPhone"
    with pytest.raises(CompanionError, match="invalid or has expired"):
        controller.pair(token, name="Other", address="192.168.137.3")
    controller.revoke_all()
    assert not controller.authenticate(session)


def test_phone_can_queue_but_not_transmit_routine_mail(controller):
    result = _submit(
        controller,
        "compose",
        {
            "destination": "ok1abc",
            "subject": "Status",
            "body": "All well",
            "priority": 0,
            "transmit": False,
        },
    )
    assert "error" not in result
    message = controller.runtime.mailstore.get(result["value"]["id"])
    assert message is not None
    assert message.final_dest == "OK1ABC"
    assert message.folder == Folder.OUTBOX
    assert controller.runtime.operations.sent == []


def test_immediate_emergency_requires_ephemeral_desktop_arm(controller):
    payload = {
        "destination": "OK1SOS",
        "subject": "MAYDAY",
        "body": "Need assistance",
        "priority": 3,
        "transmit": True,
    }
    denied = _submit(controller, "compose", payload)
    assert isinstance(denied["error"], CompanionError)
    assert "not armed" in str(denied["error"])

    controller.remote_emergency_armed = True
    accepted = _submit(controller, "compose", payload)
    assert accepted["value"]["transmit_started"] is True
    assert controller.runtime.operations.sent == [accepted["value"]["id"]]


def test_field_notebook_and_return_timer_are_local(controller, tmp_path):
    note = _submit(controller, "save_note", {"text": "Check relay battery"})
    assert note["value"]["note"]["text"] == "Check relay battery"
    assert json.loads((tmp_path / "notes.json").read_text(encoding="utf-8"))["notes"]

    timer = _submit(controller, "start_checkin", {"minutes": 5})
    assert timer["value"]["checkin"]["deadline"] > time.time()
    cleared = _submit(controller, "clear_checkin", {})
    assert cleared["value"]["ok"] is True


def test_http_server_serves_shell_pairs_and_rejects_unpaired_state(controller):
    port = controller.start(host="127.0.0.1", port=0)
    base = f"http://127.0.0.1:{port}"
    with urllib.request.urlopen(base + "/", timeout=2) as response:
        assert b"Guardian Companion" in response.read()
    with pytest.raises(urllib.error.HTTPError) as denied:
        urllib.request.urlopen(base + "/api/state?after=-1", timeout=2)
    assert denied.value.code == 401

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    request = urllib.request.Request(
        base + "/api/pair",
        json.dumps({"token": controller.pairing_fragment(), "name": "Android"}).encode(),
        {"Content-Type": "application/json"},
    )
    assert json.loads(opener.open(request, timeout=2).read())["ok"]
    controller.poll()
    state = json.loads(opener.open(base + "/api/state?after=-1", timeout=2).read())
    assert state["station"] == "OK7PS"
    assert state["version"] == "2.3.5"


def test_wifi_direct_credentials_are_valid_and_station_specific():
    ssid, password = suggested_credentials("ok7ps/p")
    assert ssid == "Guardian-OK7PSP"
    assert len(password) == 14
    assert password.isalnum()
