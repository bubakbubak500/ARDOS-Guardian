"""Internet custody never implies delivery and never destroys a local identity."""
import base64
import json
import threading
import time
from types import SimpleNamespace

import pytest

from guardian.ardos_cz.client import Client, ServerError, server_url
from guardian.ardos_cz.protocol import inspect_bundle
from guardian.ardos_cz.service import ArdosService
from guardian.config import StationConfig
from guardian.message import Folder, MailMessage, MessageStore, Status
from guardian.operations import Operations
from guardian.routing import HeardStations, RouteTable, Route
from guardian.services import EventBus, SnapshotStore, WorkerPool
from guardian.session import SessionState


def mail(mid=42, body='Hello'):
    return MailMessage(msg_id=mid, source='OK1AAA', final_dest='OK1BBB', subject='Test',
                       body=body, created=1000.0, folder=Folder.OUTBOX, status=Status.QUEUED)


def receipt(bundle):
    return {'server_id':'server-id', 'server_url':'https://test.example',
            'content_hash': inspect_bundle(bundle)['content_hash']}


def test_default_off_and_https_only():
    assert not StationConfig().ardos_cz_enabled
    assert server_url('https://example.test/') == 'https://example.test'
    for invalid in ('http://example.test', 'https://user:secret@example.test',
                    'https://example.test/path', 'https://example.test?key=secret'):
        with pytest.raises(ValueError):
            server_url(invalid)


def test_durable_import_restart_and_rf_duplicate(tmp_path):
    store = MessageStore(tmp_path)
    bundle = mail().to_bundle()
    store.import_server(bundle, 'OK1BBB', receipt(bundle))
    store.mark_read(42)
    restart = MessageStore(tmp_path)
    restart.import_server(bundle, 'OK1BBB', receipt(bundle))
    restart.store_incoming(bundle, 'OK1BBB', via='OK1AAA')
    assert len(restart.list(Folder.INBOX)) == 1
    assert restart.get(42).read
    assert restart.get(42).body == 'Hello'


def test_rf_first_then_server_deduplicates_hops_and_zip(tmp_path):
    store = MessageStore(tmp_path)
    bundle = mail().to_bundle()
    store.store_incoming(bundle, 'OK1BBB', via='OK1RELAY')
    store.mark_read(42)
    store.import_server(bundle, 'OK1BBB', receipt(bundle))
    assert len(store.list()) == 1
    assert store.get(42).read and store.get(42).hops == ['OK1RELAY']


def test_identity_conflict_preserves_original_and_no_import_receipt(tmp_path):
    store = MessageStore(tmp_path)
    store.add(mail(body='Original'))
    bundle = mail(body='Different').to_bundle()
    with pytest.raises(ValueError, match='local_message_id_conflict'):
        store.import_server(bundle, 'OK1BBB', receipt(bundle))
    assert store.get(42).body == 'Original'
    assert not store.server_path(42)


