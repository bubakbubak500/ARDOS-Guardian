"""Guardian is the status source; LilyGO is a BLE display, never the inbox source."""
import asyncio
from dataclasses import replace
import os
from queue import Queue
import time
from types import SimpleNamespace as NS

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from guardian.guard_mesh import (
    GuardianStatus, MeshBleClient, MeshDevice, PROTOCOL_UUID, PROTOCOL_VERSION,
    SERVICE_UUID, STATUS_PACKET, STATUS_UUID, guardian_status,
)
from guardian.services.snapshots import (
    MailboxSnapshot, NetworkSnapshot, RadioSnapshot,
    SnapshotStore, VaraSnapshot,
)
from guardian.session import SessionState


def test_wire_packet_fits_default_ble_mtu_and_matches_firmware_vector():
    status = GuardianStatus(tx=True, rx=True, inbox=5, unread=2, outbox=1)
    assert status.encode(7) == bytes.fromhex(
        "47 4d 01 07 05 00 00 00 02 00 00 00 01 00 00 00 07 00 00 00"
    )
    assert STATUS_PACKET.size == 20
    assert STATUS_PACKET.unpack(status.encode(0x100000000))[-1] == 0


def test_wire_counts_are_bounded_and_flags_have_distinct_meanings():
    status = GuardianStatus(inbox=-1, unread=2**40, radio_connected=True,
                            vara_connected=True, control_active=True)
    assert STATUS_PACKET.unpack(status.encode(0)) == (b"GM", 1, 57, 0, 0xFFFFFFFF, 0, 0)


def radio(*states, connected=False, ptt=False):
    snapshots = SnapshotStore()
    snapshots.update(radio=RadioSnapshot(connected=connected, ptt=ptt),
                     vara=VaraSnapshot(command_connected=connected),
                     network=NetworkSnapshot(control_channel_active=connected))
    return NS(snapshots=snapshots, net=NS(sessions={i: NS(state=s) for i, s in enumerate(states)}))


def runtime(*radios):
    snapshots = SnapshotStore()
    snapshots.update(mailbox=MailboxSnapshot(inbox=7, unread=3, outbox=2))
    return NS(snapshots=snapshots, radio_coordinator=NS(radios=list(radios)))


@pytest.mark.parametrize("state,tx,rx", [
    (SessionState.IDLE, False, False),
    (SessionState.STARTING_VARA, True, False),
    (SessionState.TRANSFERRING, True, False),
    (SessionState.RECEIVING, False, True),
    (SessionState.CONFIRMED, False, False),
    (SessionState.FAILED, False, False),
    (SessionState.CANCELLED, False, False),
])
def test_transport_independent_message_phases(state, tx, rx):
    status = guardian_status(runtime(radio(state)))
    assert (status.tx, status.rx) == (tx, rx)
    assert (status.inbox, status.unread, status.outbox) == (7, 3, 2)


def test_dual_radios_combine_activity_but_do_not_double_shared_inbox():
    status = guardian_status(runtime(radio(SessionState.TRANSFERRING),
                                     radio(SessionState.RECEIVING, connected=True)))
    assert status.tx and status.rx and status.inbox == 7
    assert status.radio_connected and status.vara_connected and status.control_active
    assert len(status.transfers) == 2
    assert not guardian_status(runtime(radio(ptt=True))).tx


DEVICE = MeshDevice("LilyGO", "00:11:22:33:44:55", -45, object())


class FakeClient:
    def __init__(self, device, **kwargs):
        assert device is DEVICE.device
        assert kwargs["pair"] is True
        self.callback = kwargs["disconnected_callback"]
        self.writes = []
        self.disconnected = False
        self.version = PROTOCOL_VERSION
        self.fail_write = False
        self.services = NS(get_service=lambda uuid: NS(get_characteristic=self.characteristic))

    def characteristic(self, uuid):
        return NS(uuid=uuid, properties=["read"] if uuid == PROTOCOL_UUID else ["write"])

    async def connect(self):
        pass

    async def read_gatt_char(self, characteristic):
        assert characteristic.uuid == PROTOCOL_UUID
        return self.version

    async def write_gatt_char(self, characteristic, data, *, response):
        assert characteristic.uuid == STATUS_UUID
        assert response is True
        if self.fail_write:
            raise OSError("write failed")
        self.writes.append(bytes(data))

    async def disconnect(self):
        self.disconnected = True


def wait_event(client, wanted):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        kind, payload = client.events.get(timeout=4)
        if kind == wanted:
            return payload
    pytest.fail(f"Missing BLE event {wanted}")


def stop(client):
    client.stop()
    client._thread.join(4)
    assert not client.busy


