"""Thread boundary and state model for the offline companion server."""

from __future__ import annotations

import hashlib
import json
import queue
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import __version__
from ..config import config_dir
from ..i18n import dual
from ..message import Folder, MailMessage, Status
from ..protocol import Priority
from ..services import LogLevel


PAIRING_LIFETIME = 5 * 60
SESSION_IDLE_LIFETIME = 12 * 60 * 60
MAX_SUBJECT = 120
MAX_BODY = 8_192
MAX_NOTE = 2_000
_DESTINATION = re.compile(r"^[A-Z0-9][A-Z0-9/-]{0,15}$")


class CompanionError(RuntimeError):
    """A safe error that can be returned to a paired phone."""


@dataclass(slots=True)
class _Session:
    token: str
    name: str
    created: float
    last_seen: float
    address: str = ""


@dataclass(slots=True)
class _Command:
    name: str
    payload: dict[str, Any]
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None


class CompanionController:
    """Own the HTTP server while keeping all mutations on Guardian's UI thread.

    Request handler threads only read the immutable JSON cache.  Anything that
    changes mail or starts a radio operation is queued and executed by
    :meth:`poll`, which ``ShellRuntime.tick`` calls on the Qt thread.
    """

    def __init__(self, runtime, *, notes_path: Path | None = None) -> None:
        self.runtime = runtime
        self.notes_path = notes_path or (config_dir() / "companion-notes.json")
        self._commands: queue.Queue[_Command] = queue.Queue()
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._sessions: dict[str, _Session] = {}
        self._pairing_token = ""
        self._pairing_expires = 0.0
        self._server = None
        self._server_thread: threading.Thread | None = None
        self._revision = 0
        self._fingerprint = ""
        self._state: dict[str, Any] = {}
        self._notes = self._load_notes()
        self._checkin: dict[str, Any] | None = None
        self._checkin_alerted = False
        self.remote_emergency_armed = False
        self.hide_message_previews = False
        self.last_error = ""
        self.bound_port = 0
        self.poll()

    # -- lifetime -----------------------------------------------------
    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self, *, host: str = "0.0.0.0", port: int = 8765) -> int:
        if self.running:
            return self.bound_port
        from .server import CompanionHTTPServer, CompanionRequestHandler

        server = CompanionHTTPServer((host, int(port)), CompanionRequestHandler, self)
        thread = threading.Thread(
            target=server.serve_forever,
            name="guardian-companion",
            daemon=True,
        )
        self._server = server
        self._server_thread = thread
        self.bound_port = int(server.server_address[1])
        self.last_error = ""
        self.new_pairing()
        thread.start()
        self.runtime.events.publish(
            dual(
                f"Offline companion server started on port {self.bound_port}.",
                f"Offline companion server byl spuštěn na portu {self.bound_port}.",
            ),
            source="companion",
        )
        return self.bound_port

    def stop(self) -> None:
        server = self._server
        if server is None:
            return
        self._server = None
        server.shutdown()
        server.server_close()
        thread = self._server_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._server_thread = None
        self.bound_port = 0
        self.remote_emergency_armed = False
        with self._changed:
            self._sessions.clear()
            self._changed.notify_all()
        self.runtime.events.publish(
            dual("Companion server stopped.", "Companion server byl zastaven."),
            source="companion",
        )

    # -- pairing ------------------------------------------------------
    def new_pairing(self) -> str:
        with self._lock:
            self._pairing_token = secrets.token_urlsafe(24)
            self._pairing_expires = time.time() + PAIRING_LIFETIME
            return self._pairing_token

    def pairing_fragment(self) -> str:
        with self._lock:
            return self._pairing_token

    def pair(self, token: str, *, name: str, address: str) -> str:
        now = time.time()
        with self._changed:
            if (
                not self._pairing_token
                or now > self._pairing_expires
                or not secrets.compare_digest(str(token), self._pairing_token)
            ):
                raise CompanionError("Pairing code is invalid or has expired.")
            session_token = secrets.token_urlsafe(32)
            safe_name = " ".join(str(name or "Phone").split())[:40] or "Phone"
            self._sessions[session_token] = _Session(
                session_token, safe_name, now, now, str(address)[:80]
            )
            # Pairing is intentionally one-use.  The desktop can display a new
            # QR code whenever another device should be admitted.
            self._pairing_token = ""
            self._pairing_expires = 0.0
            self._changed.notify_all()
        self.runtime.events.publish(
            dual(
                f"Companion paired: {safe_name}.",
                f"Companion spárován: {safe_name}.",
            ),
            source="companion",
        )
        return session_token

    def authenticate(self, token: str, *, address: str = "") -> bool:
        now = time.time()
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return False
            if now - session.last_seen > SESSION_IDLE_LIFETIME:
                self._sessions.pop(token, None)
                return False
            session.last_seen = now
            if address:
                session.address = str(address)[:80]
            return True

    def revoke_all(self) -> None:
        with self._changed:
            self._sessions.clear()
            self._changed.notify_all()

    def clients(self) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock:
            for token, item in list(self._sessions.items()):
                if now - item.last_seen > SESSION_IDLE_LIFETIME:
                    self._sessions.pop(token, None)
            return [
                {
                    "name": item.name,
                    "address": item.address,
                    "online": now - item.last_seen < 35,
                    "last_seen": item.last_seen,
                }
                for item in self._sessions.values()
            ]

    # -- request-thread API ------------------------------------------
    def public_info(self) -> dict[str, Any]:
        return {
            "name": "Guardian Companion",
            "station": self.runtime.config.callsign,
            "version": __version__,
            "pairing": bool(self.pairing_fragment()),
        }

    def state(self) -> dict[str, Any]:
        with self._lock:
            # A JSON roundtrip is a cheap, reliable deep copy and guarantees
            # request threads cannot mutate the UI-owned cache.
            return json.loads(json.dumps(self._state))

    def wait_for_state(self, after: int, timeout: float = 22.0) -> dict[str, Any]:
        deadline = time.monotonic() + min(25.0, max(0.0, timeout))
        with self._changed:
            while self._revision <= int(after):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._changed.wait(remaining)
            return json.loads(json.dumps(self._state))

    def submit(self, name: str, payload: dict[str, Any], timeout: float = 5.0) -> Any:
        command = _Command(str(name), dict(payload))
        self._commands.put(command)
        if not command.done.wait(timeout):
            raise CompanionError("Guardian did not process the request in time.")
        if command.error is not None:
            if isinstance(command.error, CompanionError):
                raise command.error
            raise CompanionError(str(command.error)) from command.error
        return command.result

    # -- UI-thread poll ----------------------------------------------
    def poll(self) -> None:
        for _ in range(50):
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                break
            try:
                command.result = self._execute(command.name, command.payload)
            except BaseException as exc:  # returned safely to the request thread
                command.error = exc
            finally:
                command.done.set()

        now = time.time()
        if self._checkin and now >= float(self._checkin["deadline"]):
            if not self._checkin_alerted:
                self._checkin_alerted = True
                self.runtime.events.publish(
                    dual(
                        "Field check-in is overdue.",
                        "Návratový časovač je po termínu.",
                    ),
                    LogLevel.WARNING,
                    source="companion",
                )
        self._publish_state()

    def _execute(self, name: str, payload: dict[str, Any]) -> Any:
        if name == "mark_read":
            message_id = int(payload.get("id", 0))
            if self.runtime.mailstore.get(message_id) is None:
                raise CompanionError("Message was not found.")
            self.runtime.mailstore.mark_read(message_id)
            self.runtime.refresh()
            return {"ok": True}

        if name == "compose":
            destination = str(payload.get("destination", "")).strip().upper()
            if not _DESTINATION.fullmatch(destination):
                raise CompanionError("Enter a valid destination callsign.")
            subject = " ".join(str(payload.get("subject", "")).split())[:MAX_SUBJECT]
            body = str(payload.get("body", "")).strip()
            if not body:
                raise CompanionError("The message body is empty.")
            if len(body) > MAX_BODY:
                raise CompanionError(f"Message is limited to {MAX_BODY} characters.")
            try:
                priority = Priority(int(payload.get("priority", 0)))
            except (TypeError, ValueError):
                raise CompanionError("Invalid message priority.") from None
            transmit = bool(payload.get("transmit", False))
            if transmit and priority is not Priority.EMERGENCY:
                raise CompanionError("Remote transmission is reserved for emergency traffic.")
            if transmit and not self.remote_emergency_armed:
                raise CompanionError("Remote emergency transmission is not armed on Guardian.")
            mail = MailMessage(
                msg_id=self.runtime.mailstore.next_id(self.runtime.config.callsign),
                source=self.runtime.config.callsign,
                final_dest=destination,
                subject=subject or "Phone message",
                body=body,
                priority=int(priority),
                created=time.time(),
                hops=[self.runtime.config.callsign],
                folder=Folder.OUTBOX,
                status=Status.QUEUED,
            )
            self.runtime.mailstore.add(mail)
            self.runtime.refresh()
            self.runtime.events.publish(
                dual(
                    f"Phone queued message #{mail.msg_id} to {destination}.",
                    f"Telefon zařadil zprávu #{mail.msg_id} pro {destination}.",
                ),
                source="companion",
            )
            sent = False
            if transmit:
                sent = bool(self.runtime.operations.send_queued(mail.msg_id))
                if not sent:
                    self.runtime.mailstore.set_status(mail.msg_id, status=Status.QUEUED)
            return {"ok": True, "id": mail.msg_id, "transmit_started": sent}

        if name == "save_note":
            text = str(payload.get("text", "")).strip()
            if not text:
                raise CompanionError("The field note is empty.")
            if len(text) > MAX_NOTE:
                raise CompanionError(f"Field notes are limited to {MAX_NOTE} characters.")
            note = {"id": secrets.token_hex(6), "text": text, "created": time.time()}
            self._notes.insert(0, note)
            del self._notes[50:]
            self._save_notes()
            self.runtime.events.publish(
                dual("Phone saved a field note.", "Telefon uložil polní poznámku."),
                source="companion",
            )
            return {"ok": True, "note": note}

        if name == "delete_note":
            note_id = str(payload.get("id", ""))
            before = len(self._notes)
            self._notes = [item for item in self._notes if item.get("id") != note_id]
            if len(self._notes) == before:
                raise CompanionError("Field note was not found.")
            self._save_notes()
            return {"ok": True}

        if name == "start_checkin":
            minutes = min(180, max(1, int(payload.get("minutes", 10))))
            label = " ".join(str(payload.get("label", "Return to station")).split())[:80]
            self._checkin = {
                "deadline": time.time() + minutes * 60,
                "label": label or "Return to station",
            }
            self._checkin_alerted = False
            self.runtime.events.publish(
                dual(
                    f"Field check-in timer started for {minutes} minutes.",
                    f"Návratový časovač spuštěn na {minutes} minut.",
                ),
                source="companion",
            )
            return {"ok": True, "checkin": self._checkin}

        if name == "clear_checkin":
            self._checkin = None
            self._checkin_alerted = False
            self.runtime.events.publish(
                dual("Field check-in completed.", "Návrat ke stanici potvrzen."),
                source="companion",
            )
            return {"ok": True}

        if name == "ping_station":
            self.runtime.events.publish(
                dual(
                    "Phone requests operator attention at the station.",
                    "Telefon žádá pozornost operátora u stanice.",
                ),
                LogLevel.WARNING,
                source="companion",
            )
            return {"ok": True}

        raise CompanionError("Unknown companion action.")

    def _publish_state(self) -> None:
        snapshot = self.runtime.snapshots.read()
        events = self.runtime.events.history()[-120:]
        messages = []
        for meta in self.runtime.mailstore.list():
            item = dict(meta)
            mail = self.runtime.mailstore.get(int(meta["msg_id"]))
            if mail is not None:
                item["body"] = mail.body
                item["attachments"] = [
                    {"name": attachment.name, "size": attachment.size}
                    for attachment in mail.attachments
                ]
            messages.append(item)
        state = {
            "revision": self._revision,
            "server_time": time.time(),
            "station": self.runtime.config.callsign,
            "version": __version__,
            "mailbox": {
                "inbox": snapshot.mailbox.inbox,
                "unread": snapshot.mailbox.unread,
                "outbox": snapshot.mailbox.outbox,
                "failed": snapshot.mailbox.outbox_failed,
                "transit": snapshot.mailbox.transit,
            },
            "network": {
                "control": snapshot.network.control_channel_active,
                "sessions": snapshot.network.active_sessions,
                "heard": snapshot.network.heard_stations,
                "scanner": snapshot.network.scanner_active,
            },
            "radio": {
                "connected": snapshot.radio.connected,
                "ptt": snapshot.radio.ptt,
                "frequency_hz": snapshot.radio.frequency_hz,
                "mode": snapshot.radio.mode,
            },
            "messages": messages,
            "events": [
                {
                    "time": event.timestamp.isoformat(),
                    "level": event.level.value,
                    "source": event.source,
                    "message": event.message,
                }
                for event in events
            ],
            "remote_emergency_armed": self.remote_emergency_armed,
            "hide_previews": self.hide_message_previews,
            "notes": list(self._notes),
            "checkin": self._checkin,
            "checkin_overdue": self._checkin_alerted,
        }
        fingerprint_source = dict(state)
        fingerprint_source.pop("server_time", None)
        fingerprint_source.pop("revision", None)
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_source, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        with self._changed:
            if fingerprint != self._fingerprint:
                self._fingerprint = fingerprint
                self._revision += 1
                state["revision"] = self._revision
                self._state = state
                self._changed.notify_all()
            elif self._state:
                self._state["server_time"] = state["server_time"]

    def _load_notes(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self.notes_path.read_text(encoding="utf-8"))
            notes = raw.get("notes", [])
            if isinstance(notes, list):
                return [item for item in notes if isinstance(item, dict)][:50]
        except (OSError, ValueError, TypeError):
            pass
        return []

    def _save_notes(self) -> None:
        self.notes_path.parent.mkdir(parents=True, exist_ok=True)
        self.notes_path.write_text(
            json.dumps({"notes": self._notes}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
