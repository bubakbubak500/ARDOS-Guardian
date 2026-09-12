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

# Payload backends are kept as literals here so configuration remains a small,
# dependency-free module.  The payload package owns construction of each
# backend.  VARA is deliberately the default and remains the safe fallback for
# an old or hand-edited profile.
PAYLOAD_BACKENDS = ("vara_p2p", "ofdm_vhf")

# These controls remain fields for API and migration compatibility, but the
# shipped station policy always enables them.  Low-level protocol objects and
# explicitly constructed StationConfig instances may still choose another
# value; production entry points call ``enforce_production_policy``.
PRODUCTION_FIXED_TRUE_FIELDS = (
    "vara_host_ptt",
    "auto_route",
    "auto_relay",
    "auto_deliver",
    "auto_qsy",
    "discovery_forward",
    "discovery_auto_use",
    "link_advert_enabled",
)

# VARA FILES and Guardian BZIP2 are retained as read-only compatibility
# fields.  Production config normalization folds either legacy selection into
# the current Guardian compression path.
LEGACY_COMPRESSION_FIELDS = ("vara_file_compression", "guardian_compression")

# G1 carries one Guardian sound-card waveform beside VARA.  The transport name
# ``ofdm_vhf`` is retained for the later payload integration, but the only
# reachable production family is SC-FTN.  These names are the six audited
# width rungs, not a request to expose other G2 waveform families.
SC_FTN_WAVEFORM = "sc_ftn"
SC_FTN_BANDWIDTHS = ("1K2", "2K7", "4K5", "5K", "10K", "20K")
G2_MAX_TX_SCALE = 2.0

