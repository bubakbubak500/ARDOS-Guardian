"""Package Linux separately without touching any Windows release assets."""
from pathlib import Path
import hashlib
import json
import platform
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from guardian import __version__


def main():
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise RuntimeError("Linux x64 packaging requires a Linux x86_64 host")
    release = ROOT / "release"
    release.mkdir(exist_ok=True)
    name = f"Guardian-{__version__}-linux-x64"
    archive = release / f"{name}.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(ROOT / "dist/Guardian", arcname=name)
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    url = "https://github.com/bubakbubak500/ARDOS-Guardian/releases"
    manifest = {"version": __version__, "platform": "linux-x64",
        "installer_url": f"{url}/download/v{__version__}/{archive.name}",
        "sha256": digest, "notes_url": f"{url}/tag/v{__version__}"}
    (release / "release-manifest-linux-x64.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (release / "SHA256SUMS-linux-x64.txt").write_text(
        f"{digest}  {archive.name}\n", encoding="ascii")
    print(archive)
    print("Source revision:", subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip())


if __name__ == "__main__":
    main()
