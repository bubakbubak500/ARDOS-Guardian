"""Multi-channel scanning.

A channel plan is a list of (name, frequency, mode) entries. The scanner cycles
the radio through them via the RadioDriver, dwelling on each for a fixed time
and optionally holding when it detects activity (S-meter above a threshold) so
the operator doesn't skip past traffic. Tick-driven, like the orchestrator, so
it's deterministic and testable.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from ..config import config_dir

DEFAULT_PLAN_PATH = config_dir() / "channels.json"


@dataclass
class Channel:
    name: str
    freq_hz: int
    mode: str = "FM"


class ChannelPlan:
    def __init__(self, channels: list[Channel] | None = None):
        self.channels: list[Channel] = list(channels or [])

    def __len__(self):
        return len(self.channels)

    def add(self, ch: Channel) -> None:
        self.channels.append(ch)

    def remove(self, name: str) -> None:
        self.channels = [c for c in self.channels if c.name != name]

    @classmethod
    def load(cls, path: Path | str | None = None) -> "ChannelPlan":
        path = Path(path) if path else DEFAULT_PLAN_PATH
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        return cls([Channel(**c) for c in data.get("channels", []) if "freq_hz" in c])

    def save(self, path: Path | str | None = None) -> Path:
        path = Path(path) if path else DEFAULT_PLAN_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"channels": [asdict(c) for c in self.channels]}, indent=2),
                        encoding="utf-8")
        return path


class ChannelScanner:
    def __init__(self, radio, plan: ChannelPlan, dwell: float = 3.0,
                 signal_threshold: int | None = None,
                 on_change: Callable[[Channel], None] | None = None,
                 on_log: Callable[[str], None] | None = None):
        self.radio = radio
        self.plan = plan
        self.dwell = dwell
        self.signal_threshold = signal_threshold   # None disables activity hold
        self.on_change = on_change
        self.on_log = on_log or (lambda m: None)
        self.enabled = False
        self.holding = False
        self.index = -1
        self._last_change = 0.0

    @property
    def current(self) -> Channel | None:
        if 0 <= self.index < len(self.plan):
            return self.plan.channels[self.index]
        return None

    def start(self, now: float) -> None:
        if not len(self.plan):
            self.on_log("Scanner: channel plan is empty")
            return
        self.enabled = True
        self._goto(0, now)

    def stop(self) -> None:
        self.enabled = False
        self.holding = False

    def tick(self, now: float) -> None:
        if not self.enabled or not len(self.plan):
            return
        # Hold on this channel while there is activity.
        if self.signal_threshold is not None:
            try:
                st = self.radio.get_state()
                if st.signal is not None and st.signal >= self.signal_threshold:
                    if not self.holding:
                        self.on_log(f"Scanner: holding on {self.current.name} (signal {st.signal})")
                    self.holding = True
                    self._last_change = now
                    return
                self.holding = False
            except Exception:
                self.holding = False
        if now - self._last_change >= self.dwell:
            self._goto((self.index + 1) % len(self.plan), now)

    def _goto(self, index: int, now: float) -> None:
        self.index = index
        ch = self.current
        if ch is None:
            return
        try:
            self.radio.set_frequency(ch.freq_hz)
            self.radio.set_mode(ch.mode)
        except Exception as exc:  # NullRadio/VOX can't tune; that's fine
            self.on_log(f"Scanner: cannot tune {ch.name}: {exc}")
        self._last_change = now
        if self.on_change:
            self.on_change(ch)
