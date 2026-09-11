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


class LogEventKind(StrEnum):
    """Structured meaning used by log consumers.

    The text of an event is localized and may change independently of the
    presentation.  Consumers should use this value for filtering and
    emphasis instead of looking for words in the rendered message.
    """

    GENERAL = "general"
    ALERT = "alert"
    TRANSFER_STARTED = "transfer_started"
    TRANSFER_COMPLETED = "transfer_completed"
    TRANSFER_FAILED = "transfer_failed"
    CONNECTION_LOST = "connection_lost"
    VARA_PTT = "vara_ptt"
    VARA_BUSY = "vara_busy"


@dataclass(frozen=True, slots=True)
class LogEvent:
    timestamp: _datetime.datetime
    level: LogLevel
    message: str
    source: str = "guardian"
    kind: LogEventKind = LogEventKind.GENERAL

    @property
    def display_text(self) -> str:
        return f"{self.timestamp:%H:%M:%S}  {self.message}"

    @property
    def is_activity_noise(self) -> bool:
        """Whether the event is retained for diagnostics but hidden on Home."""
        return (
            self.source == "vara"
            and self.level in (LogLevel.DEBUG, LogLevel.INFO)
            and self.kind in {
                LogEventKind.VARA_PTT,
                LogEventKind.VARA_BUSY,
            }
        )

    @property
    def is_activity_important(self) -> bool:
        """Whether the event deserves emphasis in an operator-facing log."""
        return self.level in (LogLevel.WARNING, LogLevel.ERROR) or self.kind in {
            LogEventKind.ALERT,
            LogEventKind.TRANSFER_STARTED,
            LogEventKind.TRANSFER_COMPLETED,
            LogEventKind.TRANSFER_FAILED,
            LogEventKind.CONNECTION_LOST,
        }


class EventBus:
    """Bounded history plus a queue of events not yet consumed by the UI."""

    def __init__(self, history_limit: int = 2_000) -> None:
        if history_limit < 1:
            raise ValueError("history_limit must be positive")
        self._pending: queue.SimpleQueue[LogEvent] = queue.SimpleQueue()
        self._history: deque[LogEvent] = deque(maxlen=history_limit)
        # Keep the Home projection independent of raw event churn.  In
        # particular, repeated VARA PTT/BUSY transitions must not push a
        # useful transfer or connection event out of the operator's history
        # while the raw stream remains available to diagnostics.
        self._activity_history: deque[LogEvent] = deque(maxlen=history_limit)
        self._lock = threading.Lock()

    def publish(
        self,
        message: str,
        level: LogLevel = LogLevel.INFO,
        *,
        source: str = "guardian",
        kind: LogEventKind | str = LogEventKind.GENERAL,
    ) -> LogEvent:
        level = LogLevel(level)
        kind = LogEventKind(kind)
        event = LogEvent(
            timestamp=_datetime.datetime.now().astimezone(),
            level=level,
            message=str(message),
            source=source,
            kind=kind,
        )
        with self._lock:
            self._history.append(event)
            if not event.is_activity_noise:
                self._activity_history.append(event)
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

    def activity_history(self) -> tuple[LogEvent, ...]:
        """Return the bounded, Home-visible event projection."""
        with self._lock:
            return tuple(self._activity_history)
