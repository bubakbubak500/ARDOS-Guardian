"""How a measured figure is written down, and what stands in when there is none.

Two lines that every reporting surface needs and that must not be written twice:
a receiver that never got far enough to measure something reports `None`, and a
UI that renders that as "0" or "—" has invented a reading. Both dialogs and the
Modem test workspace share this so the same absent figure reads the same way
wherever it appears.
"""

from __future__ import annotations

import math

from PySide6.QtWidgets import QWidget

from ..i18n import dual, tr
from ..ofdm import LinkMetrics, best_mcs_for, mcs
from ..ofdm.config import OfdmConfigError


def repolish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a property the selector matches changed."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def measurement(value, template: str = "{value}") -> str:
    """A measurement, or the word "unavailable" -- never a zero or a dash.

    A dash reads as "nothing there" and a zero reads as a reading of zero. Both
    are lies about a figure the receiver never got far enough to measure.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return tr("record.unavailable")
    return template.format(value=value)


def mcs_verdict(metrics: LinkMetrics) -> str:
    """Turn a measured link SNR into the modes it will and will not carry.

    This answers the question an operator actually has when a burst is found and
    the payload does not decode: is the modem broken, or is the link simply not
    good enough for the modulation in use? Those two look identical in the log
    and want opposite responses, and the thresholds in `MCS_TABLE` -- measured,
    not guessed -- are what separates them.

    It reads `residual_snr_db` and never `snr_db`. The training-symbol figure
    cannot see distortion, and on the first two-radio tests it stood 8 dB above
    what the link was really delivering; advice built on it would have said
    64-QAM was comfortable on a path that never once decoded it.
    """
    snr = metrics.residual_snr_db
    if snr is None:
        return measurement(None)
    best = best_mcs_for(snr)
    if best is None:
        return dual(f"{snr:.1f} dB is below every mode in the table",
                    f"{snr:.1f} dB je pod všemi režimy v tabulce")
    used = None
    if metrics.mcs is not None:
        try:
            used = mcs(metrics.mcs)
        except OfdmConfigError:
            used = None
    if used is not None and used.index > best.index:
        return dual(
            f"MCS{best.index} and below. MCS{used.index} needs about "
            f"{used.min_snr_db:.0f} dB and this burst had {snr:.1f} dB, which is "
            f"why it did not decode.",
            f"MCS{best.index} a nižší. MCS{used.index} potřebuje asi "
            f"{used.min_snr_db:.0f} dB a toto vysílání mělo {snr:.1f} dB, proto "
            f"se nedekódovalo.",
        )
    if best.index >= 3:
        return dual(f"MCS{best.index} — every mode in the table",
                    f"MCS{best.index} — všechny režimy v tabulce")
    faster = mcs(best.index + 1)
    return dual(
        f"MCS{best.index} and below. MCS{faster.index} would need about "
        f"{faster.min_snr_db:.0f} dB.",
        f"MCS{best.index} a nižší. MCS{faster.index} by potřeboval asi "
        f"{faster.min_snr_db:.0f} dB.",
    )
