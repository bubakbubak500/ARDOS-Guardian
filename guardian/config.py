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
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path


# The G2 line keeps its state apart from the public G1 line so both can be
# installed on one machine. This is not tidiness: the two builds do not know
# each other's settings, StationConfig.load() drops keys it does not recognise
# and save() writes the whole dataclass back, so a single G1 launch against a
# shared config.json would strip every G2-only setting for good.
_G2_DIR_NAME = "Guardian-G2"
_G1_DIR_NAME = "Guardian"
_G2_DOTDIR_NAME = ".guardian-g2"
_G1_DOTDIR_NAME = ".guardian"

_seed_checked = False


def _seed_from_g1(base: Path, legacy: Path) -> None:
    """On first run, copy a G1 config.json into the empty G2 directory.

    An operator moving to G2 should not have to type the station in again. It
    happens once: as soon as G2 has a config.json of its own, the G1 file is
    never read again, and G2 never writes back into the G1 directory.

    `legacy` is always the sibling of `base` under the same root, so redirecting
    APPDATA (as tests/conftest.py does at import time) isolates this completely
    — the seed cannot reach into an operator's real profile from a test run.
    """
    global _seed_checked
    if _seed_checked:
        return
    _seed_checked = True
    try:
        target = base / "config.json"
        source = legacy / "config.json"
        if target.exists() or not source.is_file():
            return
        shutil.copyfile(source, target)
    except OSError:
        # A missing or unreadable G1 profile is not a reason to fail to start.
        pass


