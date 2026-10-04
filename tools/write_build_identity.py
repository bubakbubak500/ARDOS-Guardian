"""Seal source provenance into the distributed application before freezing."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from guardian import __version__
from guardian.lab.identity import sha256, digest_json


def main():
    files = {str(p.relative_to(ROOT)).replace("\\", "/"): sha256(p)
             for p in sorted((ROOT / "guardian").rglob("*"))
             if p.is_file() and p.suffix in {".py", ".html"}}
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    manifest = {"version": __version__, "revision": revision,
                "source_sha256": digest_json(files), "files": files}
    path = ROOT / "build" / "guardian-build.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
