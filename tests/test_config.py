import json
from pathlib import Path

from guardian.config import StationConfig


def test_config_round_trip_and_ignores_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    original = StationConfig(
        callsign="ok7ps",
        operator_name="Operator",
        vara_mode="HF",
        vara_hf_cmd_port=8400,
        vara_hf_data_port=8401,
        separate_working_channels=True,
    )

    original.save(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["future_setting"] = "ignored"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = StationConfig.load(path)

    assert loaded.callsign == "ok7ps"
    assert loaded.operator_name == "Operator"
    assert loaded.vara_hf_cmd_port == 8400
    assert loaded.separate_working_channels is True
    assert not hasattr(loaded, "future_setting")


def test_invalid_config_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not-json", encoding="utf-8")

    loaded = StationConfig.load(path)

    assert loaded.callsign == "NOCALL"
    assert loaded.radio_backend == "none"
    assert loaded.separate_working_channels is False


def test_vara_mode_remembers_independent_ports_and_selects_modem() -> None:
    config = StationConfig(
        vara_mode="FM",
        vara_cmd_port=8300,
        vara_data_port=8301,
        vara_hf_cmd_port=8400,
        vara_hf_data_port=8401,
    )

    assert config.active_modem() == "afsk1200"
    config.apply_vara_mode("HF")

    assert (config.vara_cmd_port, config.vara_data_port) == (8400, 8401)
    assert config.active_modem() == "mfsk16"

    config.vara_cmd_port = 8500
    config.vara_data_port = 8501
    config.remember_vara_ports()
    config.apply_vara_mode("FM")

    assert (config.vara_cmd_port, config.vara_data_port) == (8300, 8301)
    config.apply_vara_mode("HF")
    assert (config.vara_cmd_port, config.vara_data_port) == (8500, 8501)
