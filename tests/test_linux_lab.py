"""Linux LAB packaging and selection contracts, without audio or radio access."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import guardian.lab.identity as identity_module
from guardian.lab import model, selftest


@pytest.mark.parametrize("linux", [True, False])
def test_frozen_lab_identity_hashes_linux_shared_libraries_only_on_linux(
    monkeypatch, tmp_path, linux
):
    executable = tmp_path / "Guardian"
    executable.write_bytes(b"executable fixture")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "guardian-build.json").write_text(json.dumps({"revision": "fixture"}))
    ardop = bundle / "libguardian_ardop.so"
    ardop.write_bytes(b"native ARDOP fixture")
    (bundle / "libQt6Core.so.6").write_bytes(b"native Qt fixture")
    (bundle / "guardian_ardop.dll").write_bytes(b"Windows ARDOP fixture")
    monkeypatch.setattr(identity_module, "LINUX", linux)
    monkeypatch.setattr(identity_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(identity_module.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(identity_module.sys, "executable", str(executable))
    before = identity_module.identity()
    assert "bundle/guardian_ardop.dll" in before["files"]
    assert ("bundle/libguardian_ardop.so" in before["files"]) == linux
    assert ("bundle/libQt6Core.so.6" in before["files"]) == linux
    ardop.write_bytes(b"changed native ARDOP fixture")
    after = identity_module.identity()
    assert (before["fingerprint"] != after["fingerprint"]) == linux


@pytest.mark.parametrize("linux,expected", [
    (True, ["ofdm_vhf", "ardop"]),
    (False, ["vara_p2p", "ofdm_vhf", "ardop"]),
])
def test_lab_selftest_probes_only_supported_production_factories(
    monkeypatch, tmp_path, linux, expected
):
    import guardian.config as config_module
    import guardian.modem as modem_module
    import guardian.qt.runtime as runtime_module

    monkeypatch.setattr(selftest, "LINUX", linux)
    requested, controls, closed = [], [], []

    class Config:
        def __init__(self, *, callsign, payload_backend):
            requested.append(payload_backend)
            self.payload_backend = payload_backend

        def enforce_production_policy(self):
            return self

        def save(self, path):
            assert path == tmp_path / "config.json"

        def active_modem(self):
            return self.payload_backend

    class Runtime:
        def __init__(self):
            self.operations = SimpleNamespace(net=SimpleNamespace(payload=SimpleNamespace(
                backends={name: SimpleNamespace() for name in expected})))
            self.workers = SimpleNamespace(close=lambda *, wait: closed.append(wait))

        def close(self):
            closed.append("runtime")

    reports = []
    connection = SimpleNamespace(send=reports.append, close=lambda: closed.append("pipe"))
    monkeypatch.setattr(config_module, "StationConfig", Config)
    monkeypatch.setattr(config_module, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(runtime_module, "ShellRuntime", Runtime)
    monkeypatch.setattr(modem_module, "make_modem",
                        lambda name: controls.append(name) or SimpleNamespace())
    monkeypatch.setattr(identity_module, "identity", lambda: {"fingerprint": "fixture"})
    monkeypatch.setenv("GUARDIAN_STATE_DIR", str(tmp_path))
    selftest.probe(connection, str(tmp_path))
    assert requested == expected
    assert controls == expected
    assert [contract["requested"] for contract in reports[0]["contracts"]] == expected
    assert reports[0]["fingerprint"] == "fixture"
    assert closed.count("runtime") == len(expected)
    assert closed[-1] == "pipe"


def valid_plan():
    plan = model.template()
    for label, call, port in (("a", "OK1AAA", "/dev/ttyUSB0"),
                             ("b", "OK1BBB", "/dev/ttyUSB1")):
        plan["endpoints"][label]["config"].update(
            callsign=call, radio_backend="vox", cat_port=port, ptt_line="RTS",
            audio_input=f"RX {label}", audio_output=f"TX {label}",
        )
    return plan


@pytest.mark.parametrize("modem", ["VARA FM", "VARA HF"])
def test_linux_lab_rejects_vara_cases_before_any_station_preparation(monkeypatch, modem):
    monkeypatch.setattr(model, "VARA_AVAILABLE", False)
    plan = valid_plan()
    plan["cases"][0]["modem"] = modem
    with pytest.raises(ValueError, match="unavailable on Linux.*SC-FTN or ARDOP"):
        model.validate(plan)


@pytest.mark.parametrize("modem", ["SC-FTN", "ARDOP"])
def test_linux_lab_advertises_and_accepts_native_modems(monkeypatch, modem):
    monkeypatch.setattr(model, "VARA_AVAILABLE", False)
    plan = valid_plan()
    assert plan["available_modems"] == ["SC-FTN", "ARDOP"]
    plan["cases"][0]["modem"] = modem
    checked = model.validate(plan)
    assert checked["cases"][0]["modem"] == modem


@pytest.mark.parametrize("modem", ["VARA FM", "VARA HF"])
def test_windows_lab_keeps_vara_cases_and_original_template(monkeypatch, modem):
    monkeypatch.setattr(model, "VARA_AVAILABLE", True)
    plan = valid_plan()
    assert "available_modems" not in plan
    plan["cases"][0]["modem"] = modem
    assert model.validate(plan)["cases"][0]["modem"] == modem
