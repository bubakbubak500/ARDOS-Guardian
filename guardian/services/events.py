"""Thread-safe structured application events.

Producers may publish from hardware/network worker threads.  A UI consumes the
queue on its own event loop, so no worker ever has to touch a Tk/Qt widget.
"""

from __future__ import annotations

import datetime as _datetime
import queue
import threading
from collections import deque
from dataclasses import dataclass
from enum import StrEnum


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class LogEvent:
    timestamp: _datetime.datetime
    level: LogLevel
    message: str
    source: str = "guardian"

    @property
    def display_text(self) -> str:
        return f"{self.timestamp:%H:%M:%S}  {self.message}"


class EventBus:
    """Bounded history plus a queue of events not yet consumed by the UI."""

    def __init__(self, history_limit: int = 2_000) -> None:
        if history_limit < 1:
            raise ValueError("history_limit must be positive")
        self._pending: queue.SimpleQueue[LogEvent] = queue.SimpleQueue()
        self._history: deque[LogEvent] = deque(maxlen=history_limit)
        self._lock = threading.Lock()

    def publish(
        self,
        message: str,
        level: LogLevel = LogLevel.INFO,
        *,
        source: str = "guardian",
    ) -> LogEvent:
        event = LogEvent(
            timestamp=_datetime.datetime.now().astimezone(),
            level=level,
            message=str(message),
            source=source,
        )
        with self._lock:
            self._history.append(event)
        self._pending.put(event)
        return event

    def drain(self, limit: int = 200) -> list[LogEvent]:
        events: list[LogEvent] = []
        for _ in range(max(0, limit)):
            try:
                events.append(self._pending.get_nowait())
            except queue.Empty:
                break
        return events

    def history(self) -> tuple[LogEvent, ...]:
        with self._lock:
            return tuple(self._history)
