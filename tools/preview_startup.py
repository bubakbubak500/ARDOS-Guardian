"""Launch the Guardian UI with its startup animation and isolated state."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "startup-preview" / "profile"
STATE.mkdir(parents=True, exist_ok=True)
os.environ["APPDATA"] = str(STATE)
os.environ["GUARDIAN_STARTUP_PREVIEW"] = "1"
os.environ["GUARDIAN_PREVIEW_SETTINGS"] = str(STATE / "ui.ini")
sys.path.insert(0, str(ROOT))

from guardian.app import main

if __name__ == "__main__":
    main()
