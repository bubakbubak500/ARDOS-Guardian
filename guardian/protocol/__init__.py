"""ARDOS control-burst protocol package."""

from .frames import (
    MAGIC,
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
    "VERSION",
    "FrameType",
    "Priority",
    "Flags",
    "ControlFrame",
    "crc16",
    "FrameError",
]
