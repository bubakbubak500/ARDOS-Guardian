"""ARDOS control-burst protocol package."""

from .alerts import (
    ALERTS,
    AlertKind,
    alert_kind,
    decode_alert,
    encode_alert,
    max_note_length,
)
from .frames import (
    MAGIC,
    MAX_CONTROL_FRAME_BYTES,
    VERSION,
    FrameType,
    Priority,
    Flags,
    ControlFrame,
    crc16,
    FrameError,
)

__all__ = [
    "ALERTS",
    "AlertKind",
    "alert_kind",
    "decode_alert",
    "encode_alert",
    "max_note_length",
    "MAGIC",
    "MAX_CONTROL_FRAME_BYTES",
    "VERSION",
    "FrameType",
    "Priority",
    "Flags",
    "ControlFrame",
    "crc16",
    "FrameError",
]
