"""Independent Guardian G2 payload waveform families.

The verified OFDM implementation lives in :mod:`guardian.ofdm`.  This package
contains opt-in experiments which share its framing/FEC/ARQ but not its PHY.
"""

from .config import (PROFILES, PROFILE_LADDER, WaveformProfile,
                     profile_or_default, profile_names)

__all__ = [
    "PROFILES", "PROFILE_LADDER", "WaveformProfile",
    "profile_or_default", "profile_names",
]
