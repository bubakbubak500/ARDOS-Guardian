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

from ..i18n import tr


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
