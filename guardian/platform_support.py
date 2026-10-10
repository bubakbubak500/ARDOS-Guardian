"""Capabilities of the separately distributed Linux edition."""

import sys

LINUX = sys.platform.startswith("linux")
VARA_AVAILABLE = not LINUX
DEFAULT_PAYLOAD_BACKEND = "ofdm_vhf" if LINUX else "vara_p2p"
