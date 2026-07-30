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

    # How rigctld keys the transmitter (radio_backend == "hamlib").
    # "RIG" sends the CAT PTT command; "RTS"/"DTR" assert a serial control
    # line on cat_port instead (rigctld --ptt-type/--ptt-file). A no-CAT
    # handheld behind an AIOC or data cable needs RTS or DTR together with
    # the Hamlib Dummy model -- the dummy never opens a rig device, so
    # without this the COM port was simply never touched.
    ptt_type: str = "RIG"         # "RIG" | "RTS" | "DTR"

    # Experimental: let Guardian key the radio (via its own driver/rigctld) on
    # VARA's "PTT ON"/"PTT OFF" command-channel signals, so VARA never needs the
    # COM port. Generic across CI-V / RTS / DTR rigs — set VARA's own PTT to None.
    vara_host_ptt: bool = False

    # Slow-keying PTT tail (ms, 0 = off) requested for the VARA FM payload
    # phase. For AIOC-class cables on cheap handhelds: unkeying the moment
    # VARA says PTT OFF cuts the tail off the burst (seen on a spectrum
    # display), and the peer answers into what is still missing. Both
    # stations negotiate the larger of their requests in the HAVE_MSG/
    # ACK_HAVE handshake and keep PTT asserted that long after each burst.
    # FM only — HF radios do not need it — and it requires vara_host_ptt
    # (Guardian must be the one keying, or there is nothing to slow down).
    # VARA's own timing/speed is untouched.
    vara_ptt_delay_ms: int = 0

    # Experimental (Winlink mode): release the COM port + rigctld during the
    # operator hand-off so Winlink's VARA can own the COM for PTT (older rigs
    # without VOX). Reclaimed when the operator confirms the transfer.
    vara_handoff_com: bool = False

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
    vara_fm_path: str = ""           # optional explicit VARAFM.exe location
    vara_hf_path: str = ""           # optional explicit VARA.exe location
    # VARA HF only. BW2300 is VARA's own default; BW500 is the narrow mode for
    # poor conditions, BW2750 the tactical one. Both stations must agree.
    vara_hf_bandwidth: str = "BW2300"   # "BW500" | "BW2300" | "BW2750"

    # Control-burst modem: "auto" picks AFSK1200 for FM, MFSK16 for HF.
    control_modem: str = "auto"       # "auto" | "afsk1200" | "mfsk16"

    # How the message payload is moved after the handshake. "winlink_manual"
    # was dropped in 0.6.26; a config still holding it is coerced on load.
    payload_backend: str = "vara_p2p"  # "vara_p2p"

    # Control-burst channel: "off" (idle) | "audio" (real RF via the radio).
    control_channel: str = "off"

    # Mesh / smart routing.
    auto_route: bool = True    # discover a next hop (ROUTE_QUERY) when none known
    auto_relay: bool = False   # forward received messages toward their final dest
    auto_deliver: bool = True  # send waiting Outbox/Transit mail when the hop is heard
    beacon_enabled: bool = False
    beacon_interval: float = 120.0   # seconds between presence beacons
    scan_dwell: float = 3.0    # seconds per channel when scanning
    auto_qsy: bool = True      # VARA P2P: tune the radio to the station's freq before connecting

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
        # The manual Winlink hand-off was removed in 0.6.26; a station whose
        # config still selects it must not be left without a transport.
        if clean.get("payload_backend") != "vara_p2p":
            clean["payload_backend"] = "vara_p2p"
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
