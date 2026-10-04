"""LAB contracts and failure handling. These tests do not claim RF performance."""
from __future__ import annotations

from copy import deepcopy
import io
import json
from pathlib import Path
import threading
import urllib.error
import urllib.request
import zipfile

import pytest

from guardian.lab.model import template, validate, station_config, content_identity
from guardian.lab.identity import identity, sha256
from guardian.lab.runner import Lab, write_json
from guardian.lab.server import Server
from guardian.message.mail import MailMessage, Attachment


def plan():
    value = template()
    for label, call, port in (("a", "OK1AAA", "COM31"), ("b", "OK1BBB", "COM32")):
        value["endpoints"][label]["config"].update(callsign=call, radio_backend="vox",
            cat_port=port, ptt_line="AIOC", audio_input=f"RX {label}", audio_output=f"TX {label}")
    return value


def test_plan_preserves_production_settings_and_directional_calibrations():
    value = plan()
    value["endpoints"]["a"]["config"]["g2_tx_scales"] = {"sc_ftn": 0.21}
    value["endpoints"]["b"]["config"]["g2_tx_scales"] = {"sc_ftn": 0.78}
    checked = validate(value)
    cfg = station_config(checked["endpoints"]["a"], {"modem": "SC-FTN", "bandwidth": "4K5"})
    assert cfg["g2_bandwidth"] == "4K5"
    assert cfg["g2_tx_scales"]["sc_ftn"] == 0.21
    assert checked["endpoints"]["b"]["config"]["g2_tx_scales"]["sc_ftn"] == 0.78
    assert cfg["ofdm_fec"] == value["endpoints"]["a"]["config"]["ofdm_fec"]
    assert checked["trial_count"] == 6


@pytest.mark.parametrize("change", [
    lambda p: p.update(schema_version=2),
    lambda p: p["endpoints"]["b"]["config"].update(callsign="OK1AAA"),
    lambda p: p["endpoints"]["b"]["config"].update(audio_output="TX a"),
    lambda p: p["endpoints"]["b"]["config"].update(cat_port="com31"),
    lambda p: p["endpoints"]["a"]["config"].update(unknown_timing=5),
    lambda p: p["endpoints"]["a"]["config"].update(radio_backend="unknown"),
    lambda p: p["endpoints"]["b"].update(vara_command_port=18301),
    lambda p: p["cases"][0].update(bandwidth="7K"),
    lambda p: p["cases"][0].update(directions=["a-to-b", "a-to-b"]),
    lambda p: p["cases"][0].update(repetitions=101),
])
def test_rejects_ambiguous_or_unsupported_plans(change):
    value = plan()
    change(value)
    with pytest.raises(ValueError):
        validate(value)


def test_content_verification_checks_filenames_text_and_bytes():
    original = MailMessage(1, "OK1AAA", "OK1BBB", subject="test", body="text",
                           attachments=[Attachment("binary.bin", b"\0\1\2")])
    received = deepcopy(original)
    received.hops = ["OK1AAA"]  # Expected production routing metadata is local.
    assert content_identity(original) == content_identity(received)
    received.attachments[0].name = "other.bin"
    assert content_identity(original) != content_identity(received)
    received = deepcopy(original)
    received.attachments[0].data = b"\0\1\3"
    assert content_identity(original) != content_identity(received)


def test_identity_includes_current_control_and_data_implementations():
    result = identity()
    for name in ("guardian/operations.py", "guardian/session/orchestrator.py",
                 "guardian/payload/vara_p2p.py", "guardian/payload/ardop.py",
                 "guardian/ofdm/link.py", "guardian/lab/station.py"):
        assert name in result["files"]
    assert len(result["fingerprint"]) == 64


def test_two_real_processes_share_factories_and_isolate_state():
    from guardian.lab.selftest import run
    result = run()
    assert result["passed"] and not result["rf_started"]
    assert len({s["state_dir"] for s in result["stations"]}) == 2
    for station in result["stations"]:
        assert station["fingerprint"] == result["fingerprint"]
        assert {r["backend"] for r in station["contracts"]} == {
            "guardian.payload.vara_p2p.VaraP2PBackend",
            "guardian.payload.ofdm_vhf.OfdmVhfBackend", "guardian.payload.ardop.ArdopBackend"}
        assert station["contracts"][2]["control"] == "guardian.modem.ardop.ArdopControlModem"


def test_export_never_includes_private_license_or_payload(tmp_path):
    lab = Lab(tmp_path)
    run_id = "20261004-120000-1234abcd"
    folder = tmp_path / "runs" / run_id
    (folder / "private").mkdir(parents=True)
    write_json(folder / "summary.json", {"state": "completed"})
    write_json(folder / "group-1.json", {"license_label": "A"})
    (folder / "private" / "VARAFM.ini").write_text("Registration Code=SECRET")
    (folder / "secret.txt").write_text("SECRET")
    with zipfile.ZipFile(io.BytesIO(lab.export(run_id))) as archive:
        assert set(archive.namelist()) == {"summary.json", "group-1.json", "SHA256SUMS.json"}
        assert b"SECRET" not in b"".join(archive.read(n) for n in archive.namelist())
    with pytest.raises(ValueError):
        lab.export("../../secret")


