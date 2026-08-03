"""Persistent mailbox.

Each message is stored as a `.bundle` file (the transferable ZIP) plus a
lightweight entry in `index.json` (folder, status, next hop, headers) so the
folder lists render without unpacking every bundle.
"""

from __future__ import annotations

import json
from pathlib import Path

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
        self.load()

    # ------------------------------------------------------------------ #
    def load(self) -> None:
        if self.index_path.exists():
            try:
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                self._index = {int(k): v for k, v in data.get("messages", {}).items()}
                self._counter = int(data.get("counter", 0))
            except (json.JSONDecodeError, OSError, ValueError):
                self._index = {}

    def _save_index(self) -> None:
        payload = {"counter": self._counter,
                   "messages": {str(k): v for k, v in self._index.items()}}
        self.index_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _bundle_path(self, msg_id: int) -> Path:
        return self.root / f"{msg_id}.bundle"

    def _meta(self, mail: MailMessage, size: int) -> dict:
        return {
            "msg_id": mail.msg_id, "source": mail.source, "final_dest": mail.final_dest,
            "subject": mail.subject, "priority": mail.priority, "created": mail.created,
            "folder": mail.folder, "status": mail.status, "next_hop": mail.next_hop,
            "hops": mail.hops, "size": size, "att": len(mail.attachments),
            "read": getattr(mail, "read", True),
        }

    # ------------------------------------------------------------------ #
    def next_id(self, callsign: str = "") -> int:
        """Globally-unique-ish 32-bit id: station-hash prefix + counter.

        12-bit station hash (4096 stations) << 20 | 20-bit per-station counter,
        so two different stations almost never mint the same id.
        """
        self._counter += 1
        prefix = crc16(callsign.strip().upper().encode("ascii", "replace")) & 0xFFF
        mid = ((prefix << 20) | (self._counter & 0xFFFFF)) & 0xFFFFFFFF
        self._save_index()
        return mid

    def add(self, mail: MailMessage) -> None:
        bundle = mail.to_bundle()
        self._bundle_path(mail.msg_id).write_bytes(bundle)
        self._index[mail.msg_id] = self._meta(mail, len(bundle))
        self._save_index()

    def list(self, folder: str | None = None) -> list[dict]:
        items = [m for m in self._index.values() if folder is None or m.get("folder") == folder]
        return sorted(items, key=lambda m: m.get("created", 0), reverse=True)

    def counts(self) -> dict[str, int]:
        c = {f: 0 for f in Folder.ALL}
        for m in self._index.values():
            c[m.get("folder", Folder.DRAFT)] = c.get(m.get("folder", Folder.DRAFT), 0) + 1
        return c

    def unread(self, folder: str) -> int:
        return sum(1 for m in self._index.values()
                   if m.get("folder") == folder and not m.get("read", True))

    def failed(self, folder: str = Folder.OUTBOX) -> int:
        """Messages parked in a folder because sending them did not work."""
        return sum(1 for m in self._index.values()
                   if m.get("folder") == folder
                   and m.get("status") == Status.FAILED)

    def awaiting_send(self, folder: str = Folder.OUTBOX) -> int:
        """Messages actually queued for transmission.

        A failed message stays in the outbox so it can be retried, but it is
        not waiting for anything -- counting it as pending leaves the station
        context reading "waiting to send: 1" forever with nothing in flight.
        """
        return sum(1 for m in self._index.values()
                   if m.get("folder") == folder
                   and m.get("status") != Status.FAILED)

    def mark_read(self, msg_id: int) -> None:
        meta = self._index.get(msg_id)
        if meta and not meta.get("read", True):
            meta["read"] = True
            self._save_index()

    def get(self, msg_id: int) -> MailMessage | None:
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

    def delete(self, msg_id: int) -> None:
        self._index.pop(msg_id, None)
        p = self._bundle_path(msg_id)
        if p.exists():
            p.unlink()
        self._save_index()

    def clear(self) -> int:
        """Delete every message: bundles on disk and the index.

        The id counter deliberately survives -- message ids reach other
        stations' session tables and dedup state, and a wiped mailbox is no
        reason to start minting ids the net has already seen from us.
        Returns how many indexed messages were removed.
        """
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
        self.add(mail)
        return mail
