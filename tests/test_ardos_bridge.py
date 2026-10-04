"""Real RF session callbacks around an asynchronous server bridge."""
import threading
import time

import pytest

from guardian.ardos_cz.client import ServerError
from guardian.ardos_cz.service import ArdosService
from guardian.message import Folder, MailMessage, MessageStore, Status
from guardian.protocol import FrameType
from guardian.routing import Route
from guardian.session import Message, Orchestrator, SessionState
from test_ardos_cz import FakeClient, drain
from test_dual_radio import station, heard, PayloadWire


@pytest.fixture
def bridge(station):
    coordinator, radios, buses, frames = station
    config = radios[0].config
    config.ardos_cz_enabled = True
    config.ardos_cz_url = 'https://test.example'
    config.auto_deliver = False
    client = FakeClient()
    client.station = config.callsign
    client.device_id = 'relay-device'
    client.scopes = {'connect', 'bridge'}
    requests = []
    original = client.request
    def request(method, path, body=None):
        requests.append((method, path, body))
        return original(method, path, body)
    client.request = request
    service = ArdosService(radios[0], lambda *_: client)
    radios[0].ardos_cz = service
    yield service, client, coordinator, radios, buses, frames, requests
    service.close()
    service.workers.close(wait=True)


def incoming(bridge, ingress=0, ttl=5):
    _, _, _, radios, _, _, _ = bridge
    mail = MailMessage(msg_id=501, source='OK1AAA', final_dest='OK1BBB',
                       subject='RF to server', body='Original RF sender has no server account')
    message = Message(msg_id=501, source='OK1AAA', final_dest='OK1BBB',
        next_hop='OK7PS', direction='in', state=SessionState.RECEIVING,
        ttl=ttl, payload_bytes=mail.to_bundle())
    radios[ingress].net.sessions[501] = message
    radios[ingress].net.notify_payload_delivered(501)
    return message


@pytest.mark.parametrize('ingress,single', [(0, False), (1, False), (0, True)])
def test_rf_sender_server_recipient_and_return_receipt(bridge, ingress, single):
    server, client, coordinator, radios, buses, frames, requests = bridge
    if single:
        coordinator.radios.remove(radios[1])
    # Even a known RF destination/manual route must not win in server-first relay mode.
    for radio in coordinator.radios:
        heard(radio, 'OK1BBB')
        radio.routes.add(Route('OK1BBB', 'OK1BBB'))
    sender = Orchestrator('OK1AAA', buses[ingress].endpoint('sender'), auto_route=False)
    wire = PayloadWire()
    sender.payload = radios[ingress].net.payload = wire
    value = MailMessage(msg_id=501, source='OK1AAA', final_dest='OK1BBB',
                        subject='Bridge', body='RF origin; server destination')
    outgoing = sender.send_message('OK1BBB', value.subject, 501, next_hop='OK7PS',
                                   payload_bytes=value.to_bundle(), ttl=5)
    for _ in range(15):
        for bus in buses:
            bus.pump()
        coordinator.tick()
    drain(server)
    assert server.store.server_path(501)['state'] == 'accepted'
    assert server.store.get(501).folder == Folder.TRANSIT
    assert outgoing.state != SessionState.DELIVERED
    assert server.store.radio_path(501) == {'inbound_radio':ingress+1, 'previous_hop':'OK1AAA', 'ttl':4}
    assert all(body['ingress'] == 'rf_bridge' for _, path, body in requests
               if path in ('/v1/messages', '/v1/delivery/availability'))
    assert any(f.type == FrameType.RECEIVED and f.source == 'OK7PS' for f in frames[ingress])
    assert not any(f.source == 'OK7PS' and f.type in (FrameType.HAVE_MSG, FrameType.ROUTE_QUERY)
                   for band in frames for f in band)
    # No end-to-end RF confirmation until the destination's durable ACK.
    client.saved['state'] = 'delivered'
    server._poll(client)
    assert MessageStore(server.store.root).server_path(501)['rf_receipt_due']
    server._queue_rf_receipts()
    coordinator.flush_receipts()
    for bus in buses:
        bus.pump()
    assert outgoing.state == SessionState.DELIVERED
    assert server.store.get(501).status == Status.DELIVERED
    assert any(f.type == FrameType.DELIVERED and f.next_hop == 'OK1AAA' for f in frames[ingress])
    assert not any(f.type == FrameType.DELIVERED and f.next_hop == 'OK1AAA' for f in frames[1-ingress])


