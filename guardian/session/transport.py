"""Control-frame transport abstraction.

The orchestrator sends/receives *control frames* over some channel. In the real
station that channel is the AFSK/MFSK burst modem over radio audio (Phase 3).
Until that exists — and for dry-run testing — we provide a LoopbackBus that
behaves like a shared RF channel: every endpoint hears every frame, just like
stations sharing a simplex frequency.

The bus is **queue-based and pumped explicitly** (no threads): `send()` only
enqueues, and `pump()` delivers. This keeps delivery deterministic and avoids
re-entrancy when a frame handler sends another frame.
"""

from __future__ import annotations

from collections import deque
from typing import Callable

from ..protocol import ControlFrame


class ControlTransport:
    """Interface: send a frame, and receive frames via the on_frame callback."""

    on_frame: Callable[[ControlFrame], None] | None = None

    def send(self, frame: ControlFrame) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class NullTransport(ControlTransport):
    """A control channel that does nothing — the app's default when the live
    audio channel is OFF. send() is a no-op and no frames are ever delivered,
    so the orchestrator exists but the radio stays idle until the operator
    starts the audio control channel."""

    def __init__(self) -> None:
        self.on_frame = None

    def send(self, frame: ControlFrame) -> None:
        return None


class _BusEndpoint(ControlTransport):
    def __init__(self, bus: "LoopbackBus", name: str):
        self._bus = bus
        self.name = name
        self.on_frame = None

    def send(self, frame: ControlFrame) -> None:
        self._bus._enqueue(self, frame)


class LoopbackBus:
    """A simulated shared simplex channel connecting several endpoints.

    Frames a station sends are heard by *all other* endpoints (not itself),
    exactly like a radio channel. An optional monitor callback sees every frame
    that crosses the channel — handy for an on-air monitor view.
    """

    def __init__(self, monitor: Callable[[str, ControlFrame], None] | None = None):
        self._endpoints: list[_BusEndpoint] = []
        self._queue: deque[tuple[_BusEndpoint, ControlFrame]] = deque()
        self.monitor = monitor

    def endpoint(self, name: str) -> _BusEndpoint:
        ep = _BusEndpoint(self, name)
        self._endpoints.append(ep)
        return ep

    def _enqueue(self, sender: _BusEndpoint, frame: ControlFrame) -> None:
        self._queue.append((sender, frame))

    def pump(self, max_frames: int = 1000) -> int:
        """Deliver all queued frames. Returns how many were delivered."""
        delivered = 0
        while self._queue and delivered < max_frames:
            sender, frame = self._queue.popleft()
            if self.monitor is not None:
                self.monitor(sender.name, frame)
            for ep in self._endpoints:
                if ep is sender:
                    continue
                if ep.on_frame is not None:
                    ep.on_frame(frame)
            delivered += 1
        return delivered

    @property
    def idle(self) -> bool:
        return not self._queue
