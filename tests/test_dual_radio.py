"""Two independent channels, one mailbox, including cross-band delivery receipts."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from guardian.config import StationConfig
from guardian.message import Folder, MailMessage, MessageStore, Status
from guardian.multi_radio import RadioCoordinator
from guardian.operations import Operations
from guardian.protocol import ControlFrame, FrameType
from guardian.routing import HeardStations, RouteTable
from guardian.services import EventBus, SnapshotStore, WorkerPool
from guardian.session import LoopbackBus, Orchestrator, SessionState


@pytest.fixture
def station(tmp_path):
    config = StationConfig(callsign="OK7PS", dual_radio_enabled=True,
                           manual_frequency_hz=145_500_000, auto_relay=True)
    second = config.second_radio_config()
    second.manual_frequency_hz = 433_500_000
    store = MessageStore(tmp_path / "mail")
    radios = []
    for cfg in (config, second):
        cfg._save_disabled = True
        radio = Operations(cfg, EventBus(), SnapshotStore(), WorkerPool(),
                           store, RouteTable(), HeardStations())
        radios.append(radio)
    coordinator = RadioCoordinator(radios[0])
    coordinator.attach(radios[1], 2)
    frames = [[], []]
    buses = [LoopbackBus(lambda _, f: frames[0].append(f)),
             LoopbackBus(lambda _, f: frames[1].append(f))]
    for radio, bus in zip(radios, buses):
        from types import SimpleNamespace
        radio.vara._cmd = SimpleNamespace(shutdown=lambda *_: None, close=lambda: None)
        radio.vara._data = SimpleNamespace(shutdown=lambda *_: None, close=lambda: None)
        radio.vara.state.cmd_connected = radio.vara.state.data_connected = True
        transport = bus.endpoint("bridge")
        # Loopback channel runs the real Orchestrator and Operations callbacks.
        radio.audio_transport = transport
        radio.net = radio._build_net(transport)
        radio.net._now = time.monotonic()
        radio.net.working_channel_offer = None
        radio.net.working_channel_accept = None
    yield coordinator, radios, buses, frames
    for radio in radios:
        radio.audio_transport = None
        radio.close()
        radio.workers.close(wait=True)


def mail(store, destination="OK1BBB", message_id=101):
    value = MailMessage(msg_id=message_id, source="OK7PS", final_dest=destination,
                        subject="Across bands", body="Shared mailbox", created=time.time(),
                        folder=Folder.OUTBOX, status=Status.QUEUED)
    store.add(value)
    return value


def heard(radio, callsign):
    radio.heard.record(callsign, time.monotonic(), freq_hz=radio.current_frequency())


def test_second_profile_round_trip_and_independent_ports(tmp_path):
    cfg = StationConfig(callsign="OK7PS", radio_backend="vox", cat_port="COM7")
    second = cfg.second_radio_config()
    assert second.callsign == cfg.callsign
    assert second.vara_cmd_port != cfg.vara_cmd_port
    assert second.rigctld_port != cfg.rigctld_port
    second.cat_port = "COM8"
    second.manual_frequency_hz = 433_500_000
    cfg.second_radio = second.radio_channel_profile()
    cfg.dual_radio_enabled = True
    cfg.save(tmp_path / "config.json")
    loaded = StationConfig.load(tmp_path / "config.json")
    assert loaded.dual_radio_enabled
    assert loaded.second_radio_config().cat_port == "COM8"
    assert loaded.cat_port == "COM7"
    assert loaded.second_radio_config().manual_frequency_hz == 433_500_000


def test_outgoing_mail_uses_second_radio_and_cannot_start_twice(station):
    coordinator, radios, buses, frames = station
    heard(radios[1], "OK1BBB")
    value = mail(coordinator.mailstore)
    assert radios[0].send_queued(value.msg_id)
    assert value.msg_id not in radios[0].net.sessions
    assert radios[1].net.sessions[value.msg_id].next_hop == "OK1BBB"
    assert not radios[0].send_queued(value.msg_id)
    for bus in buses:
        bus.pump()
    assert not frames[0]
    assert frames[1][0].type == FrameType.HAVE_MSG


def test_automatic_delivery_and_duplicate_heard_rows(station):
    coordinator, radios, _, _ = station
    for radio in radios:
        heard(radio, "OK1BBB")
    assert {s.radio_id for s in coordinator.heard.active(time.monotonic())} == {1, 2}
    value = mail(coordinator.mailstore)
    coordinator.tick()
    assert sum(value.msg_id in r.net.sessions for r in radios) == 1


def test_retuned_or_stale_heard_station_does_not_choose_wrong_radio(station):
    coordinator, radios, _, _ = station
    heard(radios[1], "OK1BBB")
    radios[1].config.manual_frequency_hz = 435_000_000
    assert coordinator.select("OK1BBB") is None
    radios[1].config.manual_frequency_hz = 433_500_000
    radios[1].heard.get("OK1BBB").last_heard -= 2000
    assert coordinator.select("OK1BBB") is None


class PayloadWire:
    """Transfer complete real mail bundles across one simulated RF band."""
    def __init__(self):
        self.pending = {}
        self.waiting = {}

    def start_send(self, msg, done):
        self.pending[msg.msg_id] = (msg.payload_bytes, done)
        self.finish(msg.msg_id)

    def start_receive(self, msg, done):
        self.waiting[msg.msg_id] = (msg, done)
        self.finish(msg.msg_id)

    def finish(self, message_id):
        if message_id in self.pending and message_id in self.waiting:
            body, sent = self.pending.pop(message_id)
            msg, received = self.waiting.pop(message_id)
            msg.payload_bytes = body
            sent(True)
            received(True)

    def cancel(self, msg):
        pass


@pytest.mark.parametrize("ingress", [0, 1])
def test_complete_cross_band_relay_and_return_receipt(station, ingress):
    coordinator, radios, buses, frames = station
    egress = 1 - ingress
    sender = Orchestrator("OK1AAA", buses[ingress].endpoint("sender"), auto_route=False)
    received = []
    destination = Orchestrator("OK1BBB", buses[egress].endpoint("destination"),
        on_event=lambda msg, _: received.append(msg.payload_bytes) if msg.state == SessionState.DELIVERED else None)
    first, second = PayloadWire(), PayloadWire()
    sender.payload = radios[ingress].net.payload = first
    destination.payload = radios[egress].net.payload = second
    heard(radios[egress], "OK1BBB")
    value = MailMessage(msg_id=501, source="OK1AAA", final_dest="OK1BBB", subject="Cross band",
                        body="Real bundle through both radios", created=time.time())
    outgoing = sender.send_message("OK1BBB", value.subject, 501, next_hop="OK7PS",
                                   payload_bytes=value.to_bundle(), ttl=5)
    for _ in range(15):
        for bus in buses:
            bus.pump()
        coordinator.tick()
    assert outgoing.state == SessionState.DELIVERED
    assert received and MailMessage.from_bundle(received[-1]).body == value.body
    stored = coordinator.mailstore.get(501)
    assert stored.status == Status.DELIVERED
    assert stored.folder == Folder.SENT
    assert radios[egress].net.sessions[501].ttl == 4
    assert coordinator.mailstore.radio_path(501)["inbound_radio"] == ingress + 1
    assert any(f.type == FrameType.DELIVERED and f.next_hop == "OK1AAA" for f in frames[ingress])
    assert not any(f.type == FrameType.DELIVERED and f.next_hop == "OK1AAA" for f in frames[egress])


def test_mail_deletion_and_settings_guard_include_radio_two(station):
    coordinator, radios, _, _ = station
    value = mail(coordinator.mailstore)
    heard(radios[1], "OK1BBB")
    assert radios[0].send_queued(value.msg_id)
    assert coordinator.busy()
    assert radios[0]._mail_delete_block_reason([value.msg_id])


def test_bridge_answers_route_query_for_other_band(station):
    _, radios, buses, frames = station
    heard(radios[1], "OK1BBB")
    radios[0].net._rx_route_query(ControlFrame(type=FrameType.ROUTE_QUERY,
        source="OK1AAA", destination="OK1BBB", message_id=123))
    buses[0].pump()
    assert frames[0][-1].type == FrameType.ROUTE_OFFER


def test_receipt_waits_for_ingress_and_survives_restart(station):
    coordinator, radios, buses, frames = station
    mail(coordinator.mailstore)
    coordinator.mailstore.radio_path(101, inbound_radio=1, previous_hop="OK1AAA", ttl=3)
    frame = ControlFrame(type=FrameType.DELIVERED, source="OK7PS", destination="OK1BBB",
                         next_hop="OK1AAA", message_id=101)
    transport = radios[0].audio_transport
    radios[0].audio_transport = None
    coordinator.transmit_receipt(radios[1], frame)
    coordinator.tick()
    assert coordinator.mailstore.radio_path(101)["pending_receipt"]
    restored = RadioCoordinator(radios[0])
    restored.attach(radios[1], 2)
    radios[0].audio_transport = transport
    restored.tick()
    buses[0].pump()
    assert frames[0][-1].type == FrameType.DELIVERED
    assert not coordinator.mailstore.radio_path(101)["pending_receipt"]


def test_dual_radio_settings_copy_save_and_validate_ports(tmp_path):
    from PySide6.QtWidgets import QApplication
    from guardian.qt.settings_dialog import SettingsDialog
    from guardian.qt.theme import ThemePreference
    app = QApplication.instance() or QApplication([])
    cfg = StationConfig(callsign="OK7PS", manual_frequency_hz=145_500_000)
    cfg._save_disabled = True
    dialog = SettingsDialog(cfg, ThemePreference.SYSTEM)
    try:
        dialog.dual_radio_enabled.setChecked(True)
        second = dialog.second_editor
        assert second is not None
        second.manual_frequency.setValue(433_500_000)
        assert dialog.apply(), dialog.error.text()
        assert cfg.dual_radio_enabled
        assert cfg.second_radio_config().manual_frequency_hz == 433_500_000
        second.vara_fm_cmd.setValue(dialog.vara_fm_cmd.value())
        assert not dialog.apply()
        assert "TCP" in dialog.error.text()
        second.vara_fm_cmd.setValue(8400)
        second.manual_frequency.setValue(0)
        assert not dialog.apply()
        dialog.dual_radio_enabled.setChecked(False)
        assert dialog.apply()
        assert cfg.second_radio["manual_frequency_hz"] == 433_500_000
    finally:
        dialog.close()


def test_assisted_discovery_finds_gateway_on_other_band(station):
    coordinator, radios, buses, frames = station
    heard(radios[1], "OK1BBB")
    source = Orchestrator("OK1AAA", buses[0].endpoint("sender"),
        discovery_mode="assisted", discovery_auto_use=True)
    now = time.monotonic()
    source.tick(now)
    radios[0].net.tick(now)
    query = source.discover_route("OK1BBB")
    for step in range(12):
        now += 1
        source.tick(now)
        radios[0].net.tick(now)
        buses[0].pump()
    route = source.discovery.routes.best("OK1BBB", now)
    assert route is not None
    assert route.next_hop == "OK7PS"
    assert any(f.type == FrameType.MULTIHOP_RREP for f in frames[0])


def test_transit_ttl_survives_restart_and_expired_mail_stays_queued(station):
    coordinator, radios, _, _ = station
    heard(radios[1], "OK1BBB")
    value = mail(coordinator.mailstore)
    coordinator.mailstore.set_status(value.msg_id, folder=Folder.TRANSIT, status=Status.WAITING_PICKUP)
    coordinator.mailstore.radio_path(value.msg_id, inbound_radio=1, previous_hop="OK1AAA", ttl=0)
    coordinator.tick()
    assert not radios[1].net.sessions
    assert not coordinator.send_queued(value.msg_id)
    coordinator.mailstore.radio_path(value.msg_id, ttl=2)
    fresh = RadioCoordinator(radios[0])
    fresh.attach(radios[1], 2)
    fresh.tick()
    assert radios[1].net.sessions[value.msg_id].ttl == 2
    assert radios[1].net.sessions[value.msg_id].previous_hop == "OK1AAA"


def test_two_independent_outgoing_transfers_can_run_together(station):
    coordinator, radios, _, _ = station
    heard(radios[0], "OK1AAA")
    heard(radios[1], "OK1BBB")
    first = mail(coordinator.mailstore, "OK1AAA", 101)
    second = mail(coordinator.mailstore, "OK1BBB", 102)
    coordinator.tick()
    assert first.msg_id in radios[0].net.sessions
    assert second.msg_id in radios[1].net.sessions
    assert radios[0].vara is not radios[1].vara
    assert radios[0].radio is not radios[1].radio
    assert radios[0].workers is not radios[1].workers


def test_two_vara_tcp_pairs_keep_data_and_ptt_separate(station):
    import socket
    from contextlib import ExitStack
    import threading
    coordinator, radios, _, _ = station
    keyed = [threading.Event(), threading.Event()]
    with ExitStack() as stack:
        listeners = []
        for _ in range(4):
            listener = stack.enter_context(socket.socket())
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(2)
            listeners.append(listener)
        for index, radio in enumerate(radios):
            radio.vara.disconnect()
            radio.radio.set_ptt = lambda enabled, index=index: keyed[index].set() if enabled else None
            radio.vara.cmd_port = listeners[index * 2].getsockname()[1]
            radio.vara.data_port = listeners[index * 2 + 1].getsockname()[1]
            radio.vara.connect(timeout=1)
        peers = [stack.enter_context(listener.accept()[0]) for listener in listeners]
        try:
            peers[2].sendall(b"PTT ON\r")
            assert keyed[1].wait(2)
            assert not keyed[0].is_set()
            peers[0].sendall(b"PTT ON\r")
            assert keyed[0].wait(2)
            radios[0].vara._data.sendall(b"first-band")
            radios[1].vara._data.sendall(b"second-band")
            assert peers[1].recv(10) == b"first-band"
            assert peers[3].recv(11) == b"second-band"
        finally:
            for radio in radios:
                radio.vara.disconnect()


def test_hamlib_frequency_poll_keeps_each_radio_snapshot(station):
    from guardian.radio.base import RadioState
    coordinator, radios, _, _ = station
    for index, radio in enumerate(radios):
        radio.config.radio_backend = "hamlib"
        radio.config.rig_model = 3073
        frequency = (145_500_000, 7_100_000)[index]
        radio.radio.get_state = lambda frequency=frequency: RadioState(connected=True, frequency_hz=frequency)
        assert radio.request_radio_poll(force=True)
    for radio in radios:
        radio.workers.close(wait=True)
        radio.workers.drain()
    assert radios[0].current_frequency() == 145_500_000
    assert radios[1].current_frequency() == 7_100_000


def test_second_config_save_preserves_station_identity(tmp_path):
    cfg = StationConfig(callsign="OK7PS", manual_frequency_hz=145_500_000)
    child = cfg.second_radio_config()
    child._station_owner = cfg
    child.callsign = "SHOULDNOTREPLACE"
    child.manual_frequency_hz = 433_500_000
    child.save(tmp_path / "station.json")
    loaded = StationConfig.load(tmp_path / "station.json")
    assert loaded.callsign == "OK7PS"
    assert loaded.manual_frequency_hz == 145_500_000
    assert loaded.second_radio_config().manual_frequency_hz == 433_500_000


def test_disconnected_vara_keeps_mail_queued_on_correct_radio(station):
    coordinator, radios, _, _ = station
    heard(radios[1], "OK1BBB")
    value = mail(coordinator.mailstore)
    radios[1].vara.disconnect()
    coordinator.tick()
    assert not radios[0].net.sessions and not radios[1].net.sessions
    assert coordinator.mailstore.get(value.msg_id).status == Status.QUEUED


def test_runtime_enable_reconfigure_disable_keeps_shared_mailbox(tmp_path, monkeypatch):
    from guardian.qt.runtime import ShellRuntime
    cfg = StationConfig(callsign="OK7PS", dual_radio_enabled=True, manual_frequency_hz=145_500_000)
    cfg._save_disabled = True
    second = cfg.second_radio_config()
    second.manual_frequency_hz = 433_500_000
    cfg.second_radio = second.radio_channel_profile()
    monkeypatch.setattr(StationConfig, "load", classmethod(lambda cls, *args: cfg))
    runtime = ShellRuntime()
    try:
        first, old = runtime.radio_coordinator.radios
        assert first.mailstore is old.mailstore is runtime.mailstore
        assert first.snapshots is not old.snapshots
        old.heard.record("OK1BBB", time.monotonic(), freq_hz=433_500_000)
        assert runtime.heard.get("OK1BBB").radio_id == 2
        cfg.operator_name = "Shared operator"
        runtime.configure_second_radio()
        assert runtime.radio_coordinator.radios[1] is old
        assert old.config.operator_name == "Shared operator"
        cfg.second_radio["vara_cmd_port"] = 8500
        runtime.configure_second_radio()
        current = runtime.radio_coordinator.radios[1]
        assert current.vara.cmd_port == 8500
        assert old._closing.is_set()
        assert not first._closing.is_set()
        assert current.mailstore is first.mailstore
        cfg.dual_radio_enabled = False
        runtime.configure_second_radio()
        assert runtime.radio_coordinator.radios == [first]
        assert runtime.heard is first.heard
        assert cfg.second_radio["vara_cmd_port"] == 8500
    finally:
        runtime.close()


def test_dual_radio_settings_fit_small_work_area(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QRect, QPoint
    from guardian.qt import window_geometry
    from guardian.qt.settings_dialog import SettingsDialog
    from guardian.qt.theme import ThemePreference
    app = QApplication.instance() or QApplication([])
    area = QRect(0, 0, 911, 472)
    monkeypatch.setattr(window_geometry, "available_screen_geometry", lambda *_: QRect(area))
    dialog = SettingsDialog(StationConfig(dual_radio_enabled=True), ThemePreference.SYSTEM)
    try:
        dialog.tabs.setCurrentIndex(dialog.tabs.count() - 1)
        dialog.show()
        for _ in range(4):
            app.processEvents()
        assert area.contains(dialog.frameGeometry())
        for button in dialog.buttons.buttons():
            assert area.contains(QRect(button.mapToGlobal(QPoint()), button.size()))
        second = dialog.second_editor
        second.tabs.setCurrentIndex(3)
        app.processEvents()
        scroll = second.tabs.currentWidget()
        scroll.ensureWidgetVisible(second.vara_fm_cmd)
        app.processEvents()
        assert scroll.viewport().rect().contains(second.vara_fm_cmd.mapTo(scroll.viewport(), QPoint()))
    finally:
        dialog.close()


def test_notification_sound_never_uses_second_radios_output(monkeypatch):
    from guardian.qt.notifications import SoundPlayer
    cfg = StationConfig(dual_radio_enabled=True, audio_output="PC Speakers")
    second = cfg.second_radio_config()
    second.audio_output = "AIOC TX"
    cfg.second_radio = second.radio_channel_profile()
    monkeypatch.setattr("guardian.qt.notifications.default_output_device", lambda: "AIOC TX")
    assert SoundPlayer(cfg)._blocked()


def test_single_radio_does_not_advertise_itself_as_a_cross_band_gateway(station):
    coordinator, radios, _, _ = station
    heard(radios[0], "OK1AAA")
    coordinator.radios.remove(radios[1])
    assert not radios[0]._bridge_reachable("OK1AAA")


def test_mailbox_maintenance_does_not_block_the_protocol_tick(station):
    import threading
    coordinator, radios, _, _ = station
    value = mail(coordinator.mailstore)
    locked = threading.Event()
    release = threading.Event()
    def maintenance():
        with radios[0]._mail_mutation_lock:
            locked.set()
            release.wait(2)
    worker = threading.Thread(target=maintenance)
    worker.start()
    try:
        assert locked.wait(1)
        start = time.monotonic()
        assert not coordinator.send_queued(value.msg_id)
        assert time.monotonic() - start < 0.5
    finally:
        release.set()
        worker.join(2)
