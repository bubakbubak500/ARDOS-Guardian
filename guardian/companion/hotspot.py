"""Best-effort offline Wi-Fi Direct access point for classic Windows builds."""

from __future__ import annotations

import os
import secrets
import string
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HotspotResult:
    ok: bool
    message: str
    ssid: str = ""
    password: str = ""
    address: str = "192.168.137.1"


def suggested_credentials(callsign: str) -> tuple[str, str]:
    safe = "".join(ch for ch in str(callsign).upper() if ch.isalnum())[:12] or "RADIO"
    alphabet = string.ascii_letters + string.digits
    password = "".join(secrets.choice(alphabet) for _ in range(14))
    return f"Guardian-{safe}", password


class WifiDirectHotspot:
    """Start a Wi-Fi Direct Legacy AP when the Windows adapter supports it.

    Unlike Mobile Hotspot tethering, the legacy Wi-Fi Direct publisher is
    available to unpackaged classic desktop apps and does not require an
    internet-facing connection profile.  The publisher object is retained for
    the whole session; dropping it tears the AP down.
    """

    def __init__(self) -> None:
        self.publisher = None
        self.ssid = ""
        self.password = ""

    @property
    def running(self) -> bool:
        return self.publisher is not None

    def start(self, callsign: str, *, ssid: str = "", password: str = "") -> HotspotResult:
        if os.name != "nt":
            return HotspotResult(False, "Wi-Fi Direct hosting is available on Windows only.")
        if self.running:
            return HotspotResult(True, "Wi-Fi Direct is already running.", self.ssid, self.password)
        if not ssid or not password:
            generated_ssid, generated_password = suggested_credentials(callsign)
            ssid = ssid or generated_ssid
            password = password or generated_password
        if len(password) < 8:
            return HotspotResult(False, "The Wi-Fi password must contain at least 8 characters.")
        try:
            from winrt.windows.devices.wifidirect import WiFiDirectAdvertisementPublisher

            publisher = WiFiDirectAdvertisementPublisher()
            legacy = publisher.advertisement.legacy_settings
            legacy.is_enabled = True
            legacy.ssid = ssid
            legacy.passphrase.password = password
            publisher.start()
        except Exception as exc:  # driver/capability failures vary by Windows build
            return HotspotResult(
                False,
                "Windows could not start Wi-Fi Direct. Open Mobile Hotspot in Settings "
                f"or use a shared Wi-Fi network. ({exc})",
            )
        self.publisher = publisher
        self.ssid = ssid
        self.password = password
        return HotspotResult(
            True,
            "Offline Wi-Fi Direct network started.",
            ssid,
            password,
        )

    def stop(self) -> HotspotResult:
        publisher = self.publisher
        if publisher is None:
            return HotspotResult(True, "Wi-Fi Direct is already stopped.")
        try:
            publisher.stop()
        except Exception as exc:
            return HotspotResult(False, f"Windows could not stop Wi-Fi Direct. ({exc})")
        finally:
            self.publisher = None
        return HotspotResult(True, "Wi-Fi Direct stopped.")
