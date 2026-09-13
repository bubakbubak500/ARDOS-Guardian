"""Strict, direct-only WS v1 metadata codec."""
from dataclasses import dataclass
from enum import IntEnum
import re

from .frames import ControlFrame, FrameType, Priority, Flags


class Op(IntEnum):
    INVITE = 0
    ACCEPT = 1
    DECLINE = 2
    BUSY = 3
    READY = 4
    SHOT = 5
    MISS = 6
    HIT = 7
    SUNK = 8
    LOSS = 9
    ACK = 10
    RESIGN = 11
    CANCEL = 12


RESULTS = {Op.MISS, Op.HIT, Op.SUNK, Op.LOSS}
ACKABLE = RESULTS | {Op.ACCEPT, Op.DECLINE, Op.BUSY, Op.READY, Op.RESIGN, Op.CANCEL}


def ship_cells(start: int, packed: int) -> tuple[int, ...]:
    length, vertical = packed & 127, bool(packed & 128)
    if not 0 <= start < 100 or length not in (2, 3, 4, 5):
        raise ValueError("Invalid ship")
    if (start // 10 if vertical else start % 10) + length > 10:
        raise ValueError("Ship crosses board edge")
    return tuple(start + i * (10 if vertical else 1) for i in range(length))


@dataclass(frozen=True)
class Event:
    op: Op
    turn: int = 255
    a: int = 0
    b: int = 0

    def validate(self):
        if self.op in {Op.SHOT, *RESULTS}:
            if not 0 <= self.turn < 200:
                raise ValueError("Invalid turn")
        elif self.op is Op.ACK:
            if self.a not in ACKABLE or self.b != 0:
                raise ValueError("Invalid acknowledgement")
            if (self.a in RESULTS and not 0 <= self.turn < 200) or (self.a not in RESULTS and self.turn != 255):
                raise ValueError("Invalid acknowledgement turn")
        elif self.turn != 255:
            raise ValueError("Unexpected turn")
        if self.op in {Op.INVITE, Op.ACCEPT}:
            if (self.a, self.b) != (1, 0):
                raise ValueError("Unsupported rules")
        elif self.op in {Op.SHOT, Op.MISS, Op.HIT}:
            if not 0 <= self.a < 100 or self.b != 0:
                raise ValueError("Invalid coordinate")
        elif self.op in {Op.SUNK, Op.LOSS}:
            ship_cells(self.a, self.b)
        elif self.op is not Op.ACK and (self.a or self.b):
            raise ValueError("Reserved data")

    def frame(self, source, destination, session):
        self.validate()
        for call in (source, destination):
            if not re.fullmatch(r"[A-Z0-9/]{1,9}", call):
                raise ValueError("Callsign must have 1–9 ASCII characters")
        return ControlFrame(type=FrameType.WARSHIPS, source=source, destination=destination,
                            message_id=session, priority=Priority.ROUTINE, ttl=1, flags=Flags.NONE,
                            next_hop=f"W1{int(self.op):X}{self.turn:02X}{self.a:02X}{self.b:02X}")


def decode(frame):
    if frame.type is not FrameType.WARSHIPS or frame.ttl != 1 or frame.flags != Flags.NONE or frame.priority != Priority.ROUTINE:
        raise ValueError("Invalid WS envelope")
    if not re.fullmatch(r"W1[0-9A-C][0-9A-F]{6}", frame.next_hop):
        raise ValueError("Invalid WS token")
    token = frame.next_hop
    event = Event(Op(int(token[2], 16)), int(token[3:5], 16), int(token[5:7], 16), int(token[7:9], 16))
    event.frame(frame.source, frame.destination, frame.message_id)
    return event
