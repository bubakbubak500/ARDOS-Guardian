"""Measured half-duplex timing shared by real and simulated radio pipes.

The waveform duration is only one part of channel occupancy.  Keeping the
other parts explicit prevents laboratory goodput from silently omitting PTT
lead/tail, the anti-truncation guard, receive hangover, or decoder latency.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TxTiming:
    """One completed transmission, in seconds."""

    lead: float = 0.0
    waveform: float = 0.0
    guard: float = 0.0
    tail: float = 0.0
    keyed_total: float = 0.0
    wall_clock: float = 0.0

    @classmethod
    def deterministic(
        cls, waveform: float, *, lead: float = 0.0,
        guard: float = 0.0, tail: float = 0.0,
    ) -> "TxTiming":
        lead = max(0.0, float(lead))
        waveform = max(0.0, float(waveform))
        guard = max(0.0, float(guard))
        tail = max(0.0, float(tail))
        total = lead + waveform + guard + tail
        return cls(lead, waveform, guard, tail, total, total)


@dataclass(frozen=True)
class RxTiming:
    """Time spent obtaining and decoding one receive window."""

    trigger_wait: float = 0.0
    capture: float = 0.0
    hangover: float = 0.0
    decode: float = 0.0
    ready_wall_clock: float = 0.0