def config_dir() -> Path:
    """Return the directory where Guardian keeps per-station state.

    Uses %APPDATA%\\Guardian-G2 on Windows, falling back to ~/.guardian-g2.
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        base, legacy = Path(appdata) / _G2_DIR_NAME, Path(appdata) / _G1_DIR_NAME
    else:
        home = Path.home()
        base, legacy = home / _G2_DOTDIR_NAME, home / _G1_DOTDIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    _seed_from_g1(base, legacy)
    return base


DEFAULT_CONFIG_PATH = config_dir() / "config.json"

# Transports that can move a message payload. Kept as literals here so
# configuration stays importable from anywhere -- guardian.payload owns the
# actual construction, and this module must not depend on it.
PAYLOAD_BACKENDS = ("vara_p2p", "ofdm_vhf")

# What a radio profile carries: exactly the fields the Radio page edits. The
# keying delay belongs to the cable and the rig behind it, so it travels with
# them; everything else on other pages stays where the operator left it.
RADIO_PROFILE_FIELDS = (
    "radio_backend",
    "radio",
    "rig_model",
    "cat_port",
    "cat_baud",
    "rigctld_host",
    "rigctld_port",
    "rigctld_path",
    "ptt_type",
    "ptt_line",
    "vara_ptt_delay_ms",
)
MAX_RADIO_PROFILE_NAME = 24


def radio_profile_name(name: str) -> str:
    """Normalise a profile name: trimmed, single-line and short enough to show."""
    return " ".join(str(name or "").split())[:MAX_RADIO_PROFILE_NAME]


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

    # A Hamlib Dummy / no-CAT radio cannot report where its dial is. Keep the
    # operator-entered channel as explicit station state instead of accepting
    # the dummy backend's simulated value as if it came from real hardware.
    manual_frequency_hz: int = 0

    # Let Guardian key the radio (via its own driver/rigctld) on VARA's
    # "PTT ON"/"PTT OFF" command-channel signals, so VARA never needs the COM
    # port. Generic across CI-V / RTS / DTR rigs — set VARA's own PTT to None.
    #
    # Default ON since 0.6.43. Guardian's rigctld owns the CAT port, so with
    # this off VARA has no port left to key through: OK2IPW's station produced
    # a textbook VARA session (CONNECT, BITRATE, PTT ON) with the transmitter
    # never coming up, and nothing on air. A station whose profile already
    # stores `false` keeps it -- it may be keying through VARA deliberately,
    # and taking that over behind the operator's back could double-key.
    vara_host_ptt: bool = True

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

    # VARA's native TEXT compression remains the baseline. This switch selects
    # the separate FILES codec documented by VARA for binary/file transfers.
    # It is deliberately opt-in because both stations need compatible modem
    # versions and Guardian's own adaptive bundle compression is usually the
    # better transport-independent choice.
    vara_file_compression: bool = False
    # VARA's proprietary AES-256 option is intended for authorised non-amateur
    # services. VARA itself stores this password in its INI file; keeping the
    # value here lets Guardian configure either FM or HF consistently.
    vara_encryption: bool = False
    vara_encryption_password: str = ""

    # Content-aware ZIP encoding of Guardian message bundles. The smallest of
    # stored, DEFLATE, BZIP2 and LZMA is selected before either VARA or OFDM is
    # handed the payload. No LLM or external service is involved.
    guardian_compression: bool = False

    # After the final destination has queued its last control acknowledgement,
    # identify the two stations once in 40 WPM Morse. Off by default.
    morse_id_after_ack: bool = False

    # Control-burst modem: "auto" picks AFSK1200 for FM, MFSK16 for HF.
    control_modem: str = "auto"       # "auto" | "afsk1200" | "mfsk16"

    # How the message payload is moved after the handshake. "winlink_manual"
    # was dropped in 0.6.26; a config still holding it is coerced on load.
    payload_backend: str = "vara_p2p"  # "vara_p2p" | "ofdm_vhf"

    # Guardian OFDM VHF, experimental: a native payload modem that uses the
    # soundcard and Guardian's own PTT instead of VARA. Only these knobs are an
    # operator's business -- FFT size, cyclic prefix, carrier set and sample rate
    # belong to the named profile in guardian/ofdm/config.py, because the
    # occupied bandwidth a VHF radio actually passes is still to be measured and
    # changing it must be a new profile entry rather than a settings dialog.
    ofdm_profile: str = "BENCH"
    ofdm_mcs: int = 1                  # MCS1 = QPSK, rate 1/2
    ofdm_tx_lead_ms: int = 300         # after keying, before the waveform starts
    ofdm_tx_tail_ms: int = 100         # after the waveform, before unkeying
    ofdm_max_retries: int = 4          # retransmissions before a burst is failed
    # Version-2 adaptive FEC + selective-repeat ARQ. The fixed values are used
    # directly when their AUTO switch is off and are also the reproducible test
    # settings shown in the modem workspace.
    ofdm_adaptive_fec: bool = True
    # Opt-in 2.3.3 systematic sparse LDPC ladder. Older G2 peers understand
    # only the convolutional IDs, so this is explicit until capability v2 is
    # negotiated on the control channel.
    ofdm_modern_ldpc: bool = False
    ofdm_fec: str = "1/2"              # 1/2 | 2/3 | 3/4 | 5/6 | 7/8
    ofdm_adaptive_burst: bool = True
    ofdm_burst_bytes: int = 4096       # fixed-mode keyed-burst target
    ofdm_min_burst_bytes: int = 512
    ofdm_max_burst_bytes: int = 8192
    ofdm_arq_block_bytes: int = 512
    ofdm_timeout_multiplier: float = 1.0
    ofdm_legacy_mode: bool = False      # version-1 512 B stop-and-wait comparison
    # Version-2 fast selective repeat. Values above one concatenate this many
    # independently protected microbursts under a single PTT and defer the
    # cumulative ACK until the train ends. Keep one for mixed older peers.
    ofdm_train_bursts: int = 1
    ofdm_adaptive_train: bool = False
    # Protocol-v3 superframe: fold the configured train into one physical burst
    # with one preamble/training/header and up to 63 independently protected
    # ARQ blocks. Explicit opt-in because 2.3.2 peers only accept frame v2.
    ofdm_superframe: bool = False
    ofdm_train_gap_ms: int = 30
    ofdm_max_train_seconds: float = 20.0
    # Physical waveform under the Guardian G2 soundcard transport.  "ofdm" is
    # the verified default; the other families are explicit opt-in experiments
    # which keep the same control handshake, framing and selective-repeat ARQ.
    g2_waveform: str = "ofdm"           # ofdm | sc_hs | sc_ftn | sc_fde_ftn | sefdm
    # Common experimental occupied-band rung. OFDM keeps its established named
    # profile because its BENCH width intentionally differs from SC 2K7.
    g2_bandwidth: str = "2K7"            # 1K2 | 2K7 | 5K | 10K | 20K
    g2_mcs: int = 2                     # experimental-family modulation index
    g2_adaptive_mcs: bool = False        # selected MCS is the automatic ceiling
    # Calibrated digital drive is deliberately per waveform: their crest
    # factors differ enough that one safe RMS value would waste SC-FTN headroom
    # or clip SEFDM. Values are linear full-scale multipliers.
    g2_tx_scales: dict[str, float] = field(default_factory=lambda: {
        "ofdm": 1.0, "sc_hs": 1.0, "sc_ftn": 1.0,
        "sc_fde_ftn": 1.0, "sefdm": 1.0,
    })
    calibration_allowlist: list[str] = field(default_factory=list)
    calibration_auto_accept: bool = False
    calibration_windows_gain: bool = False
    calibration_max_seconds: int = 180

    # Control-burst channel: "off" (idle) | "audio" (real RF via the radio).
    control_channel: str = "off"

    # Mesh / smart routing.
    auto_route: bool = True    # discover a next hop (ROUTE_QUERY) when none known
    auto_relay: bool = False   # forward received messages toward their final dest
    auto_deliver: bool = True  # send waiting Outbox/Transit mail when the hop is heard
    # Bounded multi-hop discovery. Two positions only: "off" ignores every
    # discovery frame, "assisted" takes part -- it answers a query about this
    # station, may look for a route the operator asks for, and pauses an
    # originating message until that route is approved (or until
    # `discovery_auto_use` is set). The receive-only "monitor" position that
    # 0.6.58 and earlier had could do none of that and is migrated to
    # "assisted" on load; see routing.discovery.normalize_discovery_mode.
    discovery_mode: str = "assisted"  # "off" | "assisted"
    discovery_forward: bool = False
    discovery_ttl: int = 4
    discovery_route_lifetime: float = 1800.0
    discovery_frame_budget: int = 12
    discovery_allowlist: list[str] = field(default_factory=list)
    discovery_denylist: list[str] = field(default_factory=list)
    # Use a fresh discovered route without waiting for approval. Off by
    # default: the operator sees what was found before it carries traffic.
    discovery_auto_use: bool = False
    link_advert_enabled: bool = False
    link_advert_interval: float = 900.0
    beacon_enabled: bool = False
    beacon_interval: float = 120.0   # seconds between presence beacons
    # This station's Maidenhead locator ("" = unknown). Set from the map or
    # typed in; the finest form (10 characters, ~50 x 90 m) fits the beacon
    # beside any callsign.
    station_grid: str = ""
    # Put that locator in the beacon. Transmitting a position is a deliberate
    # act, so it has its own switch -- though nothing goes out until beacons
    # themselves are enabled, which they are not by default.
    beacon_position: bool = True
    # Draw the raster background on the map. Tiles are fetched only for what
    # is on screen and kept in %APPDATA%\Guardian-G2\maps, so ground the operator
    # has already looked at stays available with no network.
    map_background: bool = True
    # Optional operational overlays in the station map. Locator precision is
    # 0 (off), 4 (field/square) or 6 (subsquare).
    map_locator_grid: int = 0
    map_range_rings: bool = False
    map_status_colours: bool = True
    scan_dwell: float = 3.0    # seconds per channel when scanning
    # Optional raw Hamlib STRENGTH value. None means that only a decoded
    # control frame holds the scanner on a channel.
    scan_signal_threshold: int | None = None
    auto_qsy: bool = True      # VARA P2P: tune the radio to the station's freq before connecting
    # Opt-in two-channel sessions.  False deliberately preserves the original
    # single-channel handshake/QSY behaviour and keeps the extra route fields
    # out of the production UI until the operator asks for them.
    separate_working_channels: bool = False

    # Control burst behaviour
    default_ttl: int = 5

    # Theme
    appearance: str = "System"    # "System" | "Dark" | "Light"

    # Desktop notifications. `notify_incoming` covers the polite level (tray
    # toast + soft chime for new mail and routine alerts); URGENT/EMERGENCY
    # always gets the on-top window because that is what the station is
    # listening for. `notify_sound` silences every chime, including that one.
    notify_incoming: bool = True
    notify_sound: bool = True

    # Offline phone companion.  The server is always operator-started; keeping
    # only its non-privileged TCP port is safe across restarts.  RF authority
    # and pairing sessions are deliberately ephemeral and never enter config.
    companion_port: int = 8765

    # Named snapshots of the radio page, so a station used with more than one
    # rig or cable is one pick away from each of them instead of nine fields
    # re-entered from memory. Radio settings only: a profile must never carry
    # a callsign, an audio device or a VARA port from one setup to another.
    radio_profiles: dict[str, dict] = field(default_factory=dict)

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
        # config still selects it must not be left without a transport. An
        # allowlist rather than a comparison against one name: a stored
        # "winlink_manual", a typo or a hand edit all fall back to VARA, but a
        # transport Guardian really has must survive a restart.
        if clean.get("payload_backend") not in PAYLOAD_BACKENDS:
            clean["payload_backend"] = "vara_p2p"
        # A hand-edited or truncated file must not leave the profile picker
        # holding something that is not a profile.
        profiles = clean.get("radio_profiles")
        if isinstance(profiles, dict):
            clean["radio_profiles"] = {
                radio_profile_name(key): value
                for key, value in profiles.items()
                if radio_profile_name(key) and isinstance(value, dict)
            }
        else:
            clean.pop("radio_profiles", None)
        # Kept as literals so configuration stays importable from anywhere;
        # routing.discovery.normalize_discovery_mode is the same migration.
        mode = str(clean.get("discovery_mode", "")).strip().lower()
        if mode == "monitor":
            mode = "assisted"
        if mode in {"off", "assisted"}:
            clean["discovery_mode"] = mode
        else:
            clean.pop("discovery_mode", None)
        for name in ("discovery_allowlist", "discovery_denylist"):
            values = clean.get(name)
            if isinstance(values, list):
                clean[name] = [
                    str(value).strip().upper()
                    for value in values
                    if str(value).strip()
                ]
            else:
                clean.pop(name, None)
        # Keep hand-edited adaptive modem values on explicit protocol ladders.
        if str(clean.get("ofdm_fec", "")) not in {
            "1/2", "2/3", "3/4", "5/6", "7/8",
            "LDPC-1/2", "LDPC-3/4", "LDPC-9/10",
        }:
            clean.pop("ofdm_fec", None)
        burst_ladder = {256, 512, 1024, 2048, 4096, 8192, 16384}
        for name in ("ofdm_burst_bytes", "ofdm_min_burst_bytes",
                     "ofdm_max_burst_bytes"):
            if clean.get(name) not in burst_ladder:
                clean.pop(name, None)
        if clean.get("ofdm_arq_block_bytes") not in {256, 512, 1024}:
            clean.pop("ofdm_arq_block_bytes", None)
        if clean.get("g2_waveform") not in {
            "ofdm", "sc_hs", "sc_ftn", "sc_fde_ftn", "sefdm"
        }:
            clean.pop("g2_waveform", None)
        if clean.get("g2_bandwidth") not in {"1K2", "2K7", "5K", "10K", "20K"}:
            clean.pop("g2_bandwidth", None)
        scales = clean.get("g2_tx_scales")
        if isinstance(scales, dict):
            try:
                clean["g2_tx_scales"] = {
                    name: min(1.0, max(0.05, float(scales.get(name, 1.0))))
                    for name in ("ofdm", "sc_hs", "sc_ftn", "sc_fde_ftn", "sefdm")
                }
            except (TypeError, ValueError):
                clean.pop("g2_tx_scales", None)
        else:
            clean.pop("g2_tx_scales", None)
        values = clean.get("calibration_allowlist")
        if isinstance(values, list):
            clean["calibration_allowlist"] = [
                str(value).strip().upper() for value in values
                if str(value).strip()
            ]
        else:
            clean.pop("calibration_allowlist", None)
        try:
            clean["calibration_max_seconds"] = min(
                900, max(30, int(clean.get("calibration_max_seconds", 180)))
            )
        except (TypeError, ValueError):
            clean.pop("calibration_max_seconds", None)
        try:
            experimental_mcs = int(clean.get("g2_mcs", 2))
        except (TypeError, ValueError):
            clean.pop("g2_mcs", None)
        else:
            clean["g2_mcs"] = min(20, max(0, experimental_mcs))
        arq_bytes = int(clean.get("ofdm_arq_block_bytes", 512))
        for name in ("ofdm_burst_bytes", "ofdm_min_burst_bytes",
                     "ofdm_max_burst_bytes"):
            if name in clean and int(clean[name]) < arq_bytes:
                clean.pop(name)
        try:
            timeout_multiplier = float(clean.get("ofdm_timeout_multiplier", 1.0))
        except (TypeError, ValueError):
            clean.pop("ofdm_timeout_multiplier", None)
        else:
            clean["ofdm_timeout_multiplier"] = min(4.0, max(0.5, timeout_multiplier))
        try:
            clean["ofdm_train_bursts"] = min(
                8, max(1, int(clean.get("ofdm_train_bursts", 1)))
            )
            clean["ofdm_train_gap_ms"] = min(
                200, max(10, int(clean.get("ofdm_train_gap_ms", 30)))
            )
            clean["ofdm_max_train_seconds"] = min(
                60.0, max(1.0, float(clean.get("ofdm_max_train_seconds", 20.0)))
            )
        except (TypeError, ValueError):
            for name in ("ofdm_train_bursts", "ofdm_train_gap_ms",
                         "ofdm_max_train_seconds"):
                clean.pop(name, None)
        try:
            clean["companion_port"] = min(
                65535, max(1024, int(clean.get("companion_port", 8765)))
            )
        except (TypeError, ValueError):
            clean.pop("companion_port", None)
        return cls(**clean)

    def save(self, path: Path | str | None = None) -> Path:
        path = Path(path) if path else DEFAULT_CONFIG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return path

    # --- Radio profiles --------------------------------------------------
    def radio_profile(self) -> dict:
        """This station's current radio settings, as a profile would store them."""
        return {name: getattr(self, name) for name in RADIO_PROFILE_FIELDS}

    def save_radio_profile(self, name: str) -> str:
        """Store the current radio settings under a short name, replacing any."""
        key = radio_profile_name(name)
        if not key:
            raise ValueError("a radio profile needs a name")
        self.radio_profiles[key] = self.radio_profile()
        return key

    def apply_radio_profile(self, name: str) -> bool:
        """Load a stored profile into the radio fields. Unknown names do nothing.

        Only the fields the profile actually carries are written, so a profile
        saved by an older build cannot blank a setting it never knew about.
        """
        stored = self.radio_profiles.get(radio_profile_name(name))
        if not isinstance(stored, dict):
            return False
        for field_name in RADIO_PROFILE_FIELDS:
            if field_name in stored:
                setattr(self, field_name, stored[field_name])
        return True

    def delete_radio_profile(self, name: str) -> bool:
        return self.radio_profiles.pop(radio_profile_name(name), None) is not None

    def radio_profile_names(self) -> list[str]:
        return sorted(self.radio_profiles, key=str.casefold)

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
