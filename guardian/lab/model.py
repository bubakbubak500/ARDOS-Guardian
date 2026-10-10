"""Campaign validation; modem settings remain ordinary StationConfig settings."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import fields, asdict
from pathlib import Path

from ..platform_support import VARA_AVAILABLE
from ..radio.presets import DUMMY_MODEL

MODES = {"SC-FTN": "ofdm_vhf", "VARA FM": "vara_p2p",
         "VARA HF": "vara_p2p", "ARDOP": "ardop"}


def template() -> dict:
    from guardian.config import StationConfig
    from guardian.waveforms.config import profile_names
    config = asdict(StationConfig().enforce_production_policy())
    endpoints = {}
    for name, call, port in (("a", "", 18300), ("b", "", 18400)):
        station = deepcopy(config)
        station.update(callsign=call, rigctld_port=14532 if name == "a" else 14533)
        endpoints[name] = {"config": station, "vara_directory": "",
                           "vara_command_port": port, "license_label": name.upper()}
    result = {"schema_version": 1, "name": "Guardian production comparison",
            "endpoints": endpoints, "cases": [{"modem": "SC-FTN", "bandwidth": "2K7",
            "bytes": 10240, "repetitions": 3, "directions": ["a-to-b", "b-to-a"]}],
            "learning": "sequence", "timeout_seconds": 300, "settle_seconds": 2,
            "stop_on_failure": True, "notes": "",
            "available_profiles": list(profile_names())}
    if not VARA_AVAILABLE:
        result["available_modems"] = ["SC-FTN", "ARDOP"]
    return result


def validate(plan: dict) -> dict:
    from guardian.config import StationConfig
    from guardian.waveforms.config import profile_for
    if not isinstance(plan, dict):
        raise ValueError("Plan must be a JSON object")
    value = deepcopy(plan)
    if value.get("schema_version") != 1:
        raise ValueError("Unsupported LAB plan schema")
    if not isinstance(value.get("endpoints"), dict) or set(value["endpoints"]) != {"a", "b"}:
        raise ValueError("Exactly two endpoints a/b are required")
    known = {f.name for f in fields(StationConfig)}
    calls, ports, cat, audio_out, audio_in = set(), set(), set(), set(), set()
    for label, endpoint in value["endpoints"].items():
        if not isinstance(endpoint, dict) or not isinstance(endpoint.get("config"), dict):
            raise ValueError(f"{label}: endpoint and config must be objects")
        raw = endpoint.get("config", {})
        if set(raw) - known:
            raise ValueError(f"{label}: unknown Guardian config fields: {sorted(set(raw)-known)}")
        cfg = StationConfig(**raw).enforce_production_policy()
        call = cfg.callsign.strip().upper()
        if not call or call in {"NOCALL", "N0CALL"} or call in calls:
            raise ValueError("Two distinct configured station callsigns are required")
        calls.add(call)
        cfg.callsign = call
        if cfg.dual_radio_enabled:
            raise ValueError("Each LAB endpoint represents one radio; disable dual_radio_enabled")
        if cfg.beacon_enabled:
            raise ValueError("Disable automatic beacons in the LAB station profiles")
        if cfg.radio_backend not in {"hamlib", "vox", "guardian_k5", "guardian_k61"}:
            raise ValueError(f"{label}: select the actual radio/PTT driver")
        for field, used in (("audio_input", audio_in), ("audio_output", audio_out)):
            hint = str(getattr(cfg, field)).strip()
            if not hint or hint in used:
                raise ValueError(f"{label}: select a distinct, explicit {field}")
            used.add(hint)
        if cfg.cat_port:
            key = cfg.cat_port.strip().upper()
            if key in cat:
                raise ValueError("Both stations cannot own the same COM port")
            cat.add(key)
        elif cfg.radio_backend in {"vox", "guardian_k5", "guardian_k61"}:
            raise ValueError(f"{label}: COM port is required for serial PTT")
        if cfg.radio_backend == "hamlib":
            if cfg.rigctld_host not in {"127.0.0.1", "localhost"}:
                raise ValueError("This LAB uses local owned rigctld processes")
            key = (cfg.rigctld_host, cfg.rigctld_port)
            if key in ports:
                raise ValueError("Both stations cannot share rigctld")
            ports.add(key)
        base = int(endpoint.get("vara_command_port", 0))
        if not 1024 <= base <= 65532:
            raise ValueError("VARA command port must be 1024..65532")
        for port in (base, base+1, base+2):
            key = ("127.0.0.1", port)
            if key in ports:
                raise ValueError("Overlapping modem/rigctld ports")
            ports.add(key)
        endpoint["config"] = asdict(cfg)
    cases = value.get("cases", [])
    if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
        raise ValueError("A campaign needs 1..100 cases")
    count = 0
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Each case must be an object")
        if not VARA_AVAILABLE and case.get("modem") in {"VARA FM", "VARA HF"}:
            raise ValueError("VARA is unavailable on Linux. Use SC-FTN or ARDOP.")
        if case.get("modem") not in MODES:
            raise ValueError("Unknown modem; use SC-FTN, VARA FM, VARA HF or ARDOP")
        if case["modem"] == "SC-FTN":
            profile_for("sc_ftn", case.get("bandwidth", "2K7"))
        else:
            case.pop("bandwidth", None)
        if case.get("channel"):
            channel = case["channel"]
            if not isinstance(channel, dict):
                raise ValueError("channel must be an object")
            if int(channel.get("frequency_hz", 0)) <= 0 or channel.get("mode") not in {"FM", "USB", "LSB", "PKTUSB", "PKTFM"}:
                raise ValueError("channel requires frequency_hz and a supported radio mode")
            if "passband_hz" in channel:
                try:
                    passband = int(channel["passband_hz"])
                except (TypeError, ValueError):
                    raise ValueError("channel passband_hz must be positive") from None
                if isinstance(channel["passband_hz"], bool) or passband <= 0:
                    raise ValueError("channel passband_hz must be positive")
                channel["passband_hz"] = passband
                if any(endpoint["config"]["radio_backend"] != "hamlib"
                       or endpoint["config"]["rig_model"] == DUMMY_MODEL
                       for endpoint in value["endpoints"].values()):
                    raise ValueError("channel passband_hz requires Hamlib CAT radio endpoints")
        size = int(case.get("bytes", 10240))
        if not 1 <= size <= 32 * 1024 * 1024:
            raise ValueError("Payload size must be 1 byte..32 MiB")
        case["bytes"] = size
        if not 1 <= int(case.get("repetitions", 1)) <= 100:
            raise ValueError("Repetitions must be 1..100")
        directions = case.get("directions", ["a-to-b", "b-to-a"])
        if not directions or len(directions) != len(set(directions)) or set(directions)-{"a-to-b", "b-to-a"}:
            raise ValueError("Invalid or duplicate directions")
        case["directions"] = directions
        if case.get("payload_path") and not Path(case["payload_path"]).is_file():
            raise ValueError("Payload file does not exist")
        count += int(case.get("repetitions", 1)) * len(directions)
    if count > 1000:
        raise ValueError("Maximum 1000 trials per campaign")
    if value.get("learning", "sequence") not in {"cold", "sequence"}:
        raise ValueError("learning must be cold or sequence")
    if not 10 <= float(value.get("timeout_seconds", 300)) <= 7200:
        raise ValueError("Timeout must be 10..7200 seconds")
    if not 0 <= float(value.get("settle_seconds", 2)) <= 600:
        raise ValueError("Settle must be 0..600 seconds")
    value["trial_count"] = count
    return value


def station_config(endpoint: dict, case: dict) -> dict:
    """Only operator-level selections change. Production resolves all tuning."""
    config = deepcopy(endpoint["config"])
    config["payload_backend"] = MODES[case["modem"]]
    if case["modem"].startswith("VARA"):
        config["vara_mode"] = case["modem"].split()[1]
    if case["modem"] == "SC-FTN":
        config["g2_bandwidth"] = case.get("bandwidth", "2K7")
    return config


def content_identity(mail) -> dict:
    import hashlib
    return {"source": mail.source, "destination": mail.final_dest,
            "subject": mail.subject, "body": mail.body, "priority": mail.priority,
            "attachments": [{"name": a.name, "bytes": len(a.data),
                "sha256": hashlib.sha256(a.data).hexdigest()} for a in mail.attachments]}
