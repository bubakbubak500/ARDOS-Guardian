"""Offscreen shell verification from the extracted frozen release archive."""
from pathlib import Path
import os
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from guardian import __version__


def main():
    archive = ROOT / "release" / f"Guardian-{__version__}-linux-x64.tar.gz"
    with tempfile.TemporaryDirectory(prefix="guardian-linux-verify-") as temporary:
        with tarfile.open(archive) as bundle:
            bundle.extractall(temporary, filter="data")
        folder = Path(temporary) / f"Guardian-{__version__}-linux-x64"
        env = dict(os.environ, GUARDIAN_STATE_DIR=str(Path(temporary) / "state"),
                   QT_QPA_PLATFORM="offscreen", OPENBLAS_NUM_THREADS="1",
                   XDG_CONFIG_HOME=str(Path(temporary) / "config"))
        # A source self-test script is run by the frozen bundle's interpreter
        # only through its dedicated command-line entry point.
        report = ROOT / "output/linux-frozen-shell.txt"
        subprocess.run([str(folder / "Guardian"), "--linux-shell-self-test",
                        "--linux-shell-self-test-report", str(report)],
                       check=True, cwd=folder, env=env, timeout=60)
        assert report.read_text().startswith("PASS\n")
        print(report.read_text())


if __name__ == "__main__":
    main()
