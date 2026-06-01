"""Control-burst modems (audio <-> bytes).

The control bursts that coordinate VARA transfers need their own modem, because
they travel *outside* any VARA data session (wake/announce/route-negotiation
happen before a connection exists). Profiles:

  * AFSK 1200 (Bell 202)  -> VARA FM channels (implemented)
  * MFSK-16 (FEC)         -> VARA HF / SSB    (planned)

Modems convert raw frame bytes (a ControlFrame.encode()) to float audio and
back. They are channel/PTT-agnostic; the AudioControlTransport wires a modem
to a sound device and the radio's PTT.
"""

from .afsk import AFSKModem
from .mfsk import MFSKModem

__all__ = ["AFSKModem", "MFSKModem", "make_modem"]


def make_modem(name: str, sample_rate: int = 48000):
    """Build a modem by control_modem name ('afsk1200' | 'mfsk16')."""
    name = (name or "afsk1200").lower()
    if name == "mfsk16":
        return MFSKModem(sample_rate=sample_rate)
    return AFSKModem(sample_rate=sample_rate)
