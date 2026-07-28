"""Payload-transport backends.

The control handshake (Phase 2) negotiates *who/when/next-hop*. The payload
backend is the swappable piece that actually moves the message bytes once a hop
is agreed:

  * VaraP2PBackend — Guardian owns VARA, opens a peer-to-peer session and pumps
                     the payload itself (immediate, self-contained).

An operator-driven Winlink hand-off backend existed until 0.6.26. It was a
fallback while `vara_p2p` was unproven on air; once two-station transfers
worked it only offered a way to configure the station into a slower manual
workflow. `make_backend` maps any stored name onto VARA P2P so a config written
by an older Guardian still loads.
"""

from .base import PayloadBackend
from .vara_p2p import VaraP2PBackend

__all__ = ["PayloadBackend", "VaraP2PBackend", "make_backend"]


def make_backend(name: str = "vara_p2p", *, vara=None, on_log=None, on_qsy=None,
                 on_unqsy=None, on_acquire=None, on_release=None, **_legacy):
    """Build a payload backend by config name.

    `name` and any surplus keyword arguments (an operator `prompt` callback,
    for one) are accepted and ignored so an older config still loads.
    """
    return VaraP2PBackend(vara=vara, on_log=on_log, on_qsy=on_qsy, on_unqsy=on_unqsy,
                          on_acquire=on_acquire, on_release=on_release)
