"""Independent Guardian G2 payload waveform families.

The verified OFDM implementation lives in :mod:`guardian.ofdm`.  This package
contains opt-in experiments which share its framing/FEC/ARQ but not its PHY.
"""

from .config import (FAMILY_PROFILE_LADDERS, PROFILES, PROFILE_LADDER,
                     WaveformProfile, family_profile_names, profile_for,
                     profile_or_default, profile_names)

__all__ = [
    "FAMILY_PROFILE_LADDERS", "PROFILES", "PROFILE_LADDER", "WaveformProfile",
    "family_profile_names", "profile_for", "profile_or_default", "profile_names",
]
