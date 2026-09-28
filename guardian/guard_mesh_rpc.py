"""Bounded BLE request framing and the Guard Mesh application API.

Framing runs on the BLE worker. MeshApi.handle runs only on the shell thread.
No request executes code, opens a path, changes radio settings or transfers attachments.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import re
import struct
import time
import uuid
import zipfile

HEADER = struct.Struct("<BBH")  # flags, transfer ID, fragment index
MAX_JSON = 32768
FRAGMENT_BYTES = 16
ASSEMBLY_TIMEOUT = 30.0


def json_bytes(value) -> bytes:
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(data) > MAX_JSON:
        raise ValueError("JSON exceeds 32768 bytes")
    return data


def fragments(value, transfer_id):
    data = json_bytes(value)
    for offset in range(0, len(data), FRAGMENT_BYTES):
        flags = (1 if offset == 0 else 0) | (2 if offset + FRAGMENT_BYTES >= len(data) else 0)
        yield HEADER.pack(flags, transfer_id, offset // FRAGMENT_BYTES) + data[offset:offset + FRAGMENT_BYTES]


class Assembler:
    def __init__(self):
        self.data = bytearray()
        self.transfer_id = None
        self.index = 0
        self.started = 0.0

    def feed(self, packet, now=None):
        now = time.monotonic() if now is None else now
        if not 5 <= len(packet) <= 20:
            raise ValueError("Invalid BLE fragment length")
        flags, tid, index = HEADER.unpack_from(packet)
        if flags & ~3:
            raise ValueError("Invalid BLE fragment flags")
        if flags & 1:
            if index != 0 or self.transfer_id is not None:
                raise ValueError("Unexpected BLE start fragment")
            self.data.clear()
            self.transfer_id, self.index, self.started = tid, 0, now
        if (self.transfer_id != tid or self.index != index
                or now - self.started > ASSEMBLY_TIMEOUT):
            raise ValueError("Missing, reordered or expired BLE fragment")
        self.data.extend(packet[HEADER.size:])
        self.index += 1
        if len(self.data) > MAX_JSON:
            raise ValueError("BLE request too large")
        if not flags & 2:
            return None
        raw = bytes(self.data)
        self.transfer_id = None
        self.data.clear()
        value = json.loads(raw.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Invalid JSON number")))
        if not isinstance(value, dict):
            raise ValueError("BLE request must be a JSON object")
        return tid, value


class ApiError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def integer(request, name, default, low, high):
    value = request.get(name, default)
    if type(value) is not int or not low <= value <= high:
        raise ApiError("invalid_request", f"{name} must be an integer in {low}..{high}")
    return value


class MeshApi:
    def __init__(self, runtime):
        self.runtime = runtime

    def handle(self, request, peer):
        request_id = request.get("id")
        try:
            if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", request_id):
                raise ApiError("invalid_request", "id must contain 1..64 ASCII letters, digits, dot, dash or underscore")
            op = request.get("op")
            if op == "status.get":
                from .guard_mesh import guardian_status
                result = asdict(guardian_status(self.runtime))
            elif op == "contacts.list":
                result = self._contacts(request)
            elif op == "messages.list":
                result = self._messages(request)
            elif op == "message.get":
                result = self._message(request)
            elif op == "message.queue":
                result = self._queue(request, peer)
            else:
                raise ApiError("unknown_operation", "Unknown operation")
            return {"id": request_id, "ok": True, "result": result}
        except ApiError as exc:
            return {"id": request_id if isinstance(request_id, str) and len(request_id) <= 64 else None,
                    "ok": False, "error": {"code": exc.code, "message": str(exc)}}
        except (OSError, ValueError, zipfile.BadZipFile):
            return {"id": request_id, "ok": False,
                    "error": {"code": "storage_error", "message": "Message storage is unavailable"}}

    def _page(self, request, rows):
        offset = integer(request, "offset", 0, 0, 0xFFFFFFFF)
        limit = integer(request, "limit", 10, 1, 20)
        revision = hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False,
                                            allow_nan=False).encode()).hexdigest()[:16]
        if "revision" in request and request["revision"] != revision:
            raise ApiError("list_changed", "List changed; restart at offset 0")
        page = rows[offset:offset + limit]
        return {"items": page, "total": len(rows), "revision": revision,
                "next_offset": offset + len(page) if offset + len(page) < len(rows) else None}

    def _contacts(self, request):
        source = request.get("source", "all")
        if source not in ("all", "live", "saved"):
            raise ApiError("invalid_request", "source must be all, live or saved")
        rows = {}
        if source != "live":
            for route in self.runtime.routes:
                if route.source != "manual":
                    continue
                rows[route.destination] = {"callsign": route.destination, "live": False,
                                           "saved": True, "next_hop": route.preferred}
        if source != "saved":
            now = time.monotonic()
            for station in self.runtime.heard.active(now):
                row = rows.setdefault(station.callsign, {"callsign": station.callsign, "saved": False})
                row.update(live=True, grid=station.grid, frequency_hz=station.last_freq_hz,
                           live_next_hop=station.callsign, approved=True)
            for radio in self.runtime.radio_coordinator.radios:
                for route in radio.net.discovery.routes.routes(now):
                    row = rows.setdefault(route.destination, {"callsign": route.destination, "saved": False})
                    if not row.get("live"):
                        row.update(live=True, live_next_hop=route.next_hop, approved=route.approved)
        return self._page(request, [rows[key] for key in sorted(rows)])

    def _messages(self, request):
        folder = request.get("folder", "inbox")
        if folder not in ("inbox", "outbox", "sent", "draft", "transit"):
            raise ApiError("invalid_request", "Unknown folder")
        fields = ("msg_id", "source", "final_dest", "subject", "created", "status", "read", "priority")
        rows = [{key: meta.get(key) for key in fields}
                for meta in self.runtime.mailstore.list(folder)]
        rows.sort(key=lambda row: (row["created"] or 0, row["msg_id"]), reverse=True)
        # Bound individual metadata too; full subject is available in message.get.
        for row in rows:
            row["subject"] = str(row["subject"] or "")[:160]
        return self._page(request, rows)

    def _message(self, request):
        msg_id = integer(request, "msg_id", None, 0, 0xFFFFFFFF)
        offset = integer(request, "offset", 0, 0, 0xFFFFFFFF)
        limit = integer(request, "limit", 512, 1, 1024)
        value = self.runtime.mailstore.get_text(msg_id)
        if value is None:
            raise ApiError("not_found", "Message not found")
        meta, text = value
        revision = hashlib.sha256((str(meta.get("subject", "")) + "\x00" + text).encode("utf-8")).hexdigest()[:16]
        if "revision" in request and request["revision"] != revision:
            raise ApiError("message_changed", "Message changed; restart at offset 0")
        return {"msg_id": msg_id, "source": meta["source"], "final_dest": meta["final_dest"],
                "subject": str(meta.get("subject", ""))[:256], "body": text[offset:offset + limit],
                "offset": offset, "total_chars": len(text), "revision": revision,
                "next_offset": offset + limit if offset + limit < len(text) else None}

    def _queue(self, request, peer):
        from .message import Folder, MailMessage, Status
        allowed = {"id", "op", "token", "to", "subject", "body", "priority"}
        if request.keys() - allowed:
            raise ApiError("invalid_request", "Unsupported message fields")
        destination = request.get("to")
        subject, body = request.get("subject", ""), request.get("body")
        if not isinstance(destination, str) or not re.fullmatch(r"[A-Za-z0-9/-]{1,16}", destination):
            raise ApiError("invalid_request", "to must be a 1..16 character callsign or group")
        if not isinstance(subject, str) or len(subject) > 256 or "\x00" in subject:
            raise ApiError("invalid_request", "subject must contain at most 256 characters")
        if not isinstance(body, str) or not body.strip() or len(body) > 4096 or "\x00" in body:
            raise ApiError("invalid_request", "body must contain 1..4096 characters")
        priority = integer(request, "priority", 0, 0, 3)
        try:
            token = str(uuid.UUID(request.get("token", "")))
        except (ValueError, TypeError, AttributeError):
            raise ApiError("invalid_request", "token must be a UUID") from None
        source = self.runtime.config.callsign.strip().upper()
        if not source or source == "NOCALL":
            raise ApiError("station_not_configured", "Configure the Guardian station callsign first")
        normalized = {"to": destination.upper(), "subject": subject, "body": body, "priority": priority}
        digest = hashlib.sha256(json_bytes(normalized)).hexdigest()
        message = MailMessage(msg_id=0, source=source, final_dest=normalized["to"], subject=subject,
                              body=body, priority=priority, created=time.time(), hops=[source],
                              folder=Folder.OUTBOX, status=Status.QUEUED)
        try:
            msg_id, duplicate = self.runtime.mailstore.queue_remote_text(
                message, peer.casefold() + ":" + token, digest)
        except ValueError:
            raise ApiError("token_conflict", "Token already belongs to different message content") from None
        return {"msg_id": msg_id, "accepted": True, "duplicate": duplicate}
