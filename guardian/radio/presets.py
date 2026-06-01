"""Radio presets and Hamlib model catalog.

Goal: the operator should never have to know a Hamlib model *number*. They pick
a radio by name. Two sources feed the picker:

  1. CURATED  — a short list of common ARDOS radios for one-click setup. Only
                model ids we are confident about are hard-coded here (several
                come straight from the project notes). Everything else is left
                to the authoritative live list below.

  2. Hamlib   — `rigctl -l` prints the *exact* model table for the Hamlib build
                installed on this PC. We parse it so the "Browse all radios"
                picker is always correct for the user's version.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class RadioPreset:
    label: str            # what the operator sees
    backend: str          # "hamlib" | "vox" | "none"
    rig_model: int = 0    # Hamlib model id (0 if not applicable)
    note: str = ""


# Model ids verified against Hamlib 4.7.1 (`rigctl -l`). The authoritative
# source at runtime is still the live "Browse all" list, which matches the
# Hamlib version actually installed on each PC.
CURATED: list[RadioPreset] = [
    RadioPreset("— No radio —", "none", 0, "UI/testing only, no control"),
    RadioPreset("Generic VOX / serial PTT", "vox", 0, "RTS or DTR keys PTT; no CAT"),
    RadioPreset("Hamlib Dummy (test rig)", "hamlib", 1, "Fake rig for testing rigctld"),
    RadioPreset("Icom IC-7300", "hamlib", 3073),
    RadioPreset("Icom IC-705", "hamlib", 3085),
    RadioPreset("Icom IC-7100", "hamlib", 3070),
    RadioPreset("Icom IC-9700", "hamlib", 3081),
    RadioPreset("Yaesu FT-891", "hamlib", 1036),
    RadioPreset("Yaesu FT-710", "hamlib", 1049),
    RadioPreset("Yaesu FTDX-10", "hamlib", 1042),
    RadioPreset("Kenwood TS-2000", "hamlib", 2014),
    RadioPreset("Kenwood TS-590SG", "hamlib", 2037),
    RadioPreset("Elecraft KX3", "hamlib", 2045),
    RadioPreset("Xiegu G90", "hamlib", 3088),
    RadioPreset("Other… (pick from full Hamlib list)", "hamlib", 0, "Use Browse all radios"),
]


@dataclass(frozen=True)
class HamlibModel:
    model_id: int
    mfg: str
    model: str
    version: str
    status: str

    @property
    def label(self) -> str:
        return f"{self.mfg} {self.model}".strip()


def find_executable(name: str, explicit: str = "") -> str | None:
    """Locate a Hamlib executable (rigctl/rigctld), trying PATH then common dirs."""
    if explicit and explicit not in (name,) and os.path.isfile(explicit):
        return explicit
    found = shutil.which(explicit or name)
    if found:
        return found
    # Common Windows install locations (Hamlib zip, WSJT-X bundle, etc.) plus
    # Guardian's own per-station install dir (%APPDATA%\Guardian\hamlib\...).
    appdata = os.environ.get("APPDATA", "")
    patterns = [
        r"C:\Program Files\hamlib*\bin",
        r"C:\Program Files (x86)\hamlib*\bin",
        r"C:\Program Files\WSJT*\bin",
        r"C:\hamlib*\bin",
    ]
    if appdata:
        patterns += [
            os.path.join(appdata, "Guardian", "hamlib", "bin"),
            os.path.join(appdata, "Guardian", "hamlib", "*", "bin"),
        ]
    exe = name if name.lower().endswith(".exe") else name + ".exe"
    for pat in patterns:
        for d in glob.glob(pat):
            cand = os.path.join(d, exe)
            if os.path.isfile(cand):
                return cand
    return None


def parse_rigctl_list(text: str) -> list[HamlibModel]:
    """Parse the output of `rigctl -l`.

    Lines look like:
        Rig #  Mfg     Model           Version       Status   Macro
          1   Hamlib  Dummy           ...           Stable    RIG_MODEL_DUMMY
        3073  Icom    IC-7300         ...           Stable    RIG_MODEL_IC7300
    The first token is the numeric id; the next is manufacturer; the model is
    the remaining words up to the version/status columns. We parse defensively:
    take id + mfg, then everything until we hit a recognised status word.
    """
    statuses = {"Alpha", "Untested", "Beta", "Stable", "Unknown"}
    models: list[HamlibModel] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or not line[0].isdigit():
            continue
        parts = line.split()
        try:
            mid = int(parts[0])
        except ValueError:
            continue
        if len(parts) < 3:
            continue
        mfg = parts[1]
        # Find the status column; model name is everything between mfg and it.
        status = ""
        status_idx = len(parts)
        for i in range(2, len(parts)):
            if parts[i] in statuses:
                status = parts[i]
                status_idx = i
                break
        model_words = parts[2:status_idx] if status else parts[2:3]
        # The token just before status is the version (e.g. 20230101.0).
        version = ""
        if status and model_words:
            version = model_words[-1]
            model_words = model_words[:-1]
        model = " ".join(model_words) if model_words else "?"
        models.append(HamlibModel(mid, mfg, model, version, status))
    return models


def load_hamlib_models(rigctl_path: str = "rigctl", timeout: float = 8.0) -> list[HamlibModel]:
    """Run `rigctl -l` and return the parsed model list (empty on failure)."""
    exe = find_executable("rigctl", rigctl_path)
    if not exe:
        return []
    try:
        out = subprocess.run(
            [exe, "-l"],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_rigctl_list(out.stdout)
