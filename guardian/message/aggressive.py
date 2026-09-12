"""Optional high-density payload encoding for Guardian.

The existing ZIP and BZIP2 paths deliberately remain untouched.  This module
implements the separately selected G2XZ1 envelope:

* a stored ZIP keeps message metadata and attachment boundaries explicit;
* JPEG attachments may be losslessly transcoded by libjxl and are verified by
  reconstructing the original JPEG before anything is put on air;
* PNG IDAT streams are recompressed with Zopfli while preserving the exact
  decompressed scanline stream (pixel-lossless, but not byte-identical PNG);
* the complete candidate is compressed as XZ/LZMA2 preset 7.

The caller still compares the envelope with the ordinary bundle and uses it
only when it is smaller.
"""

from __future__ import annotations

import hashlib
import io
import json
import lzma
import multiprocessing
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .mail import MailMessage


MAGIC = b"G2XZ1\r\n"
FORMAT_VERSION = 1
XZ_PRESET = 7
MAX_ENVELOPE_BYTES = 256 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
OPTIMIZATION_BUDGET_SECONDS = 45.0
ENCODING_TIMEOUT_SECONDS = 50.0


@dataclass(frozen=True)
class AggressiveResult:
    data: bytes
    details: tuple[str, ...]


def _tool_candidates(name: str) -> list[Path]:
    executable = f"{name}.exe" if os.name == "nt" else name
    candidates: list[Path] = []
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidates.append(Path(frozen_root) / "jpegxl" / executable)
    candidates.append(
        Path(sys.executable).resolve().parent / "jpegxl" / executable
    )
    project_root = Path(__file__).resolve().parents[2]
    candidates.append(
        project_root / "codecs" / "vendor" / "jpegxl" / "bin" / executable
    )
    discovered = shutil.which(executable)
    if discovered:
        candidates.append(Path(discovered))
    return candidates


def find_jpegxl_tool(name: str) -> Path | None:
    """Find a bundled libjxl CLI, with PATH as a source-tree fallback."""
    return next(
        (candidate for candidate in _tool_candidates(name) if candidate.is_file()),
        None,
    )


def _run_tool(command: list[str], timeout: float) -> None:
    if timeout <= 0:
        raise TimeoutError("image optimization time budget exhausted")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=max(0.1, timeout),
        check=False,
        creationflags=creationflags,
    )
    if completed.returncode:
        error = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            error or f"{Path(command[0]).name} exited {completed.returncode}"
        )


def jpeg_to_jxl(original: bytes, *, timeout: float) -> bytes:
    """Losslessly transcode JPEG and prove bit-for-bit reconstruction."""
    encoder = find_jpegxl_tool("cjxl")
    decoder = find_jpegxl_tool("djxl")
    if encoder is None or decoder is None:
        raise FileNotFoundError("bundled cjxl/djxl are unavailable")
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="guardian-jxl-") as temporary:
        root = Path(temporary)
        source = root / "source.jpg"
        encoded = root / "payload.jxl"
        restored = root / "restored.jpg"
        source.write_bytes(original)
        _run_tool(
            [str(encoder), str(source), str(encoded), "--lossless_jpeg=1", "--effort=7"],
            timeout,
        )
        remaining = timeout - (time.monotonic() - started)
        _run_tool(
            [str(decoder), str(encoded), str(restored), "--reconstruct_jpeg"],
            remaining,
        )
        rebuilt = restored.read_bytes()
        if rebuilt != original:
            raise ValueError("libjxl did not reconstruct the original JPEG bit-for-bit")
        return encoded.read_bytes()


