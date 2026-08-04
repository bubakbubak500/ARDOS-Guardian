"""ARDOS control-burst frames.

The control burst carries *metadata only* — never the message body. The body
travels over VARA FM once both stations have negotiated a session. Keeping the
burst short makes it fast and robust over a noisy FM channel.

Binary layout (big-endian), all callsigns ASCII upper-case:

    offset  size  field
    0       3     magic        b"ARD"
    3       1     version      uint8   (currently 1)
    4       1     type         uint8   (FrameType)
    5       1     priority     uint8   (Priority 0..3)
    6       1     ttl          uint8   (hop budget, decremented each relay)
    7       1     flags        uint8   (Flags bitfield)
    8       4     message_id   uint32
    12      1     src_len      uint8
    13      ..    source       ascii
    ..      1     dst_len      uint8
    ..      ..    destination  ascii   (final destination / group)
    ..      1     nh_len       uint8
    ..      ..    next_hop     ascii   (suggested next station; "" allowed)

An ALERT has no addressee, so it reuses `destination` for its payload: one
alert-code byte plus an optional short free-text note (see protocol/alerts.py).
Nothing about the wire format changes -- it is still three length-prefixed
fields -- so an older station simply sees an unknown frame type and ignores it.
    end-2   2     crc16        uint16  (CRC-16/CCITT-FALSE over all prior bytes)

A frame is typically 30-45 bytes — small enough to send as a quick burst.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum, IntFlag

MAGIC = b"ARD"
VERSION = 1

# Fixed header: magic(3) ver(1) type(1) prio(1) ttl(1) flags(1) msgid(4)
_HEADER = struct.Struct(">3sBBBBBI")
_CRC = struct.Struct(">H")


class FrameError(Exception):
    """Raised when a buffer cannot be parsed as a valid control frame."""


# Upper bound on an encoded control frame. The largest Guardian emits is 43
# bytes (HAVE_MSG with three 9-character callsigns); the headroom covers a
# field being widened. The audio transport sizes its RX window from this and
# the session layer sizes its timeouts, so a frame that exceeded it would stop
# being received at all -- test_frames asserts every type stays under.
MAX_CONTROL_FRAME_BYTES = 48

class FrameType(IntEnum):
    HAVE_MSG = 1      # "I have a message available"
    ACK_HAVE = 2      # next station heard it and is ready
    BUSY = 3          # station cannot receive now
    ROUTE_QUERY = 4   # ask who can reach destination
    ROUTE_OFFER = 5   # station offers itself as next hop
    START_VARA = 6    # begin VARA session
    RECEIVED = 7      # payload received OK (crc ok)
    DELIVERED = 8     # final station confirms end-to-end delivery
    CANCEL = 9        # cancel/retract message
    BEACON = 10       # presence beacon ("I am here") — lets neighbours hear us
    ALERT = 11        # net-wide broadcast, flooded hop to hop (see alerts.py)
    # Opt-in payload-channel negotiation. Older releases reject these unknown
    # frame types and therefore never leave their current single channel.
    WORKING_OFFER = 12  # propose a separately configured payload channel
    WORKING_ACK = 13    # peer independently configured the same channel
    # Multi-hop discovery is intentionally distinct from legacy one-hop
    # ROUTE_QUERY/OFFER. Older releases reject these unknown types instead of
    # accidentally flooding a query they do not understand.
    MULTIHOP_RREQ = 14  # bounded broadcast seeking a path to destination
    MULTIHOP_RREP = 15  # directed answer following reverse breadcrumbs
    # Experimental, bounded advertisement of one directly-heard neighbour.
    # Older releases reject the unknown type, creating a safe discovery gap.
    LINK_ADVERT = 16

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").title()


class Priority(IntEnum):
    ROUTINE = 0
    PRIORITY = 1
    URGENT = 2
    EMERGENCY = 3


class Flags(IntFlag):
    NONE = 0
    ENCRYPTED = 0x01
    COMPRESSED = 0x02
    ACK_REQUIRED = 0x04


# Bits 3-5 of the flags byte: a slow-keying request for the VARA FM payload
# phase, in 100 ms steps (0 = none, up to 700 ms). A cheap handheld unkeyed
# the instant VARA says PTT OFF cuts the tail off its own burst, so both
# stations agree to keep PTT asserted this long after each burst before
# dropping the carrier. The value rides inside the existing flags byte of
# HAVE_MSG/ACK_HAVE: the wire format is untouched, and a build that predates
# this keeps unknown flag bits intact (IntFlag KEEP) and simply echoes them
# back.
PTT_DELAY_STEP_MS = 100
_PTT_DELAY_SHIFT = 3
_PTT_DELAY_BITS = 0x07
MAX_PTT_DELAY_MS = _PTT_DELAY_BITS * PTT_DELAY_STEP_MS


def encode_ptt_delay(flags: Flags | int, delay_ms: int) -> Flags:
    """Overwrite the slow-keying field of `flags` with `delay_ms` (rounded
    down to the step, capped). Overwriting matters: a relay must replace the
    previous hop's negotiated value with its own, never forward it."""
    steps = max(0, min(int(delay_ms) // PTT_DELAY_STEP_MS, _PTT_DELAY_BITS))
    cleared = int(flags) & ~(_PTT_DELAY_BITS << _PTT_DELAY_SHIFT)
    return Flags(cleared | (steps << _PTT_DELAY_SHIFT))


def decode_ptt_delay(flags: Flags | int) -> int:
    """The slow-keying request carried in `flags`, in milliseconds."""
    return ((int(flags) >> _PTT_DELAY_SHIFT) & _PTT_DELAY_BITS) * PTT_DELAY_STEP_MS


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, xorout 0."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def _enc_call(call: str) -> bytes:
    raw = call.strip().upper().encode("ascii", errors="replace")
    if len(raw) > 255:
        raise FrameError(f"callsign too long: {call!r}")
    return bytes([len(raw)]) + raw


@dataclass
class ControlFrame:
    """A single ARDOS control burst."""

    type: FrameType
    source: str
    destination: str = ""
    next_hop: str = ""
    message_id: int = 0
    priority: Priority = Priority.ROUTINE
    ttl: int = 5
    flags: Flags = Flags.NONE

    def encode(self) -> bytes:
        """Serialise to the on-air binary form (with trailing CRC)."""
        body = bytearray()
        body += _HEADER.pack(
            MAGIC,
            VERSION,
            int(self.type),
            int(self.priority) & 0xFF,
            int(self.ttl) & 0xFF,
            int(self.flags) & 0xFF,
            int(self.message_id) & 0xFFFFFFFF,
        )
        body += _enc_call(self.source)
        body += _enc_call(self.destination)
        body += _enc_call(self.next_hop)
        body += _CRC.pack(crc16(bytes(body)))
        return bytes(body)

    @classmethod
    def decode(cls, buf: bytes) -> "ControlFrame":
        """Parse a buffer; raises FrameError on any inconsistency."""
        if len(buf) < _HEADER.size + 3 + _CRC.size:
            raise FrameError("buffer too short")

        magic, ver, ftype, prio, ttl, flags, msgid = _HEADER.unpack_from(buf, 0)
        if magic != MAGIC:
            raise FrameError(f"bad magic {magic!r}")
        if ver != VERSION:
            raise FrameError(f"unsupported version {ver}")

        # Verify CRC before trusting the variable section.
        crc_given = _CRC.unpack_from(buf, len(buf) - _CRC.size)[0]
        crc_calc = crc16(buf[: len(buf) - _CRC.size])
        if crc_given != crc_calc:
            raise FrameError(f"crc mismatch: got {crc_given:#06x} want {crc_calc:#06x}")

        pos = _HEADER.size
        calls = []
        for _ in range(3):
            if pos >= len(buf):
                raise FrameError("truncated callsign section")
            n = buf[pos]
            pos += 1
            if pos + n > len(buf) - _CRC.size:
                raise FrameError("callsign length overruns frame")
            calls.append(buf[pos : pos + n].decode("ascii", errors="replace"))
            pos += n

        try:
            ftype_e = FrameType(ftype)
        except ValueError as exc:
            raise FrameError(f"unknown frame type {ftype}") from exc

        return cls(
            type=ftype_e,
            source=calls[0],
            destination=calls[1],
            next_hop=calls[2],
            message_id=msgid,
            priority=Priority(prio) if prio in Priority._value2member_map_ else Priority.ROUTINE,
            ttl=ttl,
            flags=Flags(flags),
        )

    def summary(self) -> str:
        """Short human-readable one-liner for logs/UI."""
        parts = [self.type.label, f"src={self.source}"]
        if self.destination:
            parts.append(f"dst={self.destination}")
        if self.next_hop:
            parts.append(f"next={self.next_hop}")
        parts.append(f"id={self.message_id}")
        if self.priority != Priority.ROUTINE:
            parts.append(self.priority.name)
        return " ".join(parts)
