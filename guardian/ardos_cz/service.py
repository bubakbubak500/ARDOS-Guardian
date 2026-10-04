"""One independent worker for authentication, custody and mailbox polling."""
from __future__ import annotations

import base64
import hashlib
import random
import time

from ..message import Folder, Status
from ..services import WorkerPool
from .client import Client, ServerError, server_url
from .credentials import WindowsCredentials
from .protocol import inspect_bundle

HELD_STATES = {'uploading', 'unknown', 'accepted'}


class ArdosService:
    def __init__(self, operations, client_factory=None):
        self.operations = operations
        self.store = operations.mailstore
        self.config = operations.config
        self.workers = WorkerPool(max_workers=1, thread_name_prefix='guardian-ardos')
        self.client_factory = client_factory
        self.client = None
        self.identity = None
        self.paused = False
        self.closed = False
        self.state = 'disabled'
        self.last_verified = 0.0
        self.lease_deadline = 0.0
        self.next_poll = 0.0
        self.failures = 0
        self._active = False
        self._last_auto_send = {}

    def enabled(self):
        return bool(self.config.ardos_cz_enabled and not self.paused and not self.closed)

    def _identity(self):
        return (self.config.ardos_cz_url, self.config.callsign)

    def _client(self):
        identity = self._identity()
        if identity != self.identity or self.client is None:
            self.lease_deadline = 0
            url, station = identity
            url = server_url(url)
            credentials = WindowsCredentials(url, station, str(self.store.root.resolve()))
            self.client = (self.client_factory(url, station, credentials) if self.client_factory
                           else Client(url, station, credentials))
            self.identity = identity
        return self.client

    def _set_state(self, value):
        if self.state != value:
            self.state = value
            self.operations.events.publish('ARDOS CZ: ' + value, source='ardos_cz')

    def snapshot(self):
        state = self.state
        if not self.enabled():
            state = 'disabled'
        elif state == 'online' and time.monotonic() >= self.lease_deadline:
            state = 'unavailable'
        return {'state': state, 'last_verified': self.last_verified}

    def _heartbeat(self, client):
        result = client.heartbeat()
        return min(60, max(0, float(result['lease_seconds'])))

    def _finish(self, result):
        self._active = False
        if self.closed:
            return
        if result.error:
            self.failures = min(6, self.failures+1)
            self.lease_deadline = 0
            code = result.error.code if isinstance(result.error, ServerError) else 'local_error'
            self._set_state(code)
            self.next_poll = time.monotonic() + min(60, 2**self.failures) + random.random()
        else:
            self.failures = 0
            self.last_verified = time.time()
            self.lease_deadline = time.monotonic() + float(result.value or 0)
            self._set_state('online')
            self.next_poll = time.monotonic()+15

    def enroll(self, invite):
        if not self.enabled() or self._active:
            return False
        client = self._client()
        self._active = True
        self._set_state('connecting')
        def run():
            client.enroll(invite)
            return self._heartbeat(client)
        return self.workers.submit('enroll', run, self._finish)

    def connect(self):
        self.paused = False
        self.next_poll = 0

    def disconnect(self):
        self.paused = True
        self.lease_deadline = 0
        self._set_state('disabled')
        client = self.client
        if client and client.token:
            def logout():
                try:
                    client.request('DELETE', '/v1/session')
                finally:
                    client.token = ''
            self.workers.submit('disconnect', logout)

    def holds(self, message_id):
        return self.store.server_path(message_id).get('state') in HELD_STATES

    def _publish_receipt(self, mid, reply):
        self.store.server_path(mid, server_id=reply['id'], state=reply['state'], expires=reply['expires'])
        if reply['state'] == 'delivered':
            self.store.set_status(mid, status=Status.DELIVERED, folder=Folder.SENT)
            self.store.mark_sent(mid)
        elif reply['state'] == 'expired':
            self.store.set_status(mid, status=Status.FAILED)

    def send(self, message_id, *, leave=False):
        """Return None when RF policy should proceed, True when handled/held."""
        if self.holds(message_id):
            return True
        if not leave and self.store.server_path(message_id).get('state') == 'rf_fallback':
            return None
        if not self.enabled():
            return False if leave else None
        if not leave and self.config.ardos_cz_preference != 'server_first':
            return None
        if self._active:
            return False if leave else None
        op = self.operations
        if not op._mail_mutation_lock.acquire(blocking=False):
            return False
        try:
            mail = self.store.get(message_id)
            if not mail or mail.folder != Folder.OUTBOX or mail.source != self.config.callsign:
                return False if leave else None
            # Every manually configured RF route takes precedence over automatic internet selection.
            route = op.routes.lookup(mail.final_dest)
            if not leave and route is not None and getattr(route, 'source', 'manual') == 'manual':
                return None
            if message_id in op._mail_preparing or (op.coordinator and op.coordinator.active_message(message_id)):
                return False
            active = op.net.sessions.get(message_id)
            if active is not None and not active.state.terminal:
                return False
            try:
                client = self._client()
            except (ValueError, RuntimeError):
                self._set_state('invalid_configuration')
                return False if leave else None
            op._mail_preparing.add(message_id)
            self._active = True
        finally:
            op._mail_mutation_lock.release()

        def run():
            lease = self._heartbeat(client)
            bundle = mail.to_bundle()
            info = inspect_bundle(bundle)
            mode = 'leave' if leave else 'online'
            key = hashlib.sha256(f"{client.url}\n{mail.source}\n{mail.msg_id}\n{info['content_hash']}\n{mode}".encode()).hexdigest()
            if not leave:
                availability = client.request('POST', '/v1/delivery/availability', {'destination': mail.final_dest})
                if not availability['online']:
                    raise ServerError('destination_offline', 409)
            self.store.server_path(message_id, state='uploading', key=key, mode=mode,
                                   server_url=client.url, content_hash=info['content_hash'], attempted_at=time.time())
            try:
                reply = client.request('PUT', '/v1/messages', {'protocol': 1, 'ingress': 'direct',
                    'mode': mode, 'key': key, 'size': len(bundle), 'bundle_hash': info['bundle_hash'],
                    'bundle': base64.b64encode(bundle).decode()})
            except ServerError as exc:
                # Only a definitive rejection permits immediate RF fallback.
                unknown = exc.status == 0 or exc.status >= 500
                self.store.server_path(message_id, state='unknown' if unknown else 'rejected', error=exc.code)
                raise
            self._publish_receipt(message_id, reply)
            return lease

        def done(result):
            with op._mail_mutation_lock:
                op._mail_preparing.discard(message_id)
            self._finish(result)
            self._last_auto_send[message_id] = time.monotonic()
            if result.error and not leave and not self.holds(message_id) and not self.closed:
                op.send_queued(message_id, _skip_server=True)

        if not self.workers.submit('send', run, done):
            self._active = False
            op._mail_preparing.discard(message_id)
            return False
        return True

    def _poll(self, client):
        lease = self._heartbeat(client)
        for meta in self.store.list(Folder.OUTBOX):
            if self.closed or not self.enabled():
                return lease
            path = meta.get('server_path', {})
            if path.get('state') not in HELD_STATES | {'rf_fallback'} or path.get('server_url') != client.url:
                continue
            mid = meta['msg_id']
            try:
                reply = client.request('GET', '/v1/handoffs/' + path['key'])
            except ServerError as exc:
                if exc.status == 404:
                    self.store.server_path(mid, state='not_accepted')
                    continue
                raise
            self._publish_receipt(mid, reply)
        if 'receive' in client.scopes and self.enabled():
            result = client.request('GET', '/v1/mailbox')
            for message in result['messages']:
                if self.closed or not self.enabled():
                    break
                bundle = base64.b64decode(message['bundle'], validate=True)
                if hashlib.sha256(bundle).hexdigest() != message['bundle_hash']:
                    raise ServerError('content_hash_mismatch')
                with self.operations._mail_mutation_lock:
                    self.store.import_server(bundle, client.station, {'server_id': message['id'],
                        'server_url': client.url, 'content_hash': message['content_hash'], 'origin_verified': True})
                client.request('POST', '/v1/messages/' + message['id'] + '/ack',
                               {'content_hash': message['content_hash']})
        return lease

    def tick(self):
        self.workers.drain()
        # A timeout is not a rejection. Reconcile first; after 90 seconds the
        # durable receiver dedupe permits RF even if the server remains down.
        if not self._active:
            for meta in self.store.list(Folder.OUTBOX):
                path = meta.get('server_path', {})
                if (path.get('state') in {'uploading', 'unknown'}
                        and time.time() - path.get('attempted_at', time.time()) >= 90):
                    self.store.server_path(meta['msg_id'], state='rf_fallback')
        if not self.enabled():
            self._set_state('disabled')
            return
        if self._active:
            return
        if time.monotonic() < self.next_poll:
            if self.snapshot()['state'] == 'online' and self.config.auto_deliver and self.config.ardos_cz_preference == 'server_first':
                for meta in self.store.list(Folder.OUTBOX):
                    mid = meta['msg_id']
                    if (meta.get('status') == Status.QUEUED and not self.holds(mid)
                            and time.monotonic() - self._last_auto_send.get(mid, -60) >= 60):
                        self._last_auto_send[mid] = time.monotonic()
                        if self.send(mid):
                            break
            return
        try:
            client = self._client()
        except (ValueError, RuntimeError):
            self._set_state('invalid_configuration')
            self.next_poll = time.monotonic()+30
            return
        self._active = True
        if self.state != 'online':
            self._set_state('connecting')
        self.workers.submit('poll', lambda: self._poll(client), self._finish)

    def close(self):
        self.closed = True
        self.lease_deadline = 0
        cancelled = getattr(self.client, 'cancelled', None)
        if cancelled is not None:
            cancelled.set()
        self.workers.close(wait=False)
