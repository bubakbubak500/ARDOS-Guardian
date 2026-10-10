"""Content identities for the code actually executing, including native ARDOP."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

from ..platform_support import LINUX


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def digest_json(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def identity() -> dict:
    from guardian import __version__
    package = Path(__file__).resolve().parents[1]
    frozen = bool(getattr(sys, "frozen", False))
    files = {"runtime/executable": sha256(Path(sys.executable))}
    if frozen:
        root = Path(sys._MEIPASS)
        # The frozen EXE owns Python code; the bundle owns native dependencies.
        for path in sorted(root.rglob("*")):
            selected = path.suffix.lower() in {".exe", ".dll", ".pyd", ".pyz", ".html"}
            if LINUX and (path.suffix.lower() == ".so" or ".so." in path.name):
                selected = True
            if path.is_file() and selected:
                files[f"bundle/{path.relative_to(root).as_posix()}"] = sha256(path)
        manifest_path = root / "guardian-build.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files["bundle/guardian-build.json"] = sha256(manifest_path)
        revision = manifest["revision"]
    else:
        root = package.parent
        for path in sorted(package.rglob("*")):
            if path.is_file() and path.suffix in {".py", ".html"}:
                files[f"guardian/{path.relative_to(package).as_posix()}"] = sha256(path)
        for path in sorted((root / "native/ardop/bin").glob("*")):
            if path.suffix.lower() in {".dll", ".so"}:
                files[f"ardop/{path.name}"] = sha256(path)
        try:
            revision = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, timeout=5,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).decode().strip()
        except (OSError, subprocess.SubprocessError):
            revision = "unavailable"
    dependencies = {}
    for name in ("numpy", "sounddevice", "pyserial", "zopfli", "PySide6-Essentials"):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = "bundled" if frozen else "missing"
    contract = {"version": __version__, "python": sys.version,
                "frozen": frozen, "files": files, "dependencies": dependencies}
    return {**contract, "revision": revision, "fingerprint": digest_json(contract)}
