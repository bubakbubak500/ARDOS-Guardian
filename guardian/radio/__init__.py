"""Radio control drivers.

Guardian talks to radios through a small uniform interface (RadioDriver) so
the rest of the app never cares about CAT dialects. The preferred backend is
Hamlib via rigctld (hundreds of radios, Windows + Linux), with a generic
serial RTS/DTR PTT fallback for dumb VOX radios.
"""

from .base import RadioDriver, RadioState, NullRadio
from .hamlib import HamlibRadio
from .generic_vox import VoxRadio


def make_driver(cfg) -> RadioDriver:
    """Build the right driver for a StationConfig."""
    backend = (cfg.radio_backend or "none").lower()
    if backend == "hamlib":
        return HamlibRadio(cfg.rigctld_host, cfg.rigctld_port)
    if backend == "vox":
        return VoxRadio(cfg.cat_port, ptt_line=cfg.ptt_line)
    return NullRadio()


__all__ = [
    "RadioDriver",
    "RadioState",
    "NullRadio",
    "HamlibRadio",
    "VoxRadio",
    "make_driver",
]