def test_busy_server_worker_holds_relay_without_rf(bridge):
    server, _, coordinator, _, buses, frames, _ = bridge
    entered, release = threading.Event(), threading.Event()
    def busy():
        entered.set()
        assert release.wait(5)
        return 60
    server._active = True
    server.workers.submit('poll', busy, server._finish)
    try:
        assert entered.wait(2)
        incoming(bridge)
        for bus in buses:
            bus.pump()
        coordinator.tick()
        assert MessageStore(server.store.root).server_path(501)['state'] == 'checking'
        assert not any(f.type == FrameType.HAVE_MSG for band in frames for f in band)
    finally:
        release.set()
    drain(server)
    server.tick()
    drain(server)
    assert server.store.server_path(501)['state'] == 'accepted'


@pytest.mark.parametrize('failure', ['offline', 'denied', 'unknown', 'missing'])
def test_fallback_only_after_definite_rejection_or_unknown_timeout(bridge, monkeypatch, failure):
    server, client, _, radios, _, _, _ = bridge
    fallback = []
    monkeypatch.setattr(radios[0], 'send_queued', lambda mid, **kw: fallback.append((mid, kw)))
    if failure == 'offline':
        client.online = False
    else:
        client.upload_error = ServerError('grant_denied', 403) if failure == 'denied' else ServerError('unavailable')
    incoming(bridge)
    drain(server)
    if failure in ('unknown', 'missing'):
        assert not fallback and server.holds(501)
        if failure == 'missing':
            client.upload_error = None
            server._poll(client)
        else:
            server.store.server_path(501, attempted_at=time.time()-91)
        server._last_auto_send.clear()
        server.paused = True
        server.tick()
    assert server.store.server_path(501)['state'] == 'rf_fallback'
    assert fallback == [(501, {'_skip_server':True})]
    assert server.store.get(501).status != Status.DELIVERED


@pytest.mark.parametrize('policy', ['rf_only', 'relay_disabled', 'ttl_exhausted'])
def test_bridge_respects_rf_only_relay_switch_and_ttl(bridge, policy):
    server, _, _, radios, _, _, requests = bridge
    if policy == 'rf_only':
        server.config.ardos_cz_preference = 'rf_only'
    elif policy == 'relay_disabled':
        radios[0].config.auto_relay = False
        radios[0].net.relay = False
    incoming(bridge, ttl=1 if policy == 'ttl_exhausted' else 5)
    assert not server.holds(501)
    assert not requests


def test_delivered_bridge_duplicate_after_local_restart_is_not_forwarded(bridge):
    server, client, _, radios, _, _, requests = bridge
    first = incoming(bridge)
    drain(server)
    client.saved['state'] = 'delivered'
    server._poll(client)
    server.store = radios[0].mailstore = MessageStore(server.store.root)
    radios[0].net.sessions.clear()
    second = incoming(bridge)
    assert first is not second and second.state == SessionState.DELIVERED
    assert server.store.server_path(501)['rf_receipt_due']
    assert len([r for r in requests if r[1] == '/v1/messages']) == 1


