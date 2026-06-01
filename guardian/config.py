"""Station configuration — load/save a JSON profile.

The profile describes this station's identity, radio interface, VARA ports
and audio devices. It mirrors the example profile from the ARDOS design:

    {
      "callsign": "OK1AAA",
      "radio": "FTM-500",
      "rig_model": 1041,
      "cat_port": "COM7",
      "audio_input": "USB Audio CODEC RX",
      "audio_output": "USB Audio CODEC TX",
      "rigctld_host": "127.0.0.1",
      "rigctld_port": 4532,
      "vara_host": "127.0.0.1",
      "vara_cmd_port": 8300,
      "vara_data_port": 8301
    }
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def config_dir() -> Path:
    """Return the directory where Guardian keeps per-station state.

    Uses %APPDATA%\\Guardian on Windows, falling back to ~/.guardian.
    """
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) / "Guardian" if appdata else Path.home() / ".guardian"
    base.mkdir(parents=True, exist_ok=True)
    return base


DEFAULT_CONFIG_PATH = config_dir() / "config.json"


@dataclass
class StationConfig:
    """Everything Guardian needs to know about this station."""

    # Identity
    callsign: str = "NOCALL"
    operator_name: str = ""

    # Radio control backend: "hamlib" (rigctld) | "vox" (serial PTT) | "none"
    radio_backend: str = "none"
    radio: str = ""               # human-readable model name, e.g. "IC-7300"
    rig_model: int = 0            # Hamlib rig model id, e.g. 3073 for IC-7300
    cat_port: str = ""            # COM port for rigctld (-r) or VOX serial PTT
    cat_baud: int = 0             # 0 = let Hamlib decide

    # rigctld TCP endpoint (Guardian talks to rigctld, not the radio directly)
    rigctld_host: str = "127.0.0.1"
    rigctld_port: int = 4532
    rigctld_autostart: bool = False   # spawn rigctld ourselves if not running
    rigctld_path: str = "rigctld"     # path/command to the rigctld executable

    # VOX / dumb-radio PTT (only used when radio_backend == "vox")
    ptt_line: str = "RTS"         # "RTS" | "DTR"

    # Audio device hints (for VARA / wake detection later)
    audio_input: str = ""
    audio_output: str = ""

    # VARA — one client serves both flavours; mode selects ports + modem.
    vara_mode: str = "FM"             # "FM" | "HF"
    vara_host: str = "127.0.0.1"
    vara_cmd_port: int = 8300         # active command port (mirrors per-mode below)
    vara_data_port: int = 8301        # active data port
    # Ports remembered per mode so switching FM <-> HF is one click.
    vara_fm_cmd_port: int = 8300
    vara_fm_data_port: int = 8301
    vara_hf_cmd_port: int = 8300
    vara_hf_data_port: int = 8301

    # Control-burst modem: "auto" picks AFSK1200 for FM, MFSK16 for HF.
    control_modem: str = "auto"       # "auto" | "afsk1200" | "mfsk16"

    # How the message payload is moved after the handshake.
    payload_backend: str = "vara_p2p"  # "vara_p2p" | "winlink_manual"

    # Control-burst channel: "loopback" (simulation) | "audio" (real RF).
    control_channel: str = "loopback"

    # Mesh / smart routing.
    auto_route: bool = True    # discover a next hop (ROUTE_QUERY) when none known
    auto_relay: bool = False   # forward received messages toward their final dest
    auto_deliver: bool = True  # send waiting Outbox/Transit mail when the hop is heard
    beacon_enabled: bool = False
    beacon_interval: float = 120.0   # seconds between presence beacons
    scan_dwell: float = 3.0    # seconds per channel when scanning

    # Control burst behaviour
    default_ttl: int = 5

    # Theme
    appearance: str = "System"    # "System" | "Dark" | "Light"

    @classmethod
    def load(cls, path: Path | str | None = None) -> "StationConfig":
        path = Path(path) if path else DEFAULT_CONFIG_PATH
        if not path.exists():
            cfg = cls()
            cfg.save(path)
            return cfg
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        # Only keep keys we know about, so old/new files stay compatible.
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in known}
        return cls(**clean)

    def save(self, path: Path | str | None = None) -> Path:
        path = Path(path) if path else DEFAULT_CONFIG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return path

    # --- VARA mode helpers ----------------------------------------------
    def apply_vara_mode(self, mode: str) -> None:
        """Switch FM <-> HF, copying the remembered ports into the active set."""
        mode = "HF" if str(mode).upper() == "HF" else "FM"
        self.vara_mode = mode
        if mode == "HF":
            self.vara_cmd_port = self.vara_hf_cmd_port
            self.vara_data_port = self.vara_hf_data_port
        else:
            self.vara_cmd_port = self.vara_fm_cmd_port
            self.vara_data_port = self.vara_fm_data_port

    def remember_vara_ports(self) -> None:
        """Store the active ports back into the current mode's slot."""
        if self.vara_mode.upper() == "HF":
            self.vara_hf_cmd_port = self.vara_cmd_port
            self.vara_hf_data_port = self.vara_data_port
        else:
            self.vara_fm_cmd_port = self.vara_cmd_port
            self.vara_fm_data_port = self.vara_data_port

    def active_modem(self) -> str:
        """Resolve the control-burst modem for the current VARA mode."""
        if self.control_modem and self.control_modem != "auto":
            return self.control_modem
        return "mfsk16" if self.vara_mode.upper() == "HF" else "afsk1200"