def test_failed_index_write_never_acknowledge_memory_only_import(tmp_path, monkeypatch):
    store = MessageStore(tmp_path)
    bundle = mail().to_bundle()
    save = store._save_index
    monkeypatch.setattr(store, '_save_index', lambda: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        store.import_server(bundle, 'OK1BBB', receipt(bundle))
    assert store.list() == []  # an unindexed bundle alone must not count as import
    monkeypatch.setattr(store, '_save_index', save)
    store.import_server(bundle, 'OK1BBB', receipt(bundle))
    assert MessageStore(tmp_path).get(42).body == 'Hello'


class FakeClient:
    url = 'https://test.example'
    station = 'OK1AAA'
    token = 'test'
    scopes = {'connect', 'deposit', 'receive'}
    def __init__(self):
        self.calls = []
        self.upload_error = None
        self.online = True
        self.mailbox = []
        self.saved = None
    def heartbeat(self):
        return {'lease_seconds': 60}
    def request(self, method, path, body=None):
        self.calls.append((threading.get_ident(), method, path))
        if path == '/v1/delivery/availability':
            return {'online': self.online}
        if path == '/v1/messages':
            if self.upload_error:
                raise self.upload_error
            info = inspect_bundle(base64.b64decode(body['bundle']))
            self.saved = {'id':'server-id', 'state':'accepted', 'expires':time.time()+600,
                          'content_hash':info['content_hash']}
            return self.saved
        if path.startswith('/v1/handoffs/'):
            if self.upload_error:
                raise self.upload_error
            if self.saved:
                return self.saved
            raise ServerError('not_found', 404)
        if path == '/v1/mailbox':
            return {'messages': self.mailbox}
        if path.endswith('/ack'):
            return {'state':'delivered'}
        raise AssertionError(path)


@pytest.fixture
def service(tmp_path):
    cfg = StationConfig(callsign='OK1AAA', ardos_cz_enabled=True, ardos_cz_url='https://test.example', auto_deliver=False)
    cfg._save_disabled = True
    store = MessageStore(tmp_path / 'mail')
    store.add(mail())
    pool = WorkerPool()
    op = Operations(cfg, EventBus(), SnapshotStore(), pool, store, RouteTable(), HeardStations())
    fake = FakeClient()
    service = ArdosService(op, lambda *_:fake)
    op.ardos_cz = service
    yield service, fake, op, store
    service.close()
    service.workers.close(wait=True)
    op.close()
    pool.close(wait=True)


def drain(service):
    deadline = time.monotonic()+5
    while service._active and time.monotonic() < deadline:
        service.workers.drain()
        time.sleep(.005)
    assert not service._active


def test_acceptance_is_not_delivery_and_network_runs_off_main_thread(service):
    server, client, op, store = service
    main_thread = threading.get_ident()
    assert op.send_queued(42)  # no audio channel required
    drain(server)
    assert store.server_path(42)['state'] == 'accepted'
    assert store.get(42).folder == Folder.OUTBOX
    assert store.get(42).status != Status.DELIVERED
    assert all(thread != main_thread for thread, _, _ in client.calls)
    assert server.send(42)  # already held; no duplicate request
    assert len([call for call in client.calls if call[2] == '/v1/messages']) == 1
    client.saved['state'] = 'delivered'
    server._poll(client)
    assert store.get(42).folder == Folder.SENT and store.get(42).status == Status.DELIVERED


def test_offline_normal_send_does_not_deposit_and_falls_back(service, monkeypatch):
    server, client, op, store = service
    client.online = False
    fallback = []
    monkeypatch.setattr(op, 'send_queued', lambda mid, **kw:fallback.append((mid, kw)))
    assert server.send(42)
    drain(server)
    assert not any(path == '/v1/messages' for _, _, path in client.calls)
    assert fallback == [(42, {'_skip_server':True})]
    assert server.send(42, leave=True)
    drain(server)
    assert store.server_path(42)['state'] == 'accepted'


def test_unknown_upload_reconciles_before_rf_and_survives_restart(service, monkeypatch):
    server, client, op, store = service
    client.upload_error = ServerError('unavailable')
    fallback = []
    monkeypatch.setattr(op, 'send_queued', lambda *a, **k:fallback.append(a))
    assert server.send(42)
    drain(server)
    assert store.server_path(42)['state'] == 'unknown'
    assert MessageStore(store.root).server_path(42)['key']
    assert not fallback
    store.server_path(42, attempted_at=time.time()-100)
    server.paused = True
    server.tick()
    assert store.server_path(42)['state'] == 'rf_fallback'
    assert server.send(42) is None


def test_disabled_manual_route_and_active_rf_never_start_internet_send(service):
    server, client, op, store = service
    op.config.ardos_cz_enabled = False
    assert server.send(42) is None
    op.config.ardos_cz_enabled = True
    op.routes = RouteTable([Route('OK1BBB', 'OK1CCC')])
    assert server.send(42) is None
    assert not client.calls
    op.routes = RouteTable()
    op._mail_preparing.add(42)
    assert server.send(42) is False
    op._mail_preparing.clear()


def test_import_failure_does_not_ack_and_retries_safely(service, monkeypatch):
    server, client, op, store = service
    client.station = 'OK1BBB'
    bundle = mail(mid=43).to_bundle()
    info = inspect_bundle(bundle)
    client.mailbox = [{'id':'incoming', 'bundle':base64.b64encode(bundle).decode(),
        'bundle_hash':info['bundle_hash'], 'content_hash':info['content_hash']}]
    original = store.import_server
    monkeypatch.setattr(store, 'import_server', lambda *a, **k:(_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        server._poll(client)
    assert not any(path.endswith('/ack') for _, _, path in client.calls)
    monkeypatch.setattr(store, 'import_server', original)
    server._poll(client)
    assert store.get(43).folder == Folder.INBOX
    assert any(path.endswith('/ack') for _, _, path in client.calls)


def test_online_expires_without_a_fresh_heartbeat(service):
    server, _, _, _ = service
    server.state = 'online'
    server.lease_deadline = time.monotonic()-1
    assert server.snapshot()['state'] == 'unavailable'


def test_rf_receipt_waits_for_successful_import(service):
    server, _, op, store = service
    from guardian.session import Message
    from guardian.protocol import FrameType
    op.net.transport.send = lambda frame: frames.append(frame)
    frames = []
    message = Message(msg_id=42, source='OK1BBB', final_dest='OK1AAA', next_hop='OK1AAA', direction='in',
                      payload_bytes=mail(body='collision').to_bundle(), state=SessionState.RECEIVING)
    op.net.sessions[42] = message
    op.net.notify_payload_delivered(42)
    assert message.state == SessionState.FAILED
    assert not any(frame.type in (FrameType.RECEIVED, FrameType.DELIVERED) for frame in frames)
    assert store.get(42).body == 'Hello'
