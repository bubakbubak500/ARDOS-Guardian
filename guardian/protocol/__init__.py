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
    MAX_PTT_DELAY_MS,
    PTT_DELAY_STEP_MS,
    VERSION,
    FrameType,
    Priority,
    Flags,
    ControlFrame,
    crc16,
    decode_ptt_delay,
    encode_ptt_delay,
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
    "MAX_PTT_DELAY_MS",
    "PTT_DELAY_STEP_MS",
    "VERSION",
    "FrameType",
    "Priority",
    "Flags",
    "ControlFrame",
    "crc16",
    "decode_ptt_delay",
    "encode_ptt_delay",
    "FrameError",
]