# K5's firmware guard is part of the radio path.  These values are carried in
# named radio profiles so switching away and back cannot lose the safe timing
# defaults used by the SC-FTN backend.
GUARDIAN_K5_G2_TX_LEAD_MS = 60
GUARDIAN_K5_G2_TX_TAIL_MS = 60
GUARDIAN_K5_G2_MIN_BURST_BYTES = 512
GUARDIAN_K5_G2_ARQ_BLOCK_BYTES = 256

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
    "guardian_ptt_mode",
    "vara_ptt_delay_ms",
    "ofdm_tx_lead_ms",
    "ofdm_tx_tail_ms",
    "ofdm_min_burst_bytes",
    "ofdm_arq_block_bytes",
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
    ptt_line: str = "RTS"         # "RTS" | "DTR" | "AIOC"

    # One COM-port owner handles UART and PTT for Guardian K5/K61 firmware.
    # AIOC is the safe complementary DTR && !RTS sequence; plain active-high
    # RTS/DTR remain available for differently wired interfaces.
    guardian_ptt_mode: str = "AIOC"  # "AIOC" | "RTS" | "DTR"

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

    # Control-burst modem: "auto" picks AFSK1200 for FM, MFSK16 for HF.
    control_modem: str = "auto"       # "auto" | "afsk1200" | "mfsk16"

    # How the message payload is moved after the handshake. "winlink_manual"
    # was dropped in 0.6.26; a config still holding it is coerced on load.
    # VARA remains the default; ofdm_vhf is the internal transport name for the
    # SC-FTN Guardian modem.
    payload_backend: str = "vara_p2p"  # "vara_p2p" | "ofdm_vhf"

    # Guardian SC-FTN modem settings.  The ``ofdm_*`` names are retained for
    # wire/API compatibility with the shared payload/link code; they do not
    # make OFDM or another G2 waveform reachable in this G1 build.  Automatic
    # policy owns the legacy tuning knobs when enabled.
    ofdm_profile: str = "SC_FTN"
    ofdm_mcs: int = 3
    ofdm_tx_lead_ms: int = GUARDIAN_K5_G2_TX_LEAD_MS
    ofdm_tx_tail_ms: int = GUARDIAN_K5_G2_TX_TAIL_MS
    ofdm_max_retries: int = 3
    ofdm_adaptive_fec: bool = True
    ofdm_modern_ldpc: bool = True
    ofdm_fec: str = "LDPC-4/5"
    ofdm_adaptive_burst: bool = True
    ofdm_burst_bytes: int = 16384
    ofdm_min_burst_bytes: int = 2048
    ofdm_max_burst_bytes: int = 16384
    ofdm_arq_block_bytes: int = 2048
    ofdm_timeout_multiplier: float = 1.0
    ofdm_legacy_mode: bool = False
    ofdm_train_bursts: int = 1
    ofdm_adaptive_train: bool = False
    ofdm_superframe: bool = True
    ofdm_train_gap_ms: int = 30
    ofdm_max_train_seconds: float = 7.5

    # G2 policy identity retained for the SC-FTN profile/AutoTune contract.
    # ``g2_waveform`` is intentionally a one-value registry in this build.
    g2_waveform: str = SC_FTN_WAVEFORM
    g2_bandwidth: str = "2K7"
    g2_mcs: int = 17
    g2_adaptive_mcs: bool = True
    g2_policy_version: int = 1
    g2_tx_scales: dict[str, float] = field(
        default_factory=lambda: {SC_FTN_WAVEFORM: 1.0}
    )

    # AutoTune records are keyed by local radio/audio/PHY identity.  The
    # allowlist and automatic acceptance remain opt-in; Windows mixer gain is
    # likewise off until an integration layer explicitly enables it.
    g2_tx_calibrations: dict[str, dict] = field(default_factory=dict)
    calibration_allowlist: list[str] = field(default_factory=list)
    calibration_auto_accept: bool = False
    calibration_windows_gain: bool = False
    calibration_max_seconds: int = 180

    # Control-burst channel: "off" (idle) | "audio" (real RF via the radio).
    control_channel: str = "off"

    # Mesh / smart routing.
    auto_route: bool = True    # discover a next hop (ROUTE_QUERY) when none known
    auto_relay: bool = True    # forward received messages toward their final dest
    auto_deliver: bool = True  # send waiting Outbox/Transit mail when the hop is heard
    # Bounded multi-hop discovery. Two positions only: "off" ignores every
    # discovery frame, "assisted" takes part -- it answers a query about this
    # station, may look for a route the operator asks for, and pauses an
    # originating message until that route is approved. The receive-only "monitor" position that
    # 0.6.58 and earlier had could do none of that and is migrated to
    # "assisted" on load; see routing.discovery.normalize_discovery_mode.
    discovery_mode: str = "assisted"  # "off" | "assisted"
    discovery_forward: bool = True
    discovery_ttl: int = 4
    discovery_route_lifetime: float = 1800.0
    discovery_frame_budget: int = 12
    discovery_allowlist: list[str] = field(default_factory=list)
    discovery_denylist: list[str] = field(default_factory=list)
    # Use a fresh discovered route without waiting for operator approval. This
    # is fixed on in the shipped station policy.
    discovery_auto_use: bool = True
    link_advert_enabled: bool = True
    link_advert_interval: float = 900.0
    beacon_enabled: bool = False
    beacon_interval: float = 120.0   # seconds between presence beacons
    # This station's Maidenhead locator ("" = unknown). Set from the map or
    # typed in; the finest form (10 characters, ~50 x 90 m) fits the beacon
    # beside any callsign.
    station_grid: str = ""
    # Optional IC-705 USB(B) GPS Out serial device remembered by the map. This
    # is a port choice only; exact GPS coordinates never enter configuration.
    gps_port: str = ""
    # Put that locator in the beacon. Transmitting a position is a deliberate
    # act, so it has its own switch -- though nothing goes out until beacons
    # themselves are enabled, which they are not by default.
    beacon_position: bool = True
    # Draw the raster background on the map. Tiles are fetched only for what
    # is on screen and kept in %APPDATA%\Guardian\maps, so ground the operator
    # has already looked at stays available with no network.
    map_background: bool = True
    # Use a manually installed maps/tiles/z/x/y.png tree only after the
    # operator explicitly confirms that choice in the map window.
    map_local_tiles: bool = False
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

    # Legacy vendor-side binary bundle compression. Production config loading
    # migrates this to ``guardian_aggressive_compression`` and writes it back
    # false; the field remains constructible for lower-level compatibility.
    vara_file_compression: bool = False
    # Legacy one-pass BZIP2 compression. Production config loading migrates it
    # to ``guardian_aggressive_compression`` and writes it back false.
    guardian_compression: bool = False
    # Optional XZ/LZMA2 + JPEG XL/ZopfliPNG preparation.  It is a separate
    # selected path and is never stacked with BZIP2 or native VARA FILES.
    guardian_aggressive_compression: bool = False
    # After the final destination has queued its last control acknowledgement,
    # identify the two stations once in 40 WPM Morse. Off by default.
    morse_id_after_ack: bool = False

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

    # Named snapshots of the radio page, so a station used with more than one
    # rig or cable is one pick away from each of them instead of nine fields
    # re-entered from memory. Radio settings only: a profile must never carry
    # a callsign, an audio device or a VARA port from one setup to another.
    radio_profiles: dict[str, dict] = field(default_factory=dict)

    def enforce_production_policy(self) -> "StationConfig":
        """Normalize a station profile to the shipped production policy.

        This boundary is intentionally separate from ``__post_init__``.  Unit
        tests and protocol integrations often construct a ``StationConfig``
        with a feature disabled to exercise a branch; forcing those values at
        construction time would make those low-level scenarios impossible.
        The application calls this method while loading, saving, and applying
        the user-facing settings profile.
        """
        for name in PRODUCTION_FIXED_TRUE_FIELDS:
            setattr(self, name, True)

        legacy_compression = any(
            bool(getattr(self, name, False)) for name in LEGACY_COMPRESSION_FIELDS
        )
        self.guardian_aggressive_compression = bool(
            self.guardian_aggressive_compression or legacy_compression
        )
        for name in LEGACY_COMPRESSION_FIELDS:
            setattr(self, name, False)
        return self

    @classmethod
    def load(cls, path: Path | str | None = None) -> "StationConfig":
        path = Path(path) if path else DEFAULT_CONFIG_PATH
        if not path.exists():
            cfg = cls()
            cfg.enforce_production_policy()
            cfg.save(path)
            return cfg
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls().enforce_production_policy()
        if not isinstance(data, dict):
            return cls().enforce_production_policy()
        # Only keep keys we know about, so old/new files stay compatible.
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in known}
        # The manual Winlink hand-off was removed in 0.6.26; a station whose
        # config still selects it must not be left without a transport.
        if clean.get("payload_backend") not in PAYLOAD_BACKENDS:
            clean["payload_backend"] = "vara_p2p"
        # A hand-edited or truncated file must not leave the profile picker
        # holding something that is not a profile.
        profiles = clean.get("radio_profiles")
        if isinstance(profiles, dict):
            normalized_profiles = {}
            for key, value in profiles.items():
                normalized = radio_profile_name(key)
                if not normalized or not isinstance(value, dict):
                    continue
                profile = dict(value)
                if str(profile.get("radio_backend", "")).strip().lower() == "guardian_k5":
                    profile.setdefault("ofdm_tx_lead_ms", GUARDIAN_K5_G2_TX_LEAD_MS)
                    profile.setdefault("ofdm_tx_tail_ms", GUARDIAN_K5_G2_TX_TAIL_MS)
                    try:
                        profile_min_burst = int(profile.get("ofdm_min_burst_bytes", 2048))
                    except (TypeError, ValueError):
                        profile_min_burst = None
                    try:
                        profile_arq = int(profile.get("ofdm_arq_block_bytes", 2048))
                    except (TypeError, ValueError):
                        profile_arq = None
                    if profile_min_burst in {1024, 2048}:
                        profile["ofdm_min_burst_bytes"] = GUARDIAN_K5_G2_MIN_BURST_BYTES
                    if profile_arq in {512, 2048}:
                        profile["ofdm_arq_block_bytes"] = GUARDIAN_K5_G2_ARQ_BLOCK_BYTES
                normalized_profiles[normalized] = profile
            clean["radio_profiles"] = normalized_profiles
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

        # This port exposes one production waveform family.  A malformed local
        # setting gets the safe local default; peer profile offers are checked
        # by the transport handshake and must never be normalized here.
        waveform = str(clean.get("g2_waveform", SC_FTN_WAVEFORM)).strip().lower()
        clean["g2_waveform"] = (
            SC_FTN_WAVEFORM if waveform != SC_FTN_WAVEFORM else waveform
        )
        bandwidth = str(clean.get("g2_bandwidth", "2K7")).strip().upper()
        if bandwidth in SC_FTN_BANDWIDTHS:
            clean["g2_bandwidth"] = bandwidth
        else:
            clean.pop("g2_bandwidth", None)

        # Preserve the G2 AutoTune record shape while accepting only records
        # belonging to SC-FTN.  The record's tx_scale is the one required
        # field; identity/evidence fields remain available to the integration
        # layer and are not used to seed a new station.
        scales = clean.get("g2_tx_scales")
        if isinstance(scales, dict):
            try:
                clean["g2_tx_scales"] = {
                    SC_FTN_WAVEFORM: min(
                        G2_MAX_TX_SCALE,
                        max(0.001, float(scales.get(SC_FTN_WAVEFORM, 1.0))),
                    )
                }
            except (TypeError, ValueError):
                clean.pop("g2_tx_scales", None)
        else:
            clean.pop("g2_tx_scales", None)

        records = clean.get("g2_tx_calibrations")
        if isinstance(records, dict):
            validated: dict[str, dict] = {}
            for key, value in records.items():
                if not isinstance(key, str) or not isinstance(value, dict):
                    continue
                record_waveform = str(
                    value.get("waveform", SC_FTN_WAVEFORM)
                ).strip().lower()
                if record_waveform != SC_FTN_WAVEFORM:
                    continue
                try:
                    scale = min(
                        G2_MAX_TX_SCALE,
                        max(0.001, float(value["tx_scale"])),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                record = dict(value)
                record["waveform"] = SC_FTN_WAVEFORM
                record["tx_scale"] = scale
                validated[key[:512]] = record
            clean["g2_tx_calibrations"] = validated
        else:
            clean.pop("g2_tx_calibrations", None)

        try:
            clean["g2_policy_version"] = max(
                1, int(clean.get("g2_policy_version", 1))
            )
        except (TypeError, ValueError):
            clean.pop("g2_policy_version", None)
        try:
            clean["g2_mcs"] = min(
                20, max(0, int(clean.get("g2_mcs", 17)))
            )
        except (TypeError, ValueError):
            clean.pop("g2_mcs", None)

        # Keep legacy ``ofdm_*`` field names accepted by the shared link code,
        # but constrain values to the audited SC-FTN policy schema.
        allowed_fec = {
            "1/2", "2/3", "3/4", "5/6", "7/8",
            "LDPC-1/2", "LDPC-2/3", "LDPC-7/10", "LDPC-3/4",
            "LDPC-4/5", "LDPC-7/8", "LDPC-9/10",
        }
        if str(clean.get("ofdm_fec", "")).strip() not in allowed_fec:
            clean.pop("ofdm_fec", None)
        burst_ladder = {256, 512, 1024, 2048, 4096, 8192, 16384}
        for name in (
            "ofdm_burst_bytes",
            "ofdm_min_burst_bytes",
            "ofdm_max_burst_bytes",
            "ofdm_arq_block_bytes",
        ):
            if name not in clean:
                continue
            try:
                value = int(clean[name])
            except (TypeError, ValueError):
                clean.pop(name, None)
                continue
            if value not in burst_ladder:
                clean.pop(name, None)
            else:
                clean[name] = value
        try:
            arq_bytes = int(clean.get("ofdm_arq_block_bytes", 512))
        except (TypeError, ValueError):
            arq_bytes = 512
            clean.pop("ofdm_arq_block_bytes", None)
        for name in (
            "ofdm_burst_bytes",
            "ofdm_min_burst_bytes",
            "ofdm_max_burst_bytes",
        ):
            if name in clean and int(clean[name]) < arq_bytes:
                clean.pop(name)
        try:
            timeout_multiplier = float(
                clean.get("ofdm_timeout_multiplier", 1.0)
            )
        except (TypeError, ValueError):
            clean.pop("ofdm_timeout_multiplier", None)
        else:
            clean["ofdm_timeout_multiplier"] = min(
                4.0, max(0.5, timeout_multiplier)
            )
        try:
            clean["ofdm_train_bursts"] = min(
                8, max(1, int(clean.get("ofdm_train_bursts", 1)))
            )
            clean["ofdm_train_gap_ms"] = min(
                200, max(10, int(clean.get("ofdm_train_gap_ms", 30)))
            )
            clean["ofdm_max_train_seconds"] = min(
                60.0,
                max(1.0, float(clean.get("ofdm_max_train_seconds", 20.0))),
            )
        except (TypeError, ValueError):
            for name in (
                "ofdm_train_bursts",
                "ofdm_train_gap_ms",
                "ofdm_max_train_seconds",
            ):
                clean.pop(name, None)

        values = clean.get("calibration_allowlist")
        if isinstance(values, list):
            clean["calibration_allowlist"] = [
                str(value).strip().upper()
                for value in values
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

        guardian_ptt_mode = str(
            clean.get("guardian_ptt_mode", "AIOC")
        ).strip().upper()
        clean["guardian_ptt_mode"] = (
            guardian_ptt_mode
            if guardian_ptt_mode in {"AIOC", "RTS", "DTR"}
            else "AIOC"
        )
        return cls(**clean).enforce_production_policy()

    def save(self, path: Path | str | None = None) -> Path:
        self.enforce_production_policy()
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
