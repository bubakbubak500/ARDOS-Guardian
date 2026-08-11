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

from ..compression import (
    decompress_guardian_envelope,
    external_candidates,
    is_guardian_envelope,
)

BUNDLE_VERSION = 1

# Rough on-air throughput (payload bytes/sec) for an estimate shown to the user.
# VARA FM ~ a few hundred B/s effective; HF much less. A conservative single
# number keeps the warning honest without pretending to be exact.
_EST_BYTES_PER_SEC = 250.0


@dataclass(frozen=True)
class BundleEncoding:
    """A bundle plus the lossless ZIP method chosen for it."""

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

    @staticmethod
    def _best_zip_method(data: bytes) -> tuple[int, int | None]:
        """Return the ZIP method with the smallest exact compressed payload."""
        methods = (
            (zipfile.ZIP_STORED, None),
            (zipfile.ZIP_DEFLATED, 9),
            (zipfile.ZIP_BZIP2, 9),
            (zipfile.ZIP_LZMA, None),
        )
        scored: list[tuple[int, int, int | None]] = []
        for method, level in methods:
            probe = io.BytesIO()
            with zipfile.ZipFile(probe, "w") as zf:
                options = {"compress_type": method}
                if level is not None:
                    options["compresslevel"] = level
                zf.writestr("entry", data, **options)
                compressed_size = zf.getinfo("entry").compress_size
            scored.append((compressed_size, method, level))
        _, method, level = min(scored, key=lambda item: item[0])
        return method, level

    def _adaptive_mixed_bundle(self) -> bytes:
        """Build a Guardian bundle selecting a codec independently per entry."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, data in self._bundle_entries():
                method, level = self._best_zip_method(data)
                options = {"compress_type": method}
                if level is not None:
                    options["compresslevel"] = level
                zf.writestr(name, data, **options)
        return buf.getvalue()

    def to_bundle(self) -> bytes:
        """Return the stable baseline bundle used for local mailbox storage."""
        return self._bundle_with(zipfile.ZIP_DEFLATED)

    def to_adaptive_bundle(self, *, include_high_ratio: bool = True) -> BundleEncoding:
        """Choose the smallest interoperable lossless ZIP representation.

        Python's ZIP reader already understands every candidate, so an older
        Guardian can receive and open the result without a new envelope or
        on-air negotiation. That is important for relays and for a station that
        falls back from OFDM to VARA after the message was announced.
        """
        baseline = self.to_bundle()
        candidates = [
            ("stored", self._bundle_with(zipfile.ZIP_STORED)),
            ("deflate", baseline),
            ("deflate-max", self._bundle_with(zipfile.ZIP_DEFLATED, compresslevel=9)),
            ("bzip2", self._bundle_with(zipfile.ZIP_BZIP2, compresslevel=9)),
            ("lzma", self._bundle_with(zipfile.ZIP_LZMA)),
            ("adaptive-mixed", self._adaptive_mixed_bundle()),
        ]
        if include_high_ratio:
            candidates.extend(external_candidates(baseline))
        method, data = min(candidates, key=lambda item: len(item[1]))
        return BundleEncoding(data=data, method=method, baseline_size=len(baseline))

    @classmethod
    def from_bundle(cls, data: bytes) -> "MailMessage":
        if is_guardian_envelope(data):
            data = decompress_guardian_envelope(data)
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
