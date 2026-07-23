"""Guardian — ARDOS control & routing layer in front of VARA FM.

A mini routing/control layer that announces, negotiates and orchestrates
VARA FM message transfers over standard amateur radios (via Hamlib/rigctld
or simple VOX/serial PTT).
"""

from ._version import __version__

__app_name__ = "Guardian"
