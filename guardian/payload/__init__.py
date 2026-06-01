"""Payload-transport backends.

The control handshake (Phase 2) negotiates *who/when/next-hop*. The payload
backend is the swappable piece that actually moves the message bytes once a hop
is agreed. Two are provided:

  * VaraP2PBackend     — Guardian owns VARA, opens a peer-to-peer session and
                         pumps the payload itself (immediate, self-contained).
  * WinlinkManualBackend — Guardian coordinates, then the operator runs their
                         own Winlink session for the transfer and confirms.

Both satisfy the same PayloadBackend interface, so the orchestrator is unchanged.
"""

from .base import PayloadBackend
from .vara_p2p import VaraP2PBackend
from .winlink_manual import WinlinkManualBackend

__all__ = ["PayloadBackend", "VaraP2PBackend", "WinlinkManualBackend", "make_backend"]


def make_backend(name: str, *, vara=None, prompt=None, on_log=None, on_qsy=None, on_unqsy=None):
    """Build a payload backend by config name."""
    name = (name or "vara_p2p").lower()
    if name == "winlink_manual":
        return WinlinkManualBackend(prompt=prompt, on_log=on_log)
    return VaraP2PBackend(vara=vara, on_log=on_log, on_qsy=on_qsy, on_unqsy=on_unqsy)