def test_scan_filters_service_and_preserves_ble_device_object():
    async def discover(**kwargs):
        assert kwargs["return_adv"] and kwargs["service_uuids"] == [SERVICE_UUID]
        return {
            "mesh": (NS(name="LilyGO", address="mesh"), NS(local_name=None, rssi=-40, service_uuids=[SERVICE_UUID.upper()])),
            "other": (NS(name="Other", address="other"), NS(local_name=None, rssi=-20, service_uuids=[])),
        }
    client = MeshBleClient(scanner=NS(discover=discover))
    assert client.scan()
    devices = wait_event(client, "devices")
    stop(client)
    assert len(devices) == 1 and devices[0].address == "mesh"
    assert devices[0].device.name == "LilyGO"


def test_pair_write_changes_heartbeat_and_cancel(monkeypatch):
    monkeypatch.setattr("guardian.guard_mesh.HEARTBEAT_SECONDS", 0.05)
    fake = FakeClient(DEVICE.device, pair=True, disconnected_callback=lambda _: None)
    client = MeshBleClient(client_factory=lambda *args, **kwargs: fake)
    status = GuardianStatus(rx=True, inbox=9, unread=4)
    client.publish(status)
    try:
        assert client.connect(DEVICE)
        assert not client.scan()
        assert wait_event(client, "sent")[0] == status
        assert fake.writes[0] == status.encode(0)
        assert wait_event(client, "sent")[0] == status
        assert fake.writes[1] == status.encode(1)
        changed = replace(status, rx=False, tx=True, inbox=10)
        client.publish(changed)
        while wait_event(client, "sent")[0] != changed:
            pass
        assert fake.writes[-1][3] & 2
    finally:
        stop(client)
    assert fake.disconnected


@pytest.mark.parametrize("failure", ["version", "missing_service", "write", "stale", "expired"])
def test_incompatible_fw_failed_write_and_stale_source_disconnect(failure):
    fake = FakeClient(DEVICE.device, pair=True, disconnected_callback=lambda _: None)
    if failure == "version":
        fake.version = b"GM\x03\x00"
    if failure == "missing_service":
        fake.services = NS(get_service=lambda uuid: None)
    if failure == "write":
        fake.fail_write = True
    client = MeshBleClient(client_factory=lambda *args, **kwargs: fake)
    if failure != "stale":
        client.publish(GuardianStatus())
    if failure == "expired":
        client._status = (GuardianStatus(), time.monotonic() - 20)
    assert client.connect(DEVICE)
    error = wait_event(client, "error")
    stop(client)
    assert error and fake.disconnected and not fake.writes


def test_cancel_during_pairing_and_allow_retry():
    class PendingPair(FakeClient):
        async def connect(self):
            await asyncio.sleep(60)
    fake = PendingPair(DEVICE.device, pair=True, disconnected_callback=lambda _: None)
    client = MeshBleClient(client_factory=lambda *args, **kwargs: fake)
    assert client.connect(DEVICE)
    stop(client)
    assert client.connect(DEVICE)
    stop(client)


def test_remote_disconnect_clears_connection():
    captured = []
    def factory(*args, **kwargs):
        fake = FakeClient(*args, **kwargs)
        captured.append(fake)
        return fake
    client = MeshBleClient(client_factory=factory)
    client.publish(GuardianStatus())
    assert client.connect(DEVICE)
    wait_event(client, "sent")
    captured[0].callback(captured[0])
    wait_event(client, "disconnected")
    stop(client)


def test_settings_tab_reuses_connection_and_has_no_status_variables(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QLabel, QMenu
    from guardian.i18n import tr
    from guardian.qt.runtime import ShellRuntime
    from guardian.qt.shell import GuardianMainWindow
    from guardian.qt.settings_dialog import SettingsDialog
    app = QApplication.instance() or QApplication([])
    live = ShellRuntime()
    window = GuardianMainWindow(live, QSettings(str(tmp_path / "ble.ini"), QSettings.Format.IniFormat))
    panels = []
    def settings_exec(dialog):
        panel = dialog.guard_mesh_panel
        panels.append(panel)
        assert dialog.tabs.tabText(dialog.tabs.indexOf(dialog.guard_mesh_scroll)) == "Guard Mesh"
        text = " ".join(label.text() for label in panel.findChildren(QLabel))
        assert all(word not in text.lower() for word in ("lilygo", "liligo", "experimental", "inbox", "vara", "tx"))
        assert not hasattr(panel, "inbox")
        return 0
    monkeypatch.setattr(SettingsDialog, "exec", settings_exec)
    try:
        menu = next(menu for menu in window.menuBar().findChildren(QMenu) if menu.title() == tr("menu.tools"))
        assert not any("Guard Mesh" in action.text() for action in menu.actions())
        window._show_settings()
        panel = window.guard_mesh_panel
        assert panel.parent() is window and not panel.isVisible()
        published = []
        panel.client.publish = published.append
        window._refresh()
        assert published == [guardian_status(live)]
        window._show_settings()
        assert panels == [panel, panel]
    finally:
        window.close()
        live.close()
