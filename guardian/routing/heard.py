"""Heard-stations registry.

Every control frame Guardian receives is evidence that a station is reachable
*right now*. We track who we've heard, when, how often, and (when the modem
provides it) at what signal level. Smart routing uses this to pick a next hop
without a hand-configured route.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HeardStation:
    callsign: str
    last_heard: float           # monotonic timestamp
    count: int = 0
    last_snr: float | None = None
    # Where our own radio was tuned when this station was last heard. It is our
    # frequency, not a measurement of theirs -- on a simplex net they are the
    # same thing, and after a QSY it says which channel the contact was on.
    last_freq_hz: int | None = None
    # Maidenhead locator the station put in its beacon, "" until one arrives.
    # Only beacons carry a position, so it survives every other frame type.
    grid: str = ""
    last_frame: str = ""
    # Destinations this station has offered to reach (from ROUTE_OFFER), so we
    # can prefer it as a relay toward those.
    reaches: set = field(default_factory=set)

    def age(self, now: float) -> float:
        return now - self.last_heard


class HeardStations:
    def __init__(self, max_age: float = 1800.0):
        self.max_age = max_age          # seconds a station stays "heard"
        self._stations: dict[str, HeardStation] = {}

    def record(self, callsign: str, now: float, *, snr: float | None = None,
               freq_hz: int | None = None, grid: str = "", frame: str = "",
               reaches: str | None = None) -> None:
        call = callsign.strip().upper()
        if not call:
            return
        st = self._stations.get(call)
        if st is None:
            st = HeardStation(callsign=call, last_heard=now)
            self._stations[call] = st
        st.last_heard = now
        st.count += 1
        if snr is not None:
            st.last_snr = snr
        if freq_hz:
            st.last_freq_hz = int(freq_hz)
        if grid:
            st.grid = grid.strip().upper()
        if frame:
            st.last_frame = frame
        if reaches:
            st.reaches.add(reaches.strip().upper())

    def is_heard(self, callsign: str, now: float, within: float | None = None) -> bool:
        st = self._stations.get(callsign.strip().upper())
        if st is None:
            return False
        limit = self.max_age if within is None else within
        return st.age(now) <= limit

    def get(self, callsign: str) -> HeardStation | None:
        return self._stations.get(callsign.strip().upper())

    def can_reach(self, destination: str, now: float) -> list[HeardStation]:
        """Heard stations that have offered to reach `destination`, freshest first."""
        dest = destination.strip().upper()
        out = [s for s in self._stations.values()
               if dest in s.reaches and s.age(now) <= self.max_age]
        return sorted(out, key=lambda s: s.last_heard, reverse=True)

    def active(self, now: float) -> list[HeardStation]:
        """All currently-fresh stations, most-recently-heard first."""
        out = [s for s in self._stations.values() if s.age(now) <= self.max_age]
        return sorted(out, key=lambda s: s.last_heard, reverse=True)

    def prune(self, now: float) -> None:
        self._stations = {c: s for c, s in self._stations.items()
                          if s.age(now) <= self.max_age}
