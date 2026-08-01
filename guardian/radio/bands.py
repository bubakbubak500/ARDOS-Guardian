"""Amateur band edges, used to bound automatic retuning.

This is not a licence check and never authorises a transmission -- the
operator's licence and the radio decide that. It exists so that automation
driven by *another station* has a limit: a peer proposing a payload channel
may move this radio across a channel inside the band it is already working
on, never onto a different band and never outside the amateur service.

IARU Region 1 allocations, which is where ARDOS is deployed. Edges are
deliberately the widest common allocation of each band: a rig tuned to a
segment its licence does not cover is the operator's business, a rig tuned
to 433 MHz because a peer asked for it is ours.
"""

from __future__ import annotations

# (lower_hz, upper_hz), inclusive, ordered by frequency.
AMATEUR_BANDS: tuple[tuple[int, int], ...] = (
    (135_700, 137_800),              # 2200 m
    (472_000, 479_000),              # 630 m
    (1_810_000, 2_000_000),          # 160 m
    (3_500_000, 3_800_000),          # 80 m
    (5_351_500, 5_366_500),          # 60 m
    (7_000_000, 7_200_000),          # 40 m
    (10_100_000, 10_150_000),        # 30 m
    (14_000_000, 14_350_000),        # 20 m
    (18_068_000, 18_168_000),        # 17 m
    (21_000_000, 21_450_000),        # 15 m
    (24_890_000, 24_990_000),        # 12 m
    (28_000_000, 29_700_000),        # 10 m
    (50_000_000, 52_000_000),        # 6 m
    (70_000_000, 70_500_000),        # 4 m
    (144_000_000, 146_000_000),      # 2 m
    (430_000_000, 440_000_000),      # 70 cm
    (1_240_000_000, 1_300_000_000),  # 23 cm
)


def band_for(frequency_hz: int | None) -> tuple[int, int] | None:
    """The amateur band holding this frequency, or None if it is outside one."""
    if not frequency_hz:
        return None
    value = int(frequency_hz)
    for band in AMATEUR_BANDS:
        if band[0] <= value <= band[1]:
            return band
    return None


def same_band(frequency_hz: int | None, reference_hz: int | None) -> bool:
    """True when both frequencies sit in the same amateur band."""
    band = band_for(frequency_hz)
    return band is not None and band == band_for(reference_hz)
