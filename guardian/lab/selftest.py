"""Packaging/parity acceptance without audio, serial ports or RF transmission."""
from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
import tempfile


def probe(connection, root):
    os.environ["GUARDIAN_STATE_DIR"] = root
    try:
        from guardian.config import StationConfig, config_dir
        from guardian.qt.runtime import ShellRuntime
        from guardian.modem import make_modem
        from .identity import identity
        contracts = []
        for name in ("vara_p2p", "ofdm_vhf", "ardop"):
            cfg = StationConfig(callsign="OK1LAB", payload_backend=name).enforce_production_policy()
            cfg.save(Path(root) / "config.json")
            runtime = ShellRuntime()
            ops = runtime.operations
            try:
                backend = ops.net.payload
                if hasattr(backend, "backends"):
                    backend = backend.backends[name]
                control = make_modem(cfg.active_modem())
                contracts.append({"requested": name,
                    "backend": f"{type(backend).__module__}.{type(backend).__name__}",
                    "control": f"{type(control).__module__}.{type(control).__name__}"})
            finally:
                runtime.close()
                runtime.workers.close(wait=True)
        connection.send({"fingerprint": identity()["fingerprint"], "state_dir": str(config_dir()),
                         "contracts": contracts})
    except BaseException as exc:
        connection.send({"error": repr(exc)})
    finally:
        connection.close()


def run():
    from .identity import identity
    expected = identity()["fingerprint"]
    context = multiprocessing.get_context("spawn")
    reports = []
    with tempfile.TemporaryDirectory(prefix="guardian-lab-selftest-") as root:
        children = []
        try:
            for label in ("a", "b"):
                parent, child = context.Pipe()
                path = str(Path(root) / label)
                process = context.Process(target=probe, args=(child, path))
                process.start()
                child.close()
                children.append((process, parent, path))
            for process, pipe, path in children:
                if not pipe.poll(90):
                    raise RuntimeError("LAB frozen station probe timed out")
                report = pipe.recv()
                if report.get("error"):
                    raise RuntimeError(report["error"])
                if report["fingerprint"] != expected or Path(report["state_dir"]) != Path(path):
                    raise RuntimeError("LAB station parity or isolation failed")
                process.join(5)
                if process.exitcode != 0:
                    raise RuntimeError("LAB station probe did not exit cleanly")
                reports.append(report)
        finally:
            for process, pipe, _ in children:
                if process.is_alive():
                    process.terminate()
                process.join(5)
                pipe.close()
    return {"passed": True, "rf_started": False, "fingerprint": expected, "stations": reports}