def jxl_to_jpeg(encoded: bytes, *, expected_size: int, expected_sha256: str) -> bytes:
    """Restore the original JPEG and enforce its recorded size and checksum."""
    decoder = find_jpegxl_tool("djxl")
    if decoder is None:
        raise FileNotFoundError("bundled djxl is unavailable")
    if expected_size < 0 or expected_size > MAX_ENVELOPE_BYTES:
        raise ValueError("invalid reconstructed JPEG size")
    with tempfile.TemporaryDirectory(prefix="guardian-jxl-") as temporary:
        root = Path(temporary)
        source = root / "payload.jxl"
        restored = root / "restored.jpg"
        source.write_bytes(encoded)
        _run_tool(
            [str(decoder), str(source), str(restored), "--reconstruct_jpeg"],
            OPTIMIZATION_BUDGET_SECONDS,
        )
        original = restored.read_bytes()
    if len(original) != expected_size:
        raise ValueError("reconstructed JPEG size does not match its manifest")
    if hashlib.sha256(original).hexdigest() != expected_sha256:
        raise ValueError("reconstructed JPEG checksum does not match its manifest")
    return original


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def optimize_png_zopfli(original: bytes) -> bytes:
    """Recompress a PNG's IDAT stream with Zopfli without changing scanlines.

    This retains every non-IDAT chunk in its original order.  The decoded PNG
    is therefore image-lossless, while IDAT layout/CRC bytes are intentionally
    different and the original file checksum is not promised.
    """
    if not original.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG stream")
    chunks: list[tuple[bytes, bytes, bytes]] = []
    idat = bytearray()
    position = 8
    saw_ihdr = False
    saw_iend = False
    while position < len(original):
        if position + 12 > len(original):
            raise ValueError("truncated PNG chunk")
        length = struct.unpack(">I", original[position:position + 4])[0]
        end = position + 12 + length
        if end > len(original):
            raise ValueError("truncated PNG payload")
        kind = original[position + 4:position + 8]
        payload = original[position + 8:position + 8 + length]
        raw = original[position:end]
        expected_crc = struct.unpack(">I", raw[-4:])[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != expected_crc:
            raise ValueError("PNG chunk CRC mismatch")
        saw_ihdr |= kind == b"IHDR"
        saw_iend |= kind == b"IEND"
        if kind == b"IDAT":
            idat.extend(payload)
        chunks.append((kind, payload, raw))
        position = end
        if kind == b"IEND":
            break
    if not saw_ihdr or not saw_iend or not idat or position != len(original):
        raise ValueError("incomplete PNG stream")

    scanlines = zlib.decompress(bytes(idat))
    from zopfli.zlib import compress as zopfli_compress

    iterations = 8 if len(scanlines) <= 8 * 1024 * 1024 else 5
    compressed = zopfli_compress(
        scanlines,
        numiterations=iterations,
        blocksplitting=1,
        blocksplittinglast=0,
        blocksplittingmax=15,
    )
    if zlib.decompress(compressed) != scanlines:
        raise ValueError("Zopfli changed the PNG scanline stream")

    output = bytearray(original[:8])
    wrote_idat = False
    for kind, _payload, raw in chunks:
        if kind == b"IDAT":
            if not wrote_idat:
                output.extend(_png_chunk(b"IDAT", compressed))
                wrote_idat = True
            continue
        output.extend(raw)
    optimized = bytes(output)
    return optimized if len(optimized) < len(original) else original


def _stored_zip(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def encode_aggressive(mail: "MailMessage") -> AggressiveResult:
    """Build the G2XZ1 candidate for a message."""
    deadline = time.monotonic() + OPTIMIZATION_BUDGET_SECONDS
    taken: set[str] = set()
    # Import here to avoid a module cycle at import time.
    from .mail import _unique_name, safe_attachment_name

    names = [
        _unique_name(safe_attachment_name(attachment.name), taken)
        for attachment in mail.attachments
    ]
    descriptors: list[dict[str, object]] = []
    payload_entries: list[tuple[str, bytes]] = []
    details: list[str] = []
    for index, (name, attachment) in enumerate(zip(names, mail.attachments)):
        original = attachment.data
        suffix = Path(name).suffix.casefold()
        codec = "raw"
        payload = original
        payload_name = f"att/{index:04d}.bin"
        image_budget_available = time.monotonic() < deadline
        if (
            image_budget_available
            and len(original) <= MAX_IMAGE_BYTES
            and suffix in {".jpg", ".jpeg"}
        ):
            try:
                candidate = jpeg_to_jxl(
                    original, timeout=max(0.1, deadline - time.monotonic())
                )
                if len(candidate) < len(original):
                    codec = "jpeg-xl-jpeg"
                    payload = candidate
                    payload_name = f"att/{index:04d}.jxl"
                    details.append(
                        f"JPEG XL {name}: {len(original)}→{len(candidate)} B"
                    )
            except (
                FileNotFoundError,
                RuntimeError,
                OSError,
                ValueError,
                TimeoutError,
                subprocess.TimeoutExpired,
            ):
                details.append(f"JPEG XL skipped {name}")
        elif (
            image_budget_available
            and len(original) <= MAX_IMAGE_BYTES
            and suffix == ".png"
        ):
            try:
                candidate = optimize_png_zopfli(original)
                if len(candidate) < len(original):
                    codec = "zopfli-png"
                    payload = candidate
                    payload_name = f"att/{index:04d}.png"
                    details.append(
                        f"ZopfliPNG {name}: {len(original)}→{len(candidate)} B"
                    )
            except (ImportError, OSError, ValueError, zlib.error):
                details.append(f"ZopfliPNG skipped {name}")
        descriptors.append({
            "name": name,
            "codec": codec,
            "payload": payload_name,
            "original_size": len(original),
            "original_sha256": hashlib.sha256(original).hexdigest(),
            "payload_size": len(payload),
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
        })
        payload_entries.append((payload_name, payload))

    manifest = {
        "format": FORMAT_VERSION,
        "msg_id": mail.msg_id,
        "source": mail.source,
        "final_dest": mail.final_dest,
        "subject": mail.subject,
        "priority": mail.priority,
        "created": mail.created,
        "hops": mail.hops,
        "attachments": descriptors,
    }
    entries = [
        ("manifest.json", json.dumps(manifest, separators=(",", ":")).encode("utf-8")),
        ("body.txt", mail.body.encode("utf-8")),
        *payload_entries,
    ]
    inner = _stored_zip(entries)
    if len(inner) > MAX_ENVELOPE_BYTES:
        raise ValueError("aggressive bundle exceeds the safe expanded size")
    compressed = lzma.compress(
        inner,
        format=lzma.FORMAT_XZ,
        check=lzma.CHECK_CRC64,
        preset=XZ_PRESET,
    )
    details.append(
        f"XZ/LZMA2 preset {XZ_PRESET}: {len(inner)}→{len(compressed)} B"
    )
    return AggressiveResult(MAGIC + compressed, tuple(details))


def _encode_in_child(mail: "MailMessage", connection) -> None:
    """Frozen-process target; return only picklable data over the pipe."""
    try:
        connection.send((True, encode_aggressive(mail)))
    except Exception as exc:
        connection.send((False, f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


def encode_aggressive_bounded(mail: "MailMessage") -> AggressiveResult:
    """Enforce a sub-minute wall-clock bound in the shipped Windows app.

    PyInstaller's multiprocessing runtime hook and guardian_launch's early
    freeze_support() allow the CPU-heavy encoder to be terminated cleanly.
    Source/test runs stay in-process so monkeypatching and tracebacks remain
    useful to developers.
    """
    if not getattr(sys, "frozen", False):
        return encode_aggressive(mail)
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_encode_in_child, args=(mail, sender), daemon=True
    )
    process.start()
    sender.close()
    try:
        if not receiver.poll(ENCODING_TIMEOUT_SECONDS):
            process.terminate()
            process.join(timeout=2.0)
            raise TimeoutError(
                f"aggressive encoding exceeded {ENCODING_TIMEOUT_SECONDS:.0f} seconds"
            )
        ok, value = receiver.recv()
        process.join(timeout=2.0)
        if not ok:
            raise RuntimeError(str(value))
        return value
    finally:
        receiver.close()
        if process.is_alive():
            process.terminate()
            process.join(timeout=2.0)


def _decompress_envelope(data: bytes) -> bytes:
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
    unpacked = decoder.decompress(
        data[len(MAGIC):], max_length=MAX_ENVELOPE_BYTES + 1
    )
    if len(unpacked) > MAX_ENVELOPE_BYTES or not decoder.eof:
        raise ValueError("aggressive bundle exceeds the safe expanded size")
    if decoder.unused_data:
        raise ValueError("aggressive bundle has trailing data")
    return unpacked


MAX_MANIFEST_BYTES = 16 * 1024


def read_aggressive_manifest(
    data: bytes, *, max_bytes: int = MAX_MANIFEST_BYTES
) -> dict[str, object] | None:
    """Read only the metadata entry from a G2XZ1 envelope.

    VARA uses this during transfer setup to label a relay.  The complete
    aggressive decoder restores JPEG XL attachments, which is both unnecessary
    for that label and an avoidable second decode on the eventual mail path.
    Keep the same expanded-envelope and stored-entry limits as
    :func:`decode_aggressive`, then bound and parse exactly one manifest.
    Malformed, duplicated, or over-sized metadata returns ``None`` so context
    display can fail closed without affecting the RF transfer.
    """
    try:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            return None
        raw_data = bytes(data)
        if not raw_data.startswith(MAGIC):
            return None
        if max_bytes < 0 or max_bytes > MAX_ENVELOPE_BYTES:
            return None
        unpacked = _decompress_envelope(raw_data)
        with zipfile.ZipFile(io.BytesIO(unpacked), "r") as archive:
            entries = archive.infolist()
            if any(entry.compress_type != zipfile.ZIP_STORED for entry in entries):
                return None
            if sum(max(0, entry.file_size) for entry in entries) > MAX_ENVELOPE_BYTES:
                return None
            manifests = [
                entry for entry in entries if entry.filename == "manifest.json"
            ]
            if len(manifests) != 1:
                return None
            info = manifests[0]
            if info.file_size < 0 or info.file_size > max_bytes:
                return None
            if info.compress_size < 0 or info.compress_size > max_bytes:
                return None
            manifest_raw = archive.read(info)
            if len(manifest_raw) > max_bytes:
                return None
            manifest = json.loads(manifest_raw.decode("utf-8"))
            if not isinstance(manifest, dict):
                return None
            if int(manifest.get("format", 0)) != FORMAT_VERSION:
                return None
            return manifest
    except Exception:  # noqa: BLE001 - metadata is best-effort
        return None


def decode_aggressive(data: bytes) -> dict[str, object]:
    """Decode a G2XZ1 envelope and restore attachment objects."""
    if not data.startswith(MAGIC):
        raise ValueError("not a G2XZ1 envelope")
    unpacked = _decompress_envelope(data)
    with zipfile.ZipFile(io.BytesIO(unpacked), "r") as archive:
        entries = archive.infolist()
        if any(entry.compress_type != zipfile.ZIP_STORED for entry in entries):
            raise ValueError("aggressive bundle contains a compressed inner entry")
        if sum(entry.file_size for entry in entries) > MAX_ENVELOPE_BYTES:
            raise ValueError("aggressive bundle entries exceed the safe expanded size")
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("invalid aggressive bundle manifest")
        if int(manifest.get("format", 0)) != FORMAT_VERSION:
            raise ValueError("unsupported aggressive bundle version")
        body = archive.read("body.txt").decode("utf-8", errors="replace")
        restored: list[tuple[str, bytes]] = []
        descriptors = manifest.get("attachments", [])
        if not isinstance(descriptors, list):
            raise ValueError("invalid aggressive attachment list")
        for descriptor in descriptors:
            if not isinstance(descriptor, dict):
                raise ValueError("invalid aggressive attachment descriptor")
            name = str(descriptor.get("name", ""))
            payload_name = str(descriptor.get("payload", ""))
            payload_path = PurePosixPath(payload_name)
            if (
                payload_path.is_absolute()
                or not payload_path.parts
                or payload_path.parts[0] != "att"
                or ".." in payload_path.parts
            ):
                raise ValueError("invalid aggressive attachment path")
            payload = archive.read(payload_name)
            payload_size = int(descriptor.get("payload_size", -1))
            payload_sha256 = str(descriptor.get("payload_sha256", ""))
            if (
                len(payload) != payload_size
                or hashlib.sha256(payload).hexdigest() != payload_sha256
            ):
                raise ValueError("attachment payload does not match its manifest")
            codec = str(descriptor.get("codec", "raw"))
            expected_size = int(descriptor.get("original_size", -1))
            expected_sha256 = str(descriptor.get("original_sha256", ""))
            if codec == "jpeg-xl-jpeg":
                attachment = jxl_to_jpeg(
                    payload,
                    expected_size=expected_size,
                    expected_sha256=expected_sha256,
                )
            elif codec in {"raw", "zopfli-png"}:
                attachment = payload
                if codec == "raw" and (
                    len(attachment) != expected_size
                    or hashlib.sha256(attachment).hexdigest() != expected_sha256
                ):
                    raise ValueError("raw attachment does not match its manifest")
            else:
                raise ValueError(f"unsupported aggressive attachment codec: {codec}")
            restored.append((name, attachment))
    return {"manifest": manifest, "body": body, "attachments": restored}
