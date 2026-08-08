"""How a rung of the OFDM profile ladder is named where an operator picks one.

Two places offer the ladder -- Station settings, which decides what the station
transmits, and the Modem test workspace, which decides what gets measured. They
have to name the rungs identically: an operator who measures WIDE_10K and then
selects something described differently has no way to know they are the same
waveform.
"""

from __future__ import annotations

from ..i18n import dual


def profile_rung_label(entry) -> str:
    """One line naming a rung and what choosing it commits to.

    Honest by construction. No rung has been measured on a real radio, so every
    entry says so rather than letting the default read as proven; and the rung
    that samples faster than the rest says that too, because a sound card that
    will not open at 96 kHz is a failure to anticipate at the moment of choosing
    rather than to debug afterwards.
    """
    khz = entry.occupied_bandwidth / 1000.0
    if entry.sample_rate != 48000:
        return dual(
            f"{entry.name} — occupies {khz:.1f} kHz · needs a "
            f"{entry.sample_rate / 1000:.0f} kHz sound card · untried on air",
            f"{entry.name} — zabírá {khz:.1f} kHz · vyžaduje zvukovou kartu na "
            f"{entry.sample_rate / 1000:.0f} kHz · na pásmu nezkoušený",
        )
    return dual(
        f"{entry.name} — occupies {khz:.1f} kHz · untried on air",
        f"{entry.name} — zabírá {khz:.1f} kHz · na pásmu nezkoušený",
    )
