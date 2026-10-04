"""Public v1 bundle validation shared by independently deployed peers."""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import zipfile

PROTOCOL_VERSION = 1
MAX_BUNDLE = 1024 * 1024
MAX_EXPANDED = 4 * 1024 * 1024
MAX_ENTRIES = 66


def callsign(value: str) -> str:
    value = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9/]{3,16}", value) or value == "NOCALL":
        raise ValueError("invalid_callsign")
    return value


def inspect_bundle(data: bytes) -> dict:
    """Bound decompression before parsing; never extract or execute attachments."""
    if len(data) > MAX_BUNDLE:
        raise ValueError("too_large")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if (len(entries) > MAX_ENTRIES or len(names) != len(set(names))
                    or sum(entry.file_size for entry in entries) > MAX_EXPANDED):
                raise ValueError("too_large")
            if any(entry.flag_bits & 1 or entry.compress_type not in
                   (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) for entry in entries):
                raise ValueError("unsupported_bundle")
            if archive.getinfo("manifest.json").file_size > 16384:
                raise ValueError("too_large")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("v") != 1:
                raise ValueError("unsupported_bundle")
            mid = manifest.get("msg_id")
            if type(mid) is not int or not 0 <= mid <= 0xFFFFFFFF:
                raise ValueError("invalid_message_id")
            for key in ("source", "final_dest"):
                if manifest[key] != callsign(manifest[key]):
                    raise ValueError("invalid_callsign")
            attachments = manifest.get("attachments", [])
            if not isinstance(attachments, list) or len(attachments) > 64:
                raise ValueError("invalid_attachments")
            if any(not isinstance(name, str) or not name or name in (".", "..")
                   or any(c in name for c in ("/", "\\", "\x00", ":"))
                   for name in attachments) or len(set(attachments)) != len(attachments):
                raise ValueError("invalid_attachments")
            expected = {"manifest.json", "body.txt", *(f"att/{n}" for n in attachments)}
            if set(names) != expected:
                raise ValueError("invalid_entries")
            if not isinstance(manifest.get("subject"), str) or len(manifest["subject"]) > 4096:
                raise ValueError("invalid_subject")
            if type(manifest.get("priority")) is not int or not 0 <= manifest["priority"] <= 255:
                raise ValueError("invalid_priority")
            if not isinstance(manifest.get("created"), (int, float)) or not math.isfinite(manifest["created"]):
                raise ValueError("invalid_created")
            if (not isinstance(manifest.get("hops"), list) or len(manifest["hops"]) > 64
                    or any(not isinstance(h, str) or len(h) > 32 for h in manifest["hops"])):
                raise ValueError("invalid_hops")
            # Reading all bounded entries also verifies each CRC, including attachments.
            content = {name: archive.read(name) for name in names}
            content["body.txt"].decode("utf-8")
            # Transport hops and ZIP timestamps/compression are not message identity.
            identity = {k: v for k, v in manifest.items() if k != "hops"}
            identity["created"] = float(identity["created"])
            digest = hashlib.sha256(json.dumps(identity, sort_keys=True,
                                              separators=(",", ":")).encode())
            for name in sorted(set(names) - {"manifest.json"}):
                digest.update(name.encode() + b"\0")
                digest.update(len(content[name]).to_bytes(8, "big"))
                digest.update(content[name])
            return {**manifest, "content_hash": digest.hexdigest(),
                    "bundle_hash": hashlib.sha256(data).hexdigest()}
    except (KeyError, TypeError, zipfile.BadZipFile, UnicodeError, RuntimeError) as exc:
        raise ValueError("invalid_bundle") from exc
