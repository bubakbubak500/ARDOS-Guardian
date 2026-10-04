"""Campaign orchestration over two real Guardian processes; never a modem simulator."""
from __future__ import annotations

from copy import deepcopy
import csv
import hashlib
import io
import json
import multiprocessing
from pathlib import Path
import random
import threading
import time
import uuid
import zipfile

from .identity import identity, digest_json
from .model import validate, station_config, MODES
from .station import main as station_main
from .vara import prepare as prepare_vara, OwnedVara


def write_json(path: Path, data):
    temp = path.with_suffix(path.suffix + ".pending")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


class Cancelled(Exception):
    pass


class Lab:
    def __init__(self, root: Path):
        from guardian import __version__
        self.version = __version__
        self.runtime_identity = identity()
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.thread = None
        self.events = []
        self.sequence = 0
        self.state = {"state": "idle", "version": self.version, "run_id": None, "stations": {}, "results": []}
        self.run_dir = None
        self.children = {}
        self.varas = []
        self.build = None

    def emit(self, kind, **data):
        with self.lock:
            self.sequence += 1
            event = {"sequence": self.sequence, "timestamp": time.time(),
                     "monotonic": time.monotonic(), "kind": kind, **data}
            self.events.append(event)
            self.events = self.events[-5000:]
            if self.run_dir:
                with (self.run_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            return event

    def snapshot(self):
        with self.lock:
            return deepcopy(self.state)

    def event_slice(self, after=0):
        with self.lock:
            return {"events": deepcopy([e for e in self.events if e["sequence"] > after]),
                    "cursor": self.sequence,
                    "truncated": bool(self.events and after and after < self.events[0]["sequence"]-1)}

    def start(self, plan: dict, *, armed=False):
        if not armed:
            raise ValueError("A real-radio run requires arm=true")
        plan = validate(plan)
        if identity()["fingerprint"] != self.runtime_identity["fingerprint"]:
            raise ValueError("Guardian changed since this LAB service started; restart the LAB")
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError("A campaign is already running")
            self.cancel.clear()
            run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
            self.run_dir = self.root / "runs" / run_id
            self.run_dir.mkdir(parents=True)
            write_json(self.run_dir / "plan.json", plan)
            self.state = {"state": "preflight", "version": self.version, "run_id": run_id, "stations": {},
                          "results": [], "trial_count": plan["trial_count"], "trial": 0,
                          "error": None, "parity": "checking"}
            self.thread = threading.Thread(target=self._run, args=(plan,), daemon=True,
                                           name="guardian-lab-campaign")
            self.thread.start()
            return run_id

    def stop(self):
        self.cancel.set()

    def _pump(self):
        for label, child in self.children.items():
            process, pipe = child["process"], child["pipe"]
            if process.is_alive():
                try:
                    pipe.send({"op": "heartbeat"})
                except (OSError, EOFError):
                    pass
            for _ in range(1000):
                if not pipe.poll():
                    break
                try:
                    event = pipe.recv()
                except (EOFError, OSError):
                    break
                child["latest"][event["kind"]] = event
                self.emit("station", endpoint=label, event=event)
                with self.lock:
                    self.state["stations"][label] = deepcopy(child["latest"])
                if event["kind"] == "error":
                    raise RuntimeError(f"Station {label.upper()}: {event['error']}")
            if not process.is_alive() or "stopped" in child["latest"]:
                raise RuntimeError(f"Station {label.upper()} exited ({process.exitcode})")
        if self.cancel.is_set():
            raise Cancelled()

    def _wait(self, predicate, timeout, description):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._pump()
            if predicate():
                return
            self.cancel.wait(0.1)
        raise TimeoutError(description)

    def _start_pair(self, plan, case, group):
        if identity()["fingerprint"] != self.build["fingerprint"]:
            raise RuntimeError("Guardian files changed since campaign preflight")
        configs, reports = {}, {}
        for label, endpoint in plan["endpoints"].items():
            folder = self.run_dir / "private" / f"group-{group}" / label
            folder.mkdir(parents=True)
            config = station_config(endpoint, case)
            if config["radio_backend"] == "hamlib":
                from guardian.radio.rigctld_launcher import port_in_use
                if port_in_use(config["rigctld_host"], config["rigctld_port"]):
                    raise RuntimeError("LAB requires a free dedicated rigctld port; refusing to reuse another process")
            if case["modem"].startswith("VARA"):
                report = prepare_vara(endpoint, config, folder / "vara")
                reports[label] = report
                self.varas.append(OwnedVara(report))
            write_json(folder / "config.json", config)
            configs[label] = (folder, config)
        if reports and len({r["sha256"] for r in reports.values()}) != 1:
            raise RuntimeError("VARA A/B executable revisions differ")
        for modem in self.varas:
            modem.start(self.cancel)
        context = multiprocessing.get_context("spawn")
        for label, (folder, config) in configs.items():
            parent, child = context.Pipe()
            peer = configs["b" if label == "a" else "a"][1]["callsign"]
            process = context.Process(target=station_main,
                args=(child, str(folder), self.build["fingerprint"], peer, case.get("channel")),
                name=f"Guardian LAB {label.upper()}")
            process.start()
            child.close()
            self.children[label] = {"process": process, "pipe": parent, "latest": {}}
        self._wait(lambda: all("prepared" in c["latest"] for c in self.children.values()),
                   120, "Station preparation timed out")
        prepared = {label: child["latest"]["prepared"] for label, child in self.children.items()}
        for kind in ("input", "output"):
            if prepared["a"]["devices"][kind]["index"] == prepared["b"]["devices"][kind]["index"]:
                raise RuntimeError(f"Both station names resolve to the same audio {kind}")
        if reports:
            for label, report in reports.items():
                for kind, ini_key in (("input", "Input Device Name"), ("output", "Output Device Name")):
                    configured = report["soundcard"].get(ini_key, "").strip()
                    actual = prepared[label]["devices"][kind]["device"]["name"].strip()
                    if configured != actual:
                        raise RuntimeError(f"VARA {label.upper()} {kind} differs from Guardian: {configured!r} / {actual!r}")
        for child in self.children.values():
            child["pipe"].send({"op": "start"})
        self._wait(lambda: all("ready" in c["latest"] for c in self.children.values()),
                   60, "Production station startup timed out")
        for modem in self.varas:
            modem.verify()
        evidence = {"case": case, "build_fingerprint": self.build["fingerprint"],
                    "stations": {k: {"prepared": v["latest"]["prepared"],
                                     "ready": v["latest"]["ready"]} for k, v in self.children.items()},
                    "vara": reports}
        write_json(self.run_dir / f"group-{group}.json", evidence)
        self.emit("pair_ready", group=group, modem=case["modem"], vara=reports)
        with self.lock:
            self.state["state"] = "running"
            self.state["parity"] = "matched; final verification pending"

    def _close_pair(self):
        errors = []
        for child in self.children.values():
            try:
                child["pipe"].send({"op": "stop"})
            except (OSError, EOFError):
                pass
        deadline = time.monotonic() + 20
        while any(c["process"].is_alive() for c in self.children.values()) and time.monotonic() < deadline:
            for label, child in self.children.items():
                pipe = child["pipe"]
                while pipe.poll():
                    try:
                        event = pipe.recv()
                    except (OSError, EOFError):
                        break
                    self.emit("station", endpoint=label, event=event)
                    if event.get("kind") == "stopped":
                        child["latest"]["stopped"] = event
            time.sleep(0.05)
        for label, child in self.children.items():
            process = child["process"]
            if process.is_alive():
                process.terminate()
                errors.append(f"Station {label} needed forced termination; inspect radio PTT")
            process.join(timeout=5)
            while child["pipe"].poll():
                try:
                    event = child["pipe"].recv()
                except (OSError, EOFError):
                    break
                self.emit("station", endpoint=label, event=event)
                if event.get("kind") == "stopped":
                    child["latest"]["stopped"] = event
            stopped = child["latest"].get("stopped", {})
            errors.extend(stopped.get("shutdown_errors", []))
            if stopped.get("fingerprint") != self.build["fingerprint"]:
                errors.append(f"Station {label} did not attest unchanged code at shutdown")
            child["pipe"].close()
        self.children.clear()
        for modem in self.varas:
            try:
                modem.close()
            except Exception as exc:
                errors.append(str(exc))
        self.varas.clear()
        if errors:
            with self.lock:
                self.state["parity"] = "invalid"
            raise RuntimeError("; ".join(errors))

    def _trial(self, plan, case, repetition, direction, number):
        source, destination = ("a", "b") if direction == "a-to-b" else ("b", "a")
        sender, receiver = self.children[source], self.children[destination]
        for modem in self.varas:
            modem.verify()
        self._wait(lambda: all(not c["latest"].get("snapshot", {}).get("busy", True)
                               for c in self.children.values()), 30, "Previous production session is still active")
        sender["latest"].pop("submitted", None)
        receiver["latest"].pop("mail", None)
        payload_dir = self.run_dir / "private" / "payloads"
        payload_dir.mkdir(exist_ok=True)
        path = payload_dir / "payload.bin"
        # The same deterministic bytes for a given size are used for every
        # modem, repetition and direction. No modem-specific benchmark framing.
        data = (Path(case["payload_path"]).read_bytes() if case.get("payload_path")
                else random.Random(int(case.get("seed", 20261004))).randbytes(case["bytes"]))
        if len(data) > 32 * 1024 * 1024:
            raise ValueError("Payload file exceeds 32 MiB")
        path.write_bytes(data)
        with self.lock:
            self.state.update(trial=number, current={"case": case, "repetition": repetition,
                                                    "direction": direction})
        self.emit("trial_started", number=number, case=case, repetition=repetition, direction=direction,
                  payload_sha256=hashlib.sha256(data).hexdigest(), payload_bytes=len(data))
        sender["pipe"].send({"op": "send", "payload_path": str(path),
                              "subject": "Guardian LAB", "body": ""})
        self._wait(lambda: "submitted" in sender["latest"], 15, "Message submission timed out")
        submitted = sender["latest"]["submitted"]
        message_id = submitted["message_id"]
        def terminal():
            snapshot = sender["latest"].get("snapshot", {})
            return snapshot.get("message_id") == message_id and snapshot.get("status") in {"delivered", "failed"}
        self._wait(terminal, float(plan.get("timeout_seconds", 300)), "Message delivery timed out")
        snapshot = sender["latest"]["snapshot"]
        receiver["pipe"].send({"op": "inspect", "message_id": message_id})
        self._wait(lambda: receiver["latest"].get("mail", {}).get("message_id") == message_id,
                   10, "Receiver mailbox inspection timed out")
        received = receiver["latest"]["mail"]
        for modem in self.varas:
            modem.verify()
        session = snapshot.get("session") or {}
        same = received.get("content") == submitted["content"]
        transport_ok = session.get("transport") == MODES[case["modem"]]
        expected_profile = sender["latest"].get("ready", {}).get("profile_token")
        profile_ok = (case["modem"].startswith("VARA") or
                      bool(expected_profile) and session.get("profile_token") == expected_profile)
        elapsed = snapshot["elapsed"]
        passed = snapshot["status"] == "delivered" and same and transport_ok and profile_ok
        phase_start, phase_end = session.get("transfer_started_at"), session.get("payload_sent_at")
        phase = phase_end-phase_start if phase_start and phase_end and phase_end > phase_start else None
        result = {"trial": number, "case": case, "repetition": repetition, "direction": direction,
                  "message_id": message_id, "passed": passed, "byte_exact": same,
                  "sender_delivered": snapshot["status"] == "delivered",
                  "requested_transport": MODES[case["modem"]], "actual_transport": session.get("transport"),
                  "requested_profile": expected_profile, "actual_profile": session.get("profile_token"),
                  "original_bytes": submitted["original_bytes"], "bundle_bytes": submitted["bundle_bytes"],
                  "wire_bytes": session.get("wire_bytes"), "elapsed_seconds": elapsed,
                  "application_bps": submitted["original_bytes"]*8/elapsed if passed and elapsed else None,
                  "payload_phase_seconds": phase,
                  "payload_phase_bps": session.get("wire_bytes", 0)*8/phase if passed and phase else None,
                  "content": submitted["content"], "received_content": received.get("content"),
                  "error": session.get("error", "")}
        self.emit("trial_finished", result=result)
        with self.lock:
            self.state["results"].append(result)
        write_json(self.run_dir / "results.json", self.state["results"])
        if not passed and plan.get("stop_on_failure", True):
            raise RuntimeError("Trial failed: delivery, contents or negotiated modem did not match")
        return passed

    def _run(self, plan):
        failed = False
        try:
            self.build = identity()
            if self.build["fingerprint"] != self.runtime_identity["fingerprint"]:
                raise RuntimeError("Guardian changed since this LAB service started; restart the LAB")
            write_json(self.run_dir / "build.json", self.build)
            self.emit("campaign_started", fingerprint=self.build["fingerprint"], version=self.build["version"])
            number, group = 0, 0
            for case in plan["cases"]:
                for repetition in range(1, int(case.get("repetitions", 1))+1):
                    for direction in case["directions"]:
                        if not self.children:
                            group += 1
                            self._start_pair(plan, case, group)
                        number += 1
                        passed = self._trial(plan, case, repetition, direction, number)
                        failed = failed or not passed
                        if plan.get("learning", "sequence") == "cold" or not passed:
                            self._close_pair()
                        end = time.monotonic() + float(plan.get("settle_seconds", 2))
                        while time.monotonic() < end:
                            self._pump()
                            self.cancel.wait(0.1)
                self._close_pair()
            if identity()["fingerprint"] != self.build["fingerprint"]:
                raise RuntimeError("Guardian code/runtime changed during the campaign")
            with self.lock:
                self.state["state"] = "failed" if failed else "completed"
        except Cancelled:
            with self.lock:
                self.state["state"] = "cancelled"
        except BaseException as exc:
            with self.lock:
                self.state.update(state="failed", error=str(exc))
            self.emit("error", error=str(exc))
        finally:
            final_state = self.state["state"]
            with self.lock:
                self.state["state"] = "stopping"
            try:
                if self.children or self.varas:
                    self._close_pair()
            except Exception as exc:
                with self.lock:
                    self.state.update(state="failed", parity="invalid", error=str(exc))
                self.emit("cleanup_error", error=str(exc))
            if self.build:
                try:
                    final = identity()["fingerprint"]
                except Exception as exc:
                    final = None
                    self.emit("identity_error", error=str(exc))
                with self.lock:
                    if final != self.build["fingerprint"]:
                        self.state.update(state="failed", parity="invalid", error="Guardian changed during measurement")
                    elif self.state.get("parity") != "invalid":
                        self.state["parity"] = "verified"
                    for result in self.state["results"]:
                        result["valid_measurement"] = result.get("passed", False) and self.state["parity"] == "verified"
            with self.lock:
                if self.state["state"] == "stopping":
                    self.state["state"] = final_state
            if self.state.get("trial", 0) > len(self.state["results"]):
                current = self.state.get("current", {})
                self.state["results"].append({"trial": self.state["trial"], **current,
                    "passed": False, "valid_measurement": False, "error": self.state.get("error") or self.state["state"]})
            write_json(self.run_dir / "results.json", self.state["results"])
            self.emit("campaign_finished", state=self.state["state"])
            self._write_csv()
            # summary.json is the completion marker used by export.
            write_json(self.run_dir / "summary.json", self.snapshot())

    def _write_csv(self):
        columns = ["trial", "modem", "bandwidth", "repetition", "direction",
                   "passed", "valid_measurement", "build_fingerprint", "byte_exact", "sender_delivered",
                   "actual_transport", "requested_profile", "actual_profile", "original_bytes", "bundle_bytes", "wire_bytes",
                   "elapsed_seconds", "application_bps", "payload_phase_seconds", "payload_phase_bps"]
        with (self.run_dir / "results.csv").open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for result in self.state["results"]:
                case = result.get("case", {})
                writer.writerow({**result, "modem": case.get("modem"),
                    "bandwidth": case.get("bandwidth"),
                    "build_fingerprint": self.build["fingerprint"] if self.build else None})

    def export(self, run_id: str) -> bytes:
        import re
        if not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{8}", run_id):
            raise ValueError("Invalid run ID")
        folder = self.root / "runs" / run_id
        if not (folder / "summary.json").is_file():
            raise ValueError("Campaign must finish before export")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            # Deliberate allowlist: no licence INI, executable, mailbox or
            # supplied payload can enter a shareable measurement report.
            entries = [p for p in folder.iterdir() if p.is_file() and
                       (p.name in {"plan.json", "build.json", "events.jsonl", "results.json",
                                   "results.csv", "summary.json"} or p.name.startswith("group-"))]
            hashes = {}
            for path in sorted(entries):
                data = path.read_bytes()
                archive.writestr(path.name, data)
                hashes[path.name] = hashlib.sha256(data).hexdigest()
            archive.writestr("SHA256SUMS.json", json.dumps(hashes, indent=2))
        return output.getvalue()
