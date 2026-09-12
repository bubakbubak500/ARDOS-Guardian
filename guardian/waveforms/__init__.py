"""Production Guardian SC-FTN waveform profiles and PHY helpers.

The shared wire framing, coding, adaptation and ARQ contracts retain their
historical ``guardian.ofdm`` import path.  This package owns the only production
audio waveform implementation.
"""

from .config import (FAMILY_PROFILE_LADDERS, PROFILES, PROFILE_LADDER,
                     WaveformProfile, family_profile_names, profile_for,
                     profile_or_default, profile_names)

__all__ = [
    "FAMILY_PROFILE_LADDERS", "PROFILES", "PROFILE_LADDER", "WaveformProfile",
    "family_profile_names", "profile_for", "profile_or_default", "profile_names",
]
