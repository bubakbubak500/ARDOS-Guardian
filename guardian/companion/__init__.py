"""Offline phone companion for Guardian.

The companion deliberately has no cloud component.  A small HTTP server runs
inside Guardian and exposes a narrowly-scoped, paired API to phones on the
same Wi-Fi network.
"""

from .controller import CompanionController, CompanionError
from .hotspot import HotspotResult, WifiDirectHotspot

__all__ = [
    "CompanionController",
    "CompanionError",
    "HotspotResult",
    "WifiDirectHotspot",
]
