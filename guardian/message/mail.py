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
from pathlib import PurePosixPath

BUNDLE_VERSION = 1

# Rough on-air throughput (payload bytes/sec) for an estimate shown to the user.
# VARA FM ~ a few hundred B/s effective; HF much less. A conservative single
# number keeps the warning honest without pretending to be exact.
_EST_BYTES_PER_SEC = 250.0


@dataclass(frozen=True)
class BundleEncoding:
    """The single Guardian BZIP2 result or its standard ZIP fallback."""

    data: bytes
    method: str
    baseline_size: int

    @property
    def saved_bytes(self) -> int:
        return max(0, self.baseline_size - len(self.data))

    @property
    def saved_percent(self) -> float:
        if not self.baseline_size:
            return 0.0
        return 100.0 * self.saved_bytes / self.baseline_size


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
    DELIVERED = "delivered"      # confirmed by the final destination
    RECEIVED = "received"        # arrived for me
    WAITING_PICKUP = "waiting"   # in transit, awaiting onward hop
    FORWARDED = "forwarded"      # next relay holds it; final receipt pending
    FAILED = "failed"


def safe_attachment_name(name: str) -> str:
    """Reduce a peer-supplied attachment name to a bare, safe filename.

    Attachment names arrive from another station.  Unchecked they go straight
    into the bundle's zip paths, where "..\\..\\evil.txt" both escapes the
    archive for anything that extracts it and fails to round-trip back through
    from_bundle(), silently losing the attachment.
    """
    cleaned = PurePosixPath(str(name).replace("\\", "/")).name.strip()
    if cleaned in ("", ".", ".."):
        return "attachment"
    return cleaned


def _unique_name(name: str, taken: set[str]) -> str:
    """Keep two attachments that sanitise to the same name distinguishable."""
    if name not in taken:
        taken.add(name)
        return name
    stem, dot, suffix = name.rpartition(".")
    base, extension = (stem, f".{suffix}") if dot else (name, "")
    index = 2
    while f"{base}-{index}{extension}" in taken:
        index += 1
    unique = f"{base}-{index}{extension}"
    taken.add(unique)
    return unique


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
    read: bool = True

    # ------------------------------------------------------------------ #
    def content_size(self) -> int:
        return len(self.body.encode("utf-8")) + sum(a.size for a in self.attachments)

    def est_seconds(self) -> float:
        return round(len(self.to_bundle()) / _EST_BYTES_PER_SEC, 1)

    def summary(self) -> str:
        a = f" +{len(self.attachments)} att" if self.attachments else ""
        return f"#{self.msg_id} {self.source}->{self.final_dest} \"{self.subject}\"{a}"

    # ------------------------------------------------------------------ #
    def _bundle_entries(self) -> list[tuple[str, bytes]]:
        taken: set[str] = set()
        names = [
            _unique_name(safe_attachment_name(a.name), taken)
            for a in self.attachments
        ]
        manifest = {
            "v": BUNDLE_VERSION,
            "msg_id": self.msg_id,
            "source": self.source,
            "final_dest": self.final_dest,
            "subject": self.subject,
            "priority": self.priority,
            "created": self.created,
            "hops": self.hops,
            "attachments": names,
        }
        entries = [
            ("manifest.json", json.dumps(manifest).encode("utf-8")),
            ("body.txt", self.body.encode("utf-8")),
        ]
        entries.extend(
            (f"att/{name}", attachment.data)
            for name, attachment in zip(names, self.attachments)
        )
        return entries

    def _bundle_with(self, compression: int, *, compresslevel: int | None = None) -> bytes:
        buf = io.BytesIO()
        options = {"compression": compression}
        if compresslevel is not None:
            options["compresslevel"] = compresslevel
        with zipfile.ZipFile(buf, "w", **options) as zf:
            for name, data in self._bundle_entries():
                zf.writestr(name, data)
        return buf.getvalue()

    def to_bundle(self) -> bytes:
        """Return the stable baseline bundle used for local mailbox storage."""
        return self._bundle_with(zipfile.ZIP_DEFLATED)

    def to_guardian_bundle(self, baseline: bytes | None = None) -> BundleEncoding:
        """Use one BZIP2 pass only when it beats the standard ZIP bundle."""
        standard = baseline if baseline is not None else self.to_bundle()
        compressed = self._bundle_with(zipfile.ZIP_BZIP2, compresslevel=9)
        if len(compressed) < len(standard):
            return BundleEncoding(
                data=compressed,
                method="bzip2",
                baseline_size=len(standard),
            )
        return BundleEncoding(
            data=standard,
            method="standard",
            baseline_size=len(standard),
        )

    @classmethod
    def from_bundle(cls, data: bytes) -> "MailMessage":
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            body = zf.read("body.txt").decode("utf-8", errors="replace") if "body.txt" in zf.namelist() else ""
            atts = []
            entries = set(zf.namelist())
            # Never trust the manifest: sanitise again on the way in, so a
            # hand-crafted bundle cannot steer a name back out of att/.
            for name in manifest.get("attachments", []):
                safe = safe_attachment_name(name)
                arc = f"att/{safe}"
                if arc in entries:
                    atts.append(Attachment(name=safe, data=zf.read(arc)))
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
