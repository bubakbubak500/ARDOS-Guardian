"""Run the production SC-FTN Quick Auto Tune on an isolated LAB radio pair."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import time

from guardian.lab.identity import identity
from guardian.lab.runner import Lab, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path, help="A validated two-radio SC-FTN LAB plan")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    case = deepcopy(plan["cases"][0])
    if case["modem"] != "SC-FTN":
        raise ValueError("Quick Auto Tune requires an SC-FTN case")
    for label, endpoint in plan["endpoints"].items():
        peer = plan["endpoints"]["b" if label == "a" else "a"]["config"]["callsign"]
        endpoint["config"]["calibration_auto_accept"] = True
        endpoint["config"]["calibration_allowlist"] = [peer]

    lab = Lab(args.output)
    lab.run_dir = args.output.resolve()
    lab.build = identity()
    lab.state["state"] = "preflight"
    start = time.monotonic()
    try:
        lab._start_pair(plan, case, 1)
        lab.children["a"]["pipe"].send({"op": "calibrate_start"})
        deadline = time.monotonic() + 1200
        last_print = 0.0
        while time.monotonic() < deadline:
            lab._pump()
            states = {
                label: child["latest"].get("snapshot", {}).get("station_lab", {})
                for label, child in lab.children.items()
            }
            if any(s.get("state") in ("failed", "cancelled") for s in states.values()):
                raise RuntimeError(f"Auto Tune failed: {states}")
            if all(s.get("state") == "complete" for s in states.values()):
                break
            now = time.monotonic()
            if now - last_print >= 10:
                print(json.dumps({
                    "elapsed_seconds": round(now - start, 1),
                    "stations": {
                        label: {key: status.get(key) for key in ("state", "progress", "total", "current", "error")}
                        for label, status in states.items()
                    },
                }, ensure_ascii=False), flush=True)
                last_print = now
            time.sleep(0.1)
        else:
            raise TimeoutError("Paired Quick Auto Tune timed out")
        report = {
            "elapsed_seconds": round(time.monotonic() - start, 2),
            "build": lab.build,
            "stations": states,
        }
        write_json(lab.run_dir / "quick-tune-results.json", report)
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return 0
    finally:
        if lab.children or lab.varas:
            lab._close_pair()


if __name__ == "__main__":
    raise SystemExit(main())
