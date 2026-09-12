"""Radio control drivers.

Guardian talks to radios through a small uniform interface (RadioDriver) so
the rest of the app never cares about CAT dialects. The preferred backend is
Hamlib via rigctld (hundreds of radios, Windows + Linux), with a generic
serial RTS/DTR PTT fallback for dumb VOX radios.
"""

from .bands import AMATEUR_BANDS, band_for, same_band
from .base import RadioDriver, RadioState, NullRadio
from .hamlib import HamlibRadio
from .generic_vox import VoxRadio
from .guardian_handheld import GuardianK5Radio, GuardianK61Radio
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
        driver = VoxRadio(cfg.cat_port, ptt_line=cfg.ptt_line)
        driver.manual_frequency_hz = int(getattr(cfg, "manual_frequency_hz", 0) or 0)
        return driver
    if backend == "guardian_k5":
        return GuardianK5Radio(
            cfg.cat_port,
            baud=cfg.cat_baud,
            ptt_mode=getattr(cfg, "guardian_ptt_mode", "AIOC"),
        )
    if backend == "guardian_k61":
        return GuardianK61Radio(
            cfg.cat_port,
            baud=cfg.cat_baud,
            ptt_mode=getattr(cfg, "guardian_ptt_mode", "AIOC"),
        )
    return NullRadio()


__all__ = [
    "AMATEUR_BANDS",
    "band_for",
    "same_band",
    "RadioDriver",
    "RadioState",
    "NullRadio",
    "HamlibRadio",
    "VoxRadio",
    "GuardianK5Radio",
    "GuardianK61Radio",
    "Channel",
    "ChannelPlan",
    "ChannelScanner",
    "make_driver",
]
