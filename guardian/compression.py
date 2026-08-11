"""Guardian's optional high-ratio compression envelope.

The ordinary bundle remains a standard ZIP.  ZPAQ, PAQ8PX and LPAQ8 cannot be
represented as ZIP methods, so the winning result is wrapped in a small,
versioned envelope carrying the exact decoder, original size and SHA-256.  The
control-channel handshake only permits this envelope when the next hop actively
advertises a decoder; legacy peers receive the ordinary ZIP fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import platform
import struct
import subprocess
import tempfile
import threading
import zipfile


MAGIC = b"GCP1"
_HEADER = struct.Struct(">4sBBQ32s")
_MAX_DECOMPRESSED = 256 * 1024 * 1024
_COMPRESS_TIMEOUT = 20.0
_CANDIDATE_VERIFY_TIMEOUT = 12.0
_DECOMPRESS_TIMEOUT = 90.0
_EXTRACT_LOCK = threading.Lock()


@dataclass(frozen=True)
class CodecSpec:
    codec_id: int
    name: str
    profile: int
    archive: str
    archive_sha256: str
    executable: str
    executable_sha256: str


CODECS = (
    CodecSpec(
        1,
        "zpaq-1",
        1,
        "zpaq715.zip",
        "e85ec2529eb0ba22ceaeabd461e55357ef099b80f61c14f377b429ea3d49d418",
        "zpaq64.exe",
        "7a94b4f1d6323a758c7b0b6344036f166bff0fd44f1c3c86f05b3688023496cb",
    ),
    CodecSpec(
        2,
        "paq8px187-1",
        1,
        "paq8px187.zip",
        "514060a7c9bd1bb20a00228dbc03069aae193c477378c1c96a38c9e838abd800",
        "paq8px_v187_x86_64_windows.exe",
        "e04a58f19416d2c1b2fdf3e4272de5c6dcd615d1d4f310a7d703e74af125b080",
    ),
    CodecSpec(
        3,
        "lpaq8-0",
        0,
        "lpaq8.zip",
        "ea43474526f13338cbb50ce3fbd974a0d088d77a3b73d42010ad11fb89a498b2",
        "lpaq8.exe",
        "52674de2e06ecc07ae9c4101de73203bb04a916b42ef2a4b71f403294441b227",
    ),
)
_BY_ID = {codec.codec_id: codec for codec in CODECS}


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _vendor_dir() -> Path:
    return Path(__file__).resolve().parent / "codecs" / "vendor"


def _checked_archive(spec: CodecSpec) -> Path:
    archive = _vendor_dir() / spec.archive
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != spec.archive_sha256:
        raise RuntimeError(f"Guardian codec archive checksum failed: {spec.archive}")
    return archive


@lru_cache(maxsize=1)
def external_codecs_available() -> bool:
    """Whether this build can decode a Guardian high-ratio envelope."""
    if platform.system() != "Windows":
        return False
    try:
        for spec in CODECS:
            # Do not advertise over the air merely because the ZIP exists.
            # Extraction and the inner executable checksum must work too.
            _tool(spec)
    except OSError:
        return False
    except RuntimeError:
        return False
    return True


def _tool(spec: CodecSpec) -> Path:
    if platform.system() != "Windows":
        raise RuntimeError(f"{spec.name} is bundled only in the Windows build")
    archive = _checked_archive(spec)
    cache = Path(tempfile.gettempdir()) / "Guardian-G2-codecs-v1"
    target = cache / spec.executable
    with _EXTRACT_LOCK:
        if target.exists():
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if digest == spec.executable_sha256:
                return target
        cache.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as source:
            payload = source.read(spec.executable)
        if hashlib.sha256(payload).hexdigest() != spec.executable_sha256:
            raise RuntimeError(f"Guardian codec executable checksum failed: {spec.name}")
        pending = target.with_suffix(".pending")
        pending.write_bytes(payload)
        os.replace(pending, target)
    return target


def _run(args: list[str], *, cwd: Path, timeout: float) -> None:
    options: dict = {
        "cwd": str(cwd),
        # Keep every codec's scratch files inside our private job directory.
        "env": {**os.environ, "TEMP": str(cwd), "TMP": str(cwd)},
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "timeout": timeout,
        "check": False,
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(args, **options)
    output = result.stdout.decode("utf-8", errors="replace")
    if result.returncode or "permission denied" in output.casefold():
        detail = output[-500:].strip()
        raise RuntimeError(f"codec exited {result.returncode}: {detail}")


def _compress_with(spec: CodecSpec, data: bytes) -> bytes:
    executable = _tool(spec)
    with tempfile.TemporaryDirectory(prefix="guardian-gcp-") as temporary:
        work = Path(temporary)
        source = work / "input.bundle"
        source.write_bytes(data)
        if spec.codec_id == 1:
            output = work / "archive.zpaq"
            args = [
                str(executable), "add", output.name, source.name,
                "-method", str(spec.profile), "-threads", "1", "-summary", "0",
            ]
        elif spec.codec_id == 2:
            output = work / "archive.paq8px187"
            args = [str(executable), f"-{spec.profile}", source.name, output.name]
        else:
            output = work / "archive.lpaq8"
            args = [str(executable), str(spec.profile), source.name, output.name]
        _run(args, cwd=work, timeout=_COMPRESS_TIMEOUT)
        compressed = output.read_bytes()
    return _HEADER.pack(
        MAGIC,
        spec.codec_id,
        spec.profile,
        len(data),
        _sha256(data),
    ) + compressed


def external_candidates(data: bytes) -> list[tuple[str, bytes]]:
    """Try all bundled high-ratio codecs without allowing one to block others."""
    if not external_codecs_available() or not data:
        return []
    results: list[tuple[str, bytes]] = []
    def compress_and_verify(spec: CodecSpec) -> bytes:
        encoded = _compress_with(spec, data)
        if decompress_guardian_envelope(
            encoded, timeout=_CANDIDATE_VERIFY_TIMEOUT
        ) != data:
            raise RuntimeError("codec round-trip mismatch")
        return encoded

    # The PAQ8PX level-1 model alone uses about 417 MB. Run candidates one at a
    # time on the already-backgrounded mail worker so their memory peaks never
    # stack, while each subprocess still has a hard timeout.
    for spec in CODECS:
        try:
            encoded = compress_and_verify(spec)
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            continue
        results.append((spec.name, encoded))
    return results


def is_guardian_envelope(data: bytes) -> bool:
    return len(data) >= _HEADER.size and data.startswith(MAGIC)


def decompress_guardian_envelope(
    data: bytes,
    *,
    timeout: float = _DECOMPRESS_TIMEOUT,
) -> bytes:
    """Decode and authenticate one GCP1 envelope, rejecting bombs/corruption."""
    if len(data) < _HEADER.size:
        raise ValueError("truncated Guardian compression envelope")
    magic, codec_id, profile, original_size, expected_hash = _HEADER.unpack_from(data)
    if magic != MAGIC:
        raise ValueError("not a Guardian compression envelope")
    if original_size > _MAX_DECOMPRESSED:
        raise ValueError("Guardian compression envelope exceeds the safety limit")
    spec = _BY_ID.get(codec_id)
    if spec is None or profile != spec.profile:
        raise ValueError("unsupported Guardian compression codec/profile")
    executable = _tool(spec)
    with tempfile.TemporaryDirectory(prefix="guardian-gcp-decode-") as temporary:
        work = Path(temporary)
        payload = data[_HEADER.size:]
        if codec_id == 1:
            archive = work / "archive.zpaq"
            archive.write_bytes(payload)
            output_dir = work / "out"
            output_dir.mkdir()
            _run(
                [str(executable), "extract", archive.name, "-to", output_dir.name,
                 "-only", "input.bundle", "-force", "-summary", "0"],
                cwd=work,
                timeout=timeout,
            )
            output = output_dir / "input.bundle"
        elif codec_id == 2:
            archive = work / "archive.paq8px187"
            archive.write_bytes(payload)
            output = work / "decoded.bundle"
            _run(
                [str(executable), "-d", archive.name, output.name],
                cwd=work,
                timeout=timeout,
            )
        else:
            archive = work / "archive.lpaq8"
            archive.write_bytes(payload)
            output = work / "decoded.bundle"
            _run(
                [str(executable), "d", archive.name, output.name],
                cwd=work,
                timeout=timeout,
            )
        decoded = output.read_bytes()
    if len(decoded) != original_size or _sha256(decoded) != expected_hash:
        raise ValueError("Guardian compression checksum/length mismatch")
    return decoded
