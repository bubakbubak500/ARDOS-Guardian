"""ARDOS control-burst protocol package."""

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
