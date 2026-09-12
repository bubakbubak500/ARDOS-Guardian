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


def test_experimental_network_flags_default_off_and_round_trip(tmp_path: Path) -> None:
    defaults = StationConfig()
    assert defaults.discovery_auto_use is False
    assert defaults.link_advert_enabled is False

    path = tmp_path / "config.json"
    configured = StationConfig(
        discovery_auto_use=True,
        link_advert_enabled=True,
        link_advert_interval=600.0,
    )
    configured.save(path)
    loaded = StationConfig.load(path)
    assert loaded.discovery_auto_use is True
    assert loaded.link_advert_enabled is True
    assert loaded.link_advert_interval == 600.0


def test_discovery_has_two_modes_and_a_monitor_profile_is_migrated(
    tmp_path: Path,
) -> None:
    # A station takes part by default: the receive-only position it may have
    # been left in could neither answer a query nor produce a usable route.
    assert StationConfig().discovery_mode == "assisted"

    path = tmp_path / "config.json"
    path.write_text('{"discovery_mode": "monitor"}', encoding="utf-8")
    assert StationConfig.load(path).discovery_mode == "assisted"

    for stored, expected in (("off", "off"), ("assisted", "assisted")):
        path.write_text(f'{{"discovery_mode": "{stored}"}}', encoding="utf-8")
        assert StationConfig.load(path).discovery_mode == expected

    path.write_text('{"discovery_mode": "sometimes"}', encoding="utf-8")
    assert StationConfig.load(path).discovery_mode == "assisted"


def test_radio_profiles_carry_the_radio_page_and_nothing_else(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    config = StationConfig(
        callsign="OK7PS",
        radio_backend="hamlib",
        radio="Icom IC-705",
        rig_model=3085,
        cat_port="COM4",
        ptt_type="RIG",
        vara_ptt_delay_ms=0,
        audio_input="USB Audio CODEC RX",
        vara_cmd_port=8300,
    )

    assert config.save_radio_profile("  IC-705  ") == "IC-705"
    # The handheld on an AIOC cable: a different rig, a different keying path.
    config.radio = "Hamlib Dummy"
    config.rig_model = 1
    config.cat_port = "COM9"
    config.ptt_type = "RTS"
    config.vara_ptt_delay_ms = 120
    config.callsign = "OK7PS/P"
    config.audio_input = "Handheld cable"
    config.save_radio_profile("AIOC")
    config.save(path)

    loaded = StationConfig.load(path)
    assert loaded.radio_profile_names() == ["AIOC", "IC-705"]
    assert loaded.apply_radio_profile("IC-705")

    assert (loaded.radio, loaded.rig_model, loaded.cat_port) == (
        "Icom IC-705",
        3085,
        "COM4",
    )
    assert (loaded.ptt_type, loaded.vara_ptt_delay_ms) == ("RIG", 0)
    # A profile is a radio, not a station: what is not on the radio page must
    # survive being handed one.
    assert loaded.callsign == "OK7PS/P"
    assert loaded.audio_input == "Handheld cable"
    assert loaded.vara_cmd_port == 8300

    assert loaded.delete_radio_profile("IC-705")
    assert not loaded.delete_radio_profile("IC-705")
    assert not loaded.apply_radio_profile("IC-705")
    assert loaded.radio_profile_names() == ["AIOC"]


def test_a_profile_from_an_older_build_cannot_blank_a_newer_field() -> None:
    config = StationConfig(radio_backend="hamlib", rig_model=3085, cat_port="COM4")
    config.radio_profiles["partial"] = {"rig_model": 1, "cat_port": "COM9"}

    assert config.apply_radio_profile("partial")

    assert (config.rig_model, config.cat_port) == (1, "COM9")
    assert config.radio_backend == "hamlib"


def test_a_damaged_profile_block_is_dropped_on_load(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    StationConfig().save(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["radio_profiles"] = {"good": {"rig_model": 1}, "bad": "not a profile"}
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert StationConfig.load(path).radio_profile_names() == ["good"]

    payload["radio_profiles"] = ["not", "a", "mapping"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert StationConfig.load(path).radio_profile_names() == []


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
