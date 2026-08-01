"""Radio control drivers.

Guardian talks to radios through a small uniform interface (RadioDriver) so
the rest of the app never cares about CAT dialects. The preferred backend is
Hamlib via rigctld (hundreds of radios, Windows + Linux), with a generic
serial RTS/DTR PTT fallback for dumb VOX radios.
"""

from .base import RadioDriver, RadioState, NullRadio
from .hamlib import HamlibRadio
from .generic_vox import VoxRadio
from .presets import DUMMY_MODEL
from .scanner import Channel, ChannelPlan, ChannelScanner


def make_driver(cfg) -> RadioDriver:
    """Build the right driver for a StationConfig."""
    backend = (cfg.radio_backend or "none").lower()
    if backend == "hamlib":
        driver = HamlibRadio(cfg.rigctld_host, cfg.rigctld_port)
        # Hamlib can normally ask the rig whether it is transmitting -- but
        # the dummy model just echoes whatever was set, and serial-line PTT
        # reads back the wire we asserted. Neither is the radio speaking, so
        # neither may count as confirmation in the PTT test.
        driver.reports_ptt = (
            int(getattr(cfg, "rig_model", 0) or 0) != DUMMY_MODEL
            and (getattr(cfg, "ptt_type", "RIG") or "RIG").upper() == "RIG"
        )
        driver.no_cat = int(getattr(cfg, "rig_model", 0) or 0) == DUMMY_MODEL
        driver.manual_frequency_hz = int(
            getattr(cfg, "manual_frequency_hz", 0) or 0
        )
        return driver
    if backend == "vox":
        return VoxRadio(cfg.cat_port, ptt_line=cfg.ptt_line)
    return NullRadio()


__all__ = [
    "RadioDriver",
    "RadioState",
    "NullRadio",
    "HamlibRadio",
    "VoxRadio",
    "Channel",
    "ChannelPlan",
    "ChannelScanner",
    "make_driver",
]
