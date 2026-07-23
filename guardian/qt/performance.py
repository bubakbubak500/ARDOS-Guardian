"""Opt-in Qt event-loop responsiveness measurement."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from ..config import config_dir


class UiResponsivenessProbe(QObject):
    def __init__(
        self,
        parent=None,
        *,
        interval_ms: int = 50,
        stall_ms: int = 100,
        clock: Callable[[], float] = time.perf_counter,
        output_path: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.interval_ms = max(10, int(interval_ms))
        self.stall_ms = max(self.interval_ms, int(stall_ms))
        self.clock = clock
        self.output_path = output_path or config_dir() / "ui-performance.jsonl"
        self.expected = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(self.interval_ms)
        self.timer.timeout.connect(self._heartbeat)

    def start(self) -> None:
        if self.timer.isActive():
            return
        self.expected = self.clock() + self.interval_ms / 1000.0
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def _heartbeat(self) -> None:
        now = self.clock()
        drift_ms = max(0.0, (now - self.expected) * 1000.0)
        if drift_ms >= self.stall_ms:
            self._record(drift_ms)
        self.expected = now + self.interval_ms / 1000.0

    def _record(self, drift_ms: float) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "wall_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "stall_ms": round(drift_ms, 1),
            "heartbeat_ms": self.interval_ms,
        }
        with self.output_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, separators=(",", ":")) + "\n")


def start_probe_from_environment(parent=None) -> UiResponsivenessProbe | None:
    if os.environ.get("GUARDIAN_UI_PROFILE", "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return None
    probe = UiResponsivenessProbe(parent)
    probe.start()
    return probe
