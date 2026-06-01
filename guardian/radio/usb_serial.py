"""USB-to-serial adapter detection and driver guidance.

Rather than bundle/auto-install kernel drivers (risky: admin-only, can break a
working device, counterfeit-chip lockouts, redistribution licensing), Guardian
*identifies* the chipset behind each COM port by its USB VID:PID and points the
operator at the correct official driver.
"""

from __future__ import annotations

from dataclasses import dataclass

# VID -> (chipset, vendor, official driver page, note)
_VENDORS = {
    0x0403: ("FTDI FT232/FT-X", "FTDI",
             "https://ftdichip.com/drivers/vcp-drivers/",
             "Usually installed automatically by Windows Update."),
    0x10C4: ("Silicon Labs CP210x", "Silicon Labs",
             "https://www.silabs.com/developer-tools/usb-to-uart-bridge-vcp-drivers",
             "Common in many radios; often via Windows Update, else install CP210x VCP."),
    0x1A86: ("WCH CH340/CH341/CH9102", "WCH (Jiangsu Qinheng)",
             "https://www.wch-ic.com/downloads/CH341SER_EXE.html",
             "Cheap cables; frequently needs the manual CH340 driver."),
    0x067B: ("Prolific PL2303", "Prolific",
             "https://www.prolific.com.tw/US/ShowProduct.aspx?p_id=225&pcid=41",
             "Beware counterfeits — genuine driver disables fake chips. Match driver to chip revision."),
}


@dataclass
class SerialAdapter:
    device: str
    description: str
    vid: int | None
    pid: int | None
    chipset: str
    vendor: str
    driver_url: str
    note: str

    @property
    def vidpid(self) -> str:
        if self.vid is None or self.pid is None:
            return "—"
        return f"{self.vid:04X}:{self.pid:04X}"


def detect() -> list[SerialAdapter]:
    """Enumerate COM ports and identify the USB-serial chipset of each."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []

    out: list[SerialAdapter] = []
    for p in list_ports.comports():
        vid, pid = getattr(p, "vid", None), getattr(p, "pid", None)
        if vid in _VENDORS:
            chipset, vendor, url, note = _VENDORS[vid]
        else:
            chipset, vendor, url, note = ("Unknown / native USB", "—", "",
                                          "If this is your radio's built-in USB, use the maker's driver.")
        out.append(SerialAdapter(
            device=p.device, description=p.description or "",
            vid=vid, pid=pid, chipset=chipset, vendor=vendor,
            driver_url=url, note=note,
        ))
    return out
