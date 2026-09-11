"""One-shot device location values, kept separate from stored station grids.

Precise coordinates are deliberately ephemeral.  The map converts a successful
fix to Maidenhead and persists only that locator after the operator confirms it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class LocationSource(StrEnum):
    GPS = "gps"
    CELLULAR = "cellular"
    SATELLITE = "satellite"
    WIFI = "wifi"
    IP = "ip"
    DEFAULT = "default"
    OBFUSCATED = "obfuscated"
    UNKNOWN = "unknown"


class LocationFailure(StrEnum):
    DENIED = "denied"
    DISABLED = "disabled"
    NO_DATA = "no_data"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class LocationFix:
    latitude: float
    longitude: float
    # Some one-shot sources (notably an NMEA stream from an IC-705) report a
    # valid position but no horizontal accuracy.  ``None`` keeps that fact
    # explicit; zero would falsely promise metre-level precision.
    accuracy_m: float | None = None
    source: LocationSource = LocationSource.UNKNOWN
    timestamp: datetime | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.latitude) or not -90.0 <= self.latitude <= 90.0:
            raise ValueError("latitude outside -90..90")
        if not math.isfinite(self.longitude) or not -180.0 <= self.longitude <= 180.0:
            raise ValueError("longitude outside -180..180")
        if self.accuracy_m is not None:
            try:
                valid_accuracy = math.isfinite(self.accuracy_m) and self.accuracy_m >= 0
            except TypeError:
                valid_accuracy = False
            if not valid_accuracy:
                raise ValueError(
                    "accuracy must be a finite non-negative distance or None"
                )

    @property
    def is_approximate(self) -> bool:
        return self.accuracy_m is not None and self.accuracy_m > 1_000.0
