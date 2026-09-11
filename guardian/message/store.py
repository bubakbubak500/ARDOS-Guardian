"""Persistent mailbox.

Each message is stored as a `.bundle` file (the transferable ZIP) plus a
lightweight entry in `index.json` (folder, status, next hop, headers) so the
folder lists render without unpacking every bundle.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time

from ..config import config_dir
from ..protocol import crc16
from .mail import Folder, MailMessage, Status


def mail_dir() -> Path:
    d = config_dir() / "mail"
    d.mkdir(parents=True, exist_ok=True)
    return d


class MessageStore:
    def __init__(self, root: Path | None = None):
        self.root = root or mail_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self._index: dict[int, dict] = {}
        self._counter = 0
        self._lock = threading.RLock()
        self.load()

    # ------------------------------------------------------------------ #
    def load(self) -> None:
        with self._lock:
            if self.index_path.exists():
                try:
                    data = json.loads(self.index_path.read_text(encoding="utf-8"))
                    self._index = {
                        int(k): v for k, v in data.get("messages", {}).items()
                    }
                    self._counter = int(data.get("counter", 0))
                except (json.JSONDecodeError, OSError, ValueError):
                    self._index = {}

    def _save_index(self) -> None:
        """Atomically publish one consistent index while callers share the lock."""
        with self._lock:
            payload = {
                "counter": self._counter,
                "messages": {str(k): v for k, v in self._index.items()},
            }
            pending = self.index_path.with_suffix(".json.pending")
            try:
                pending.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                os.replace(pending, self.index_path)
            finally:
                try:
                    pending.unlink(missing_ok=True)
                except OSError:
                    pass

    def _bundle_path(self, msg_id: int) -> Path:
        return self.root / f"{msg_id}.bundle"

    def _meta(self, mail: MailMessage, size: int) -> dict:
        meta = {
            "msg_id": mail.msg_id, "source": mail.source, "final_dest": mail.final_dest,
            "subject": mail.subject, "priority": mail.priority, "created": mail.created,
            "folder": mail.folder, "status": mail.status, "next_hop": mail.next_hop,
            "hops": mail.hops, "size": size, "att": len(mail.attachments),
            "read": getattr(mail, "read", True),
        }
        # received_at/sent_at are deliberately local index metadata. They do
        # not belong in the transferable bundle and therefore never cross RF.
        # Rebuilding an entry (for example when a relay re-stores a bundle)
        # must not erase times already learned locally.
        previous = self._index.get(mail.msg_id, {})
        for key in ("received_at", "sent_at"):
            if key in previous:
                meta[key] = previous[key]
        return meta

    # ------------------------------------------------------------------ #
    def next_id(self, callsign: str = "") -> int:
        """Globally-unique-ish 32-bit id: station-hash prefix + counter.

        12-bit station hash (4096 stations) << 20 | 20-bit per-station counter,
        so two different stations almost never mint the same id.
        """
        with self._lock:
            self._counter += 1
            prefix = crc16(callsign.strip().upper().encode("ascii", "replace")) & 0xFFF
            mid = ((prefix << 20) | (self._counter & 0xFFFFF)) & 0xFFFFFFFF
            self._save_index()
            return mid

    def add(
        self,
        mail: MailMessage,
        *,
        received_at: float | None = None,
        sent_at: float | None = None,
    ) -> None:
        bundle = mail.to_bundle()
        with self._lock:
            self._bundle_path(mail.msg_id).write_bytes(bundle)
            meta = self._meta(mail, len(bundle))
            # A timestamp supplied by a new local event is only applied when
            # this message has not already recorded that event. This keeps a
            # duplicate incoming bundle from moving its displayed date.
            if received_at is not None and not meta.get("received_at"):
                meta["received_at"] = float(received_at)
            if sent_at is not None and not meta.get("sent_at"):
                meta["sent_at"] = float(sent_at)
            self._index[mail.msg_id] = meta
            self._save_index()

    def list(self, folder: str | None = None) -> list[dict]:
        with self._lock:
            items = [
                dict(m) for m in self._index.values()
                if folder is None or m.get("folder") == folder
            ]
            return sorted(items, key=lambda m: m.get("created", 0), reverse=True)

    def counts(self) -> dict[str, int]:
        with self._lock:
            c = {f: 0 for f in Folder.ALL}
            for m in self._index.values():
                folder = m.get("folder", Folder.DRAFT)
                c[folder] = c.get(folder, 0) + 1
            return c

    def unread(self, folder: str) -> int:
        with self._lock:
            return sum(1 for m in self._index.values()
                       if m.get("folder") == folder and not m.get("read", True))

    def failed(self, folder: str = Folder.OUTBOX) -> int:
        """Messages parked in a folder because sending them did not work."""
        with self._lock:
            return sum(1 for m in self._index.values()
                       if m.get("folder") == folder
                       and m.get("status") == Status.FAILED)

    def awaiting_send(self, folder: str = Folder.OUTBOX) -> int:
        """Messages actually queued for transmission.

        A failed message stays in the outbox so it can be retried, but it is
        not waiting for anything -- counting it as pending leaves the station
        context reading "waiting to send: 1" forever with nothing in flight.
        """
        with self._lock:
            return sum(1 for m in self._index.values()
                       if m.get("folder") == folder
                       and m.get("status") != Status.FAILED)

    def mark_read(self, msg_id: int) -> None:
        with self._lock:
            meta = self._index.get(msg_id)
            if meta and not meta.get("read", True):
                meta["read"] = True
                self._save_index()

    def mark_sent(self, msg_id: int, at: float | None = None) -> float | None:
        """Record the first successful local outbound handoff time."""
        with self._lock:
            meta = self._index.get(msg_id)
            if not meta:
                return None
            existing = meta.get("sent_at")
            if existing:
                return float(existing)
            stamp = time.time() if at is None else float(at)
            meta["sent_at"] = stamp
            self._save_index()
            return stamp

    def get(self, msg_id: int) -> MailMessage | None:
        with self._lock:
            path = self._bundle_path(msg_id)
            if not path.exists():
                return None
            mail = MailMessage.from_bundle(path.read_bytes())
            meta = self._index.get(msg_id, {})
            mail.folder = meta.get("folder", Folder.INBOX)
            mail.status = meta.get("status", Status.RECEIVED)
            mail.next_hop = meta.get("next_hop", "")
            mail.read = meta.get("read", True)
            return mail

    def set_status(self, msg_id: int, *, status: str | None = None,
                   folder: str | None = None, next_hop: str | None = None) -> None:
        with self._lock:
            meta = self._index.get(msg_id)
            if not meta:
                return
            if status is not None:
                meta["status"] = status
            if folder is not None:
                meta["folder"] = folder
            if next_hop is not None:
                meta["next_hop"] = next_hop
            self._save_index()

    def delete(self, msg_id: int, *, folder: str | None = None) -> bool:
        with self._lock:
            indexed = self._index.get(msg_id)
            if folder is not None and (
                indexed is None or indexed.get("folder") != folder
            ):
                return False
            p = self._bundle_path(msg_id)
            if p.exists():
                # Keep the index entry until the file is gone. A locked or
                # otherwise undeletable bundle must remain visible and
                # retryable instead of silently disappearing from the UI.
                p.unlink()
            existed = indexed is not None
            if not existed:
                return False
            meta = self._index.pop(msg_id)
            try:
                self._save_index()
            except Exception:
                # Restore in-memory metadata when publishing the new index
                # fails. The next operation can retry the index write.
                self._index[msg_id] = meta
                raise
            return True

    def clear(self) -> int:
        """Delete every message: bundles on disk and the index.

        The id counter deliberately survives -- message ids reach other
        stations' session tables and dedup state, and a wiped mailbox is no
        reason to start minting ids the net has already seen from us.
        Returns how many indexed messages were removed.
        """
        with self._lock:
            removed = len(self._index)
            self._index.clear()
            for bundle in self.root.glob("*.bundle"):
                try:
                    bundle.unlink()
                except OSError:
                    pass    # a locked file keeps its bundle; the index entry goes
            self._save_index()
            return removed

    def store_incoming(self, bundle: bytes, my_callsign: str, *, via: str = "") -> MailMessage:
        """Persist a received bundle into Inbox (for me) or Transit (to relay)."""
        mail = MailMessage.from_bundle(bundle)
        if via and via not in mail.hops:
            mail.hops.append(via)
        mail.read = False   # incoming starts unread
        if mail.final_dest.strip().upper() == my_callsign.strip().upper():
            mail.folder, mail.status = Folder.INBOX, Status.RECEIVED
        else:
            mail.folder, mail.status = Folder.TRANSIT, Status.WAITING_PICKUP
        self.add(mail, received_at=time.time())
        return mail
