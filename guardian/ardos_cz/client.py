"""Bounded HTTPS requests. This module is called only from a dedicated worker."""
from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

from .protocol import callsign, MAX_BUNDLE


class ServerError(RuntimeError):
    def __init__(self, code, status=0):
        super().__init__(code)
        self.code, self.status = code, status


def server_url(value: str) -> str:
    url = urllib.parse.urlsplit(value.strip())
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path not in ('', '/')):
        raise ValueError('ARDOS CZ: use an HTTPS server URL without a path or credentials')
    return value.strip().rstrip('/')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ServerError('redirect_refused')


class Client:
    def __init__(self, url, station, credentials, request=None):
        self.url = server_url(url)
        self.station = callsign(station)
        self.credentials = credentials
        self.token = ''
        self.scopes = set()
        self.cancelled = threading.Event()
        self._request = request
        self._opener = urllib.request.build_opener(NoRedirect)

    def request(self, method, path, body=None):
        if self.cancelled.is_set():
            raise ServerError('cancelled')
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if self._request:
            return self._request(method, path, body, headers)
        encoded = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=encoded, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=5) as response:
                payload = response.read(MAX_BUNDLE * 2 + 65537)
                if len(payload) > MAX_BUNDLE * 2 + 65536:
                    raise ServerError('response_too_large')
                return json.loads(payload)
        except urllib.error.HTTPError as exc:
            try:
                code = json.loads(exc.read(4096)).get('detail', 'server_error')
            except (ValueError, OSError):
                code = 'server_error'
            # Never put server-supplied text or secrets in diagnostics.
            safe = {'unauthorized', 'grant_expired', 'grant_denied', 'device_revoked',
                    'destination_offline', 'destination_not_enrolled', 'conflict',
                    'too_large', 'rate_limited', 'mailbox_full', 'expired', 'not_found',
                    'invalid_invite', 'invite_used', 'origin_mismatch', 'lease_expired'}
            raise ServerError(code if isinstance(code, str) and code in safe else 'server_error', exc.code) from None
        except (OSError, ValueError):
            raise ServerError('unavailable') from None

    def enroll(self, invite):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        saved = self.credentials.load()
        if saved and saved.get('device_id'):
            # A new invitation replaces a revoked device. Preserve the old
            # working identity until the new enrollment actually succeeds.
            pending = saved.get('pending_key')
            if not pending:
                pending = base64.b64encode(Ed25519PrivateKey.generate().private_bytes_raw()).decode()
                self.credentials.save({**saved, 'pending_key': pending})
            candidate = {'private_key': pending}
        else:
            candidate = saved
        if not candidate:
            key = Ed25519PrivateKey.generate()
            candidate = {'private_key': base64.b64encode(key.private_bytes_raw()).decode()}
            # Persist BEFORE enrollment so retries prove possession of the same key.
            self.credentials.save(candidate)
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(candidate['private_key']))
        result = self.request('POST', '/v1/enroll', {'invite': invite,
            'public_key': base64.b64encode(key.public_key().public_bytes_raw()).decode()})
        if result['callsign'] != self.station:
            raise ServerError('station_mismatch')
        self.credentials.save({**candidate, 'device_id': result['device_id']})
        self.token = ''

    def connect(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        saved = self.credentials.load()
        if not saved or not saved.get('device_id'):
            raise ServerError('not_enrolled')
        self.token = ''
        ch = self.request('POST', '/v1/challenge', {'device_id': saved['device_id']})
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(saved['private_key']))
        proof = f"ARDOS-CZ/1\n{ch['id']}\n{saved['device_id']}\n{ch['nonce']}".encode()
        result = self.request('POST', '/v1/session', {'challenge_id': ch['id'],
            'signature': base64.b64encode(key.sign(proof)).decode()})
        if result['protocol'] != 1:
            raise ServerError('unsupported_protocol')
        self.token = result['token']
        info = self.request('GET', '/v1/self')
        if info['callsign'] != self.station or info['protocol'] != 1:
            self.token = ''
            raise ServerError('station_mismatch')
        self.scopes = set(info['scopes'])

    def heartbeat(self):
        if not self.token:
            self.connect()
        try:
            return self.request('PUT', '/v1/presence')
        except ServerError as exc:
            if exc.status != 401:
                raise
            self.connect()
            return self.request('PUT', '/v1/presence')
