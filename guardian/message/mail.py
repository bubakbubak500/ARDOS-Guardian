"""Rich mail message + transferable bundle (text + attachments).

The bundle is a small ZIP so it handles binary attachments, compresses text,
and is self-describing — much like Winlink's compressed message format:

    manifest.json     metadata (ids, subject, priority, route hops, att list)
    body.txt          the message body (UTF-8)
    att/<filename>    one entry per attachment
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field

BUNDLE_VERSION = 1

# Rough on-air throughput (payload bytes/sec) for an estimate shown to the user.
# VARA FM ~ a few hundred B/s effective; HF much less. A conservative single
# number keeps the warning honest without pretending to be exact.
_EST_BYTES_PER_SEC = 250.0


class Folder:
    DRAFT = "draft"
    OUTBOX = "outbox"
    SENT = "sent"
    INBOX = "inbox"
    TRANSIT = "transit"     # held to forward for someone else
    ALL = (DRAFT, OUTBOX, SENT, INBOX, TRANSIT)


class Status:
    DRAFT = "draft"
    QUEUED = "queued"
    SENDING = "sending"
    DELIVERED = "delivered"      # confirmed to next hop / end-to-end
    RECEIVED = "received"        # arrived for me
    WAITING_PICKUP = "waiting"   # in transit, awaiting onward hop
    FORWARDED = "forwarded"      # relayed onward
    FAILED = "failed"


@dataclass
class Attachment:
    name: str
    data: bytes = b""

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass
class MailMessage:
    msg_id: int
    source: str
    final_dest: str
    subject: str = ""
    body: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    priority: int = 0
    created: float = 0.0
    hops: list[str] = field(default_factory=list)   # route history (callsigns)

    # Local-only (not transferred) — managed by the store.
    folder: str = Folder.DRAFT
    status: str = Status.DRAFT
    next_hop: str = ""

    # ------------------------------------------------------------------ #
    def content_size(self) -> int:
        return len(self.body.encode("utf-8")) + sum(a.size for a in self.attachments)

    def est_seconds(self) -> float:
        return round(len(self.to_bundle()) / _EST_BYTES_PER_SEC, 1)

    def summary(self) -> str:
        a = f" +{len(self.attachments)} att" if self.attachments else ""
        return f"#{self.msg_id} {self.source}->{self.final_dest} \"{self.subject}\"{a}"

    # ------------------------------------------------------------------ #
    def to_bundle(self) -> bytes:
        manifest = {
            "v": BUNDLE_VERSION,
            "msg_id": self.msg_id,
            "source": self.source,
            "final_dest": self.final_dest,
            "subject": self.subject,
            "priority": self.priority,
            "created": self.created,
            "hops": self.hops,
            "attachments": [a.name for a in self.attachments],
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("body.txt", self.body.encode("utf-8"))
            for a in self.attachments:
                zf.writestr(f"att/{a.name}", a.data)
        return buf.getvalue()

    @classmethod
    def from_bundle(cls, data: bytes) -> "MailMessage":
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            body = zf.read("body.txt").decode("utf-8", errors="replace") if "body.txt" in zf.namelist() else ""
            atts = []
            for name in manifest.get("attachments", []):
                arc = f"att/{name}"
                if arc in zf.namelist():
                    atts.append(Attachment(name=name, data=zf.read(arc)))
        return cls(
            msg_id=int(manifest.get("msg_id", 0)),
            source=manifest.get("source", ""),
            final_dest=manifest.get("final_dest", ""),
            subject=manifest.get("subject", ""),
            body=body,
            attachments=atts,
            priority=int(manifest.get("priority", 0)),
            created=float(manifest.get("created", 0.0)),
            hops=list(manifest.get("hops", [])),
        )
