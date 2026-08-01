"""Deterministic scheduling for multi-channel scanning.

This module deliberately does not touch a radio.  ``Operations`` owns the
hardware lock and worker pool; the scanner only decides *when* the next channel
is due.  Keeping CAT calls out of this state machine makes it deterministic and
prevents a slow rigctld reply from blocking Qt's event loop.
"""

from __future__ import annotations

from dataclasses import dataclass


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


class ChannelScanner:
    def __init__(self, plan: ChannelPlan, dwell: float = 3.0,
                 signal_threshold: int | None = None):
        self.plan = plan
        self.dwell = max(0.1, float(dwell))
        self.signal_threshold = signal_threshold   # None disables activity hold
        self.enabled = False
        self.holding = False
        self.index = -1
        self._last_change = 0.0

    @property
    def current(self) -> Channel | None:
        if 0 <= self.index < len(self.plan):
            return self.plan.channels[self.index]
        return None

    @property
    def last_change(self) -> float:
        return self._last_change

    def start(self, now: float) -> Channel | None:
        if not len(self.plan):
            return None
        self.enabled = True
        self.holding = False
        self.index = 0
        self._last_change = now
        return self.current

    def stop(self) -> None:
        self.enabled = False
        self.holding = False

    def tick(
        self,
        now: float,
        *,
        signal: int | None = None,
        activity: bool = False,
    ) -> Channel | None:
        """Return the channel that should be tuned now, if one is due."""
        if not self.enabled or not len(self.plan):
            return None
        above_threshold = (
            self.signal_threshold is not None
            and signal is not None
            and signal >= self.signal_threshold
        )
        if activity or above_threshold:
            self.holding = True
            self._last_change = now
            return None
        self.holding = False
        if now - self._last_change >= self.dwell:
            self.index = (self.index + 1) % len(self.plan)
            self._last_change = now
            return self.current
        return None