def test_failed_radio_path_persistence_rolls_back_memory(bridge, monkeypatch):
    server, _, _, _, _, _, _ = bridge
    incoming(bridge)
    drain(server)
    before = server.store.radio_path(501)
    monkeypatch.setattr(server.store, '_save_index', lambda: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        server.store.radio_path(501, previous_hop='WRONG')
    assert server.store.radio_path(501) == before


def test_pending_final_receipt_survives_coordinator_restart(bridge):
    from guardian.multi_radio import RadioCoordinator
    server, client, _, radios, buses, frames, _ = bridge
    incoming(bridge, ingress=1)
    drain(server)
    client.saved['state'] = 'delivered'
    server._poll(client)
    server._queue_rf_receipts()
    saved = MessageStore(server.store.root).radio_path(501)['pending_receipt']
    assert saved['radio_id'] == 2
    assert not server.store.server_path(501)['rf_receipt_due']
    assert radios[0]._mail_delete_block_reason([501])
    restarted = RadioCoordinator(radios[0])
    restarted.attach(radios[1], 2)
    restarted.flush_receipts()
    for bus in buses:
        bus.pump()
    assert any(f.type == FrameType.DELIVERED and f.next_hop == 'OK1AAA' for f in frames[1])
    assert server.store.radio_path(501)['pending_receipt'] is None
    assert radios[0]._mail_delete_block_reason([501]) is None


def test_late_server_delivery_cancels_active_rf_fallback(bridge, monkeypatch):
    server, client, coordinator, radios, _, _, _ = bridge
    incoming(bridge)
    drain(server)
    # Server accepted while its upload reply was lost; the RF timeout has elapsed.
    server.store.server_path(501, state='rf_fallback')
    active = Message(msg_id=501, source='OK7PS', final_dest='OK1BBB',
        next_hop='OK1BBB', direction='out', state=SessionState.TRANSFERRING)
    radios[1].net.sessions[501] = active
    stopped = []
    monkeypatch.setattr(radios[1].net, '_stop_message_work', lambda msg: stopped.append(msg.msg_id))
    client.saved['state'] = 'delivered'
    server._poll(client)
    server._queue_rf_receipts()
    assert stopped == [501]
    assert active.state == SessionState.DELIVERED


def test_late_delivery_during_compression_never_announces_stale_bundle(bridge, monkeypatch):
    server, client, _, radios, buses, frames, _ = bridge
    incoming(bridge)
    drain(server)
    assert radios[0]._mail_delete_block_reason([501])
    server.store.server_path(501, state='rf_fallback')
    heard(radios[1], 'OK1BBB')
    radios[1].config.guardian_compression = True
    radios[1].config.guardian_aggressive_compression = False
    radios[1].config.vara_file_compression = False
    entered, release = threading.Event(), threading.Event()
    original = MailMessage.to_guardian_bundle
    def compress(mail, baseline):
        entered.set()
        assert release.wait(5)
        return original(mail, baseline)
    monkeypatch.setattr(MailMessage, 'to_guardian_bundle', compress)
    try:
        assert radios[0].send_queued(501, _skip_server=True)
        assert entered.wait(2)
        client.saved['state'] = 'delivered'
        server._poll(client)
        server._queue_rf_receipts()
    finally:
        release.set()
    deadline = time.monotonic()+5
    while 501 in radios[0]._mail_preparing and time.monotonic() < deadline:
        radios[1].workers.drain()
        time.sleep(.005)
    assert 501 not in radios[0]._mail_preparing
    for bus in buses:
        bus.pump()
    assert not any(f.type == FrameType.HAVE_MSG for band in frames for f in band)
    assert server.store.get(501).status == Status.DELIVERED


@pytest.mark.parametrize('persistent', [False, True])
def test_windows_index_lock_retries_are_bounded_and_durable(bridge, monkeypatch, persistent):
    import guardian.message.store as storage
    server, _, _, _, _, _, _ = bridge
    incoming(bridge)
    drain(server)
    before = server.store.server_path(501)
    replace = storage.os.replace
    attempts = []
    def locked(source, destination):
        attempts.append(destination)
        if persistent or len(attempts) == 1:
            error = PermissionError('Windows sharing lock')
            error.winerror = 32
            raise error
        return replace(source, destination)
    monkeypatch.setattr(storage.os, 'replace', locked)
    if persistent:
        with pytest.raises(PermissionError):
            server.store.server_path(501, state='delivered')
        assert len(attempts) == 5
        assert server.store.server_path(501) == before
        assert MessageStore(server.store.root).server_path(501) == before
    else:
        server.store.server_path(501, state='delivered')
        assert len(attempts) == 2
        assert MessageStore(server.store.root).server_path(501)['state'] == 'delivered'