@pytest.fixture
def server(tmp_path):
    instance = Server(tmp_path)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance
    instance.shutdown()
    instance.server_close()
    thread.join(3)


def request(server, path, body=None, *, token=True, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = "Bearer " + server.token
    return urllib.request.urlopen(urllib.request.Request(server.url + path,
        data=None if body is None else json.dumps(body).encode(), headers=h), timeout=5)


def test_api_requires_auth_and_rejects_cross_origin(server):
    with pytest.raises(urllib.error.HTTPError) as failure:
        request(server, "/api/status", token=False)
    assert failure.value.code == 401
    with pytest.raises(urllib.error.HTTPError) as failure:
        request(server, "/api/run", {"plan": plan(), "arm": True}, headers={"Origin": "https://example.com"})
    assert failure.value.code == 403
    assert server.lab.snapshot()["state"] == "idle"


def test_api_validation_never_starts_radio_and_requires_explicit_run(server):
    with request(server, "/api/validate", plan()) as response:
        assert json.load(response)["hardware_started"] is False
    with pytest.raises(urllib.error.HTTPError) as failure:
        request(server, "/api/run", {"plan": plan()})
    assert failure.value.code == 400
    assert server.lab.thread is None
    with request(server, "/api/status") as response:
        assert json.load(response)["state"] == "idle"


def test_api_ui_and_event_cursor(server):
    with request(server, "/", token=False) as response:
        assert "Guardian LAB" in response.read().decode()
    server.lab.emit("first")
    server.lab.emit("second")
    with request(server, "/api/events?after=1") as response:
        value = json.load(response)
    assert [e["kind"] for e in value["events"]] == ["second"]
    assert value["cursor"] == 2


def test_service_refuses_second_owner_of_same_directory(server):
    with pytest.raises((RuntimeError, OSError)):
        Server(server.lab.root)


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows TCP ownership API")
def test_windows_tcp_ownership_matches_the_actual_listener(server):
    import os
    from guardian.lab.vara import listeners
    assert listeners()[server.server_port] == os.getpid()


def test_malformed_vara_ini_does_not_leak_license_in_error(tmp_path):
    from guardian.lab.vara import read_ini
    path = tmp_path / "VARAFM.ini"
    path.write_text("Registration Code=SECRET-DO-NOT-EXPORT")
    with pytest.raises(ValueError) as error:
        read_ini(path)
    assert "SECRET" not in str(error.value)


def test_changed_code_fails_before_station_hardware_is_started(tmp_path, monkeypatch):
    from guardian.lab import runner
    actual = identity()
    wrong = {**actual, "fingerprint": "deliberate-test-mismatch"}
    monkeypatch.setattr(runner, "identity", lambda: wrong)
    lab = Lab(tmp_path)
    lab.start(plan(), armed=True)
    lab.thread.join(20)
    assert not lab.thread.is_alive()
    result = lab.snapshot()
    assert result["state"] == "failed"
    assert result["parity"] == "invalid"
    assert result["results"] == []
    events = [json.loads(line) for line in (lab.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert not any(e.get("event", {}).get("kind") in {"ready", "submitted"} for e in events)
    assert (lab.run_dir / "summary.json").is_file()


def test_running_service_refuses_newly_edited_source(tmp_path, monkeypatch):
    from guardian.lab import runner
    lab = Lab(tmp_path)
    monkeypatch.setattr(runner, "identity", lambda: {"fingerprint": "new-revision"})
    with pytest.raises(ValueError, match="restart"):
        lab.start(plan(), armed=True)
    assert lab.thread is None


def test_vara_private_copy_preserves_separate_license_and_exact_binary(tmp_path, monkeypatch):
    from guardian.lab import vara
    from guardian.install import dependencies
    source = tmp_path / "licensed-a"
    source.mkdir()
    exe = source / "VARAFM.exe"
    exe.write_bytes(b"test fixture, not executable")
    (source / "VARAFM.ini").write_text(
        "[Setup]\nCallsign Licence 0=OK1AAA\nRegistration Code=PRIVATE-CODE\n"
        "TCP Command Port=8300\nTCP Scan Port=8427\nEnable KISS=1\nUpdates=1\nFM Mode=1\n"
        "[Soundcard]\nInput Device Name=RX a\nOutput Device Name=TX a\nALC Drive Level=-5\n"
        "[PTT]\nVia=3\n", encoding="ascii")
    monkeypatch.setattr(dependencies, "find_vara_fm", lambda _: str(exe))
    monkeypatch.setattr(vara, "listeners", lambda: {})
    endpoint = plan()["endpoints"]["a"]
    endpoint["vara_directory"] = str(source)
    cfg = endpoint["config"]
    destination = tmp_path / "private-copy"
    report = vara.prepare(endpoint, cfg, destination)
    assert report["sha256"] == sha256(exe)
    assert "PRIVATE-CODE" not in json.dumps(report)
    copied, _ = vara.read_ini(destination / "VARAFM.ini")
    assert copied["Setup"]["Registration Code"] == "PRIVATE-CODE"
    assert copied["Setup"]["TCP Command Port"] == "18300"
    assert copied["Soundcard"]["ALC Drive Level"] == "-5"
    assert "TCP Command Port=8300" in (source / "VARAFM.ini").read_text()
    assert cfg["vara_fm_path"] == str(destination / "VARAFM.exe")


def test_vara_rejects_different_revision_before_copy(tmp_path, monkeypatch):
    from guardian.lab import vara
    from guardian.install import dependencies
    reference = tmp_path / "reference" / "VARAFM.exe"
    reference.parent.mkdir()
    reference.write_bytes(b"production")
    source = tmp_path / "license"
    source.mkdir()
    (source / "VARAFM.exe").write_bytes(b"older modem")
    monkeypatch.setattr(dependencies, "find_vara_fm", lambda _: str(reference))
    endpoint = plan()["endpoints"]["a"]
    endpoint["vara_directory"] = str(source)
    with pytest.raises(ValueError, match="differs"):
        vara.prepare(endpoint, endpoint["config"], tmp_path / "copy")
    assert not (tmp_path / "copy").exists()


@pytest.mark.parametrize("delivered,exact,transport,profile,expected", [
    (True, True, "ofdm_vhf", "same-profile", True), (False, True, "ofdm_vhf", "same-profile", False),
    (True, False, "ofdm_vhf", "same-profile", False), (True, True, "vara_p2p", "same-profile", False),
    (True, True, "ofdm_vhf", "different-bandwidth", False)])
def test_trial_requires_final_receipt_exact_contents_and_actual_modem(
        tmp_path, monkeypatch, delivered, exact, transport, profile, expected):
    lab = Lab(tmp_path)
    lab.run_dir = tmp_path
    (tmp_path / "private").mkdir()
    lab.state.update(trial_count=1)
    content = {"attachments": [{"sha256": "expected"}]}
    a, b = {"latest": {}}, {"latest": {}}
    class Pipe:
        def __init__(self, label): self.label = label
        def send(self, command):
            if command["op"] == "send":
                if expected:
                    assert Path(command["payload_path"]).name == "sample.txt"
                    assert Path(command["payload_path"]).read_bytes() == b"own payload"
                a["latest"]["submitted"] = {"message_id": 42, "content": content,
                    "original_bytes": 1000, "bundle_bytes": 1200}
                a["latest"]["snapshot"] = {"message_id": 42, "elapsed": 10,
                    "status": "delivered" if delivered else "failed", "session": {
                        "transport": transport, "profile_token": profile, "wire_bytes": 1100, "transfer_started_at": 2,
                        "payload_sent_at": 8}}
            elif command["op"] == "inspect":
                b["latest"]["mail"] = {"message_id": 42, "content": content if exact else {}}
    a["pipe"], b["pipe"] = Pipe("a"), Pipe("b")
    for child in (a, b):
        child["latest"]["snapshot"] = {"busy": False}
        child["latest"]["ready"] = {"profile_token": "same-profile"}
    lab.children = {"a": a, "b": b}
    monkeypatch.setattr(lab, "_wait", lambda predicate, timeout, description: pytest.fail(description) if not predicate() else None)
    value = plan()
    value["stop_on_failure"] = False
    if expected:
        sample = tmp_path / "sample.txt"
        sample.write_bytes(b"own payload")
        value["cases"][0]["payload_path"] = str(sample)
    result = lab._trial(value, value["cases"][0], 1, "a-to-b", 1)
    assert result is expected
    row = lab.state["results"][0]
    assert row["application_bps"] == (800 if expected else None)
    assert row["payload_phase_bps"] == (1100*8/6 if expected else None)


def test_csv_preserves_invalid_measurement_and_build_identity(tmp_path):
    import csv
    lab = Lab(tmp_path)
    lab.run_dir = tmp_path
    lab.build = lab.runtime_identity
    lab.state["results"] = [{"trial": 1, "case": {"modem": "SC-FTN", "bandwidth": "4K5"},
        "repetition": 2, "passed": True, "valid_measurement": False, "application_bps": 1234}]
    lab._write_csv()
    with (tmp_path / "results.csv").open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["valid_measurement"] == "False"
    assert row["modem"] == "SC-FTN" and row["bandwidth"] == "4K5"
    assert row["build_fingerprint"] == lab.build["fingerprint"]
