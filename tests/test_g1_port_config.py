"""Configuration contracts added by the SC-FTN/radio foundation port."""

from __future__ import annotations

import json
from pathlib import Path

from guardian.config import (
    G2_MAX_TX_SCALE,
    GUARDIAN_K5_G2_ARQ_BLOCK_BYTES,
    GUARDIAN_K5_G2_MIN_BURST_BYTES,
    GUARDIAN_K5_G2_TX_LEAD_MS,
    GUARDIAN_K5_G2_TX_TAIL_MS,
    SC_FTN_BANDWIDTHS,
    SC_FTN_WAVEFORM,
    StationConfig,
    config_dir,
)


def test_g1_identity_path_and_defaults_are_preserved(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    station_dir = config_dir()

    assert station_dir == tmp_path / "Guardian"
    assert not (tmp_path / "Guardian-G2").exists()
    defaults = StationConfig()
    assert defaults.callsign == "NOCALL"
    assert defaults.payload_backend == "vara_p2p"
    assert defaults.g2_waveform == SC_FTN_WAVEFORM
    assert defaults.g2_bandwidth in SC_FTN_BANDWIDTHS
    assert defaults.g2_tx_scales == {SC_FTN_WAVEFORM: 1.0}


def test_old_g1_fields_round_trip_with_sc_ftn_schema(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    original = StationConfig(
        callsign="OK7PS",
        operator_name="Operator",
        gps_port="COM4",
        map_local_tiles=True,
        vara_mode="HF",
        vara_hf_cmd_port=8400,
        vara_hf_data_port=8401,
        g2_bandwidth="20K",
    )
    original.save(path)
    loaded = StationConfig.load(path)

    assert (loaded.callsign, loaded.operator_name, loaded.gps_port) == (
        "OK7PS",
        "Operator",
        "COM4",
    )
    assert loaded.map_local_tiles is True
    assert (loaded.vara_mode, loaded.vara_hf_cmd_port, loaded.vara_hf_data_port) == (
        "HF",
        8400,
        8401,
    )
    assert loaded.payload_backend == "vara_p2p"
    assert loaded.g2_waveform == SC_FTN_WAVEFORM
    assert loaded.g2_bandwidth == "20K"


def test_load_accepts_only_sc_ftn_waveform_and_audited_widths(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "payload_backend": "ofdm_vhf",
                "g2_waveform": "sc_fde_ftn",
                "g2_bandwidth": "10K",
                "g2_tx_scales": {"ofdm": 0.5, "sc_fde_ftn": 1.5, "sc_ftn": 3.0},
            }
        ),
        encoding="utf-8",
    )
    loaded = StationConfig.load(path)

    assert loaded.payload_backend == "ofdm_vhf"
    assert loaded.g2_waveform == SC_FTN_WAVEFORM
    assert loaded.g2_bandwidth == "10K"
    assert loaded.g2_tx_scales == {SC_FTN_WAVEFORM: G2_MAX_TX_SCALE}

    path.write_text('{"g2_bandwidth": "40K"}', encoding="utf-8")
    assert StationConfig.load(path).g2_bandwidth == "2K7"


def test_autotune_records_keep_required_scale_and_identity_evidence(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    valid = {
        "tx_scale": 3.0,
        "waveform": "SC_FTN",
        "bandwidth": "2K7",
        "radio": "Quansheng UV-K5 / Guardian K5FW",
        "radio_backend": "guardian_k5",
        "rig_model": 0,
        "control_endpoint": "COM3",
        "audio_output": "AIOC Audio",
        "audio_host_api": "WASAPI",
        "mode": "FM",
        "policy_version": 1,
        "confidence": "paired-repeat-sweep",
        "peer_callsign": "OK1AAA",
        "metrics": {"median": 0.2},
    }
    path.write_text(
        json.dumps(
            {
                "g2_tx_calibrations": {
                    "valid": valid,
                    "missing-scale": {"waveform": "sc_ftn"},
                    "other-family": {"waveform": "sc_fde_ftn", "tx_scale": 0.4},
                },
                "calibration_allowlist": [" ok1aaa ", ""],
                "calibration_auto_accept": True,
                "calibration_windows_gain": True,
                "calibration_max_seconds": 9999,
            }
        ),
        encoding="utf-8",
    )
    loaded = StationConfig.load(path)

    assert set(loaded.g2_tx_calibrations) == {"valid"}
    record = loaded.g2_tx_calibrations["valid"]
    assert record["tx_scale"] == G2_MAX_TX_SCALE
    assert record["waveform"] == SC_FTN_WAVEFORM
    assert record["peer_callsign"] == "OK1AAA"
    assert record["metrics"] == {"median": 0.2}
    assert loaded.calibration_allowlist == ["OK1AAA"]
    assert loaded.calibration_auto_accept is True
    assert loaded.calibration_windows_gain is True
    assert loaded.calibration_max_seconds == 900


def test_guardian_k5_profile_carries_current_edge_and_block_guards(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "radio_profiles": {
                    "K5": {
                        "radio_backend": "guardian_k5",
                        "ofdm_min_burst_bytes": 2048,
                        "ofdm_arq_block_bytes": 2048,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    loaded = StationConfig.load(path)
    profile = loaded.radio_profiles["K5"]

    assert profile["ofdm_tx_lead_ms"] == GUARDIAN_K5_G2_TX_LEAD_MS
    assert profile["ofdm_tx_tail_ms"] == GUARDIAN_K5_G2_TX_TAIL_MS
    assert profile["ofdm_min_burst_bytes"] == GUARDIAN_K5_G2_MIN_BURST_BYTES
    assert profile["ofdm_arq_block_bytes"] == GUARDIAN_K5_G2_ARQ_BLOCK_BYTES
