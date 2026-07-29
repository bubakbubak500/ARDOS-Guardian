"""Net-wide alerts: a one-byte code plus an optional short note.

An alert is broadcast to everyone on the current frequency and flooded hop to
hop, so it has to fit in a single control burst. The trick is that the *code*
carries the meaning and the text is only ever a refinement: one byte on the air
expands to a full sentence in the reader's own language, and stations that
never agreed on wording still understand each other.

Budget, with the frame format left exactly as it is (see frames.py):

    14  fixed header + CRC
     6  source callsign ("OK7PS")
     1  empty next_hop length
     1  destination length prefix
     1  alert code
    --
    23  overhead, leaving 25 bytes of note inside MAX_CONTROL_FRAME_BYTES

The note is truncated to fit rather than rejected -- an alert that is slightly
abbreviated still beats one that was never sent. Airtime for a full burst is
about 1.7 s on MFSK and 1.2 s on AFSK.
"""

from __future__ import annotations

from dataclasses import dataclass

from .frames import MAX_CONTROL_FRAME_BYTES, Priority

# Fixed cost of everything that is not the note, for the longest callsign we
# expect. Measured against ControlFrame.encode() by test_alerts.
_FRAME_OVERHEAD = 23


@dataclass(frozen=True)
class AlertKind:
    code: int
    key: str                 # i18n key for the sentence shown to the operator
    priority: Priority
    hint_key: str            # i18n key describing what the note should carry


# The seed set, agreed with OK7PS/OK2IPW 2026-07-29. Codes are permanent once
# used on air: add new ones, never renumber. Gaps are deliberate so related
# alerts can be grouped later without disturbing what stations already know.
ALERTS: tuple[AlertKind, ...] = (
    AlertKind(0x01, "alert.mayday", Priority.EMERGENCY, "alert.hint_detail"),
    AlertKind(0x02, "alert.medical", Priority.EMERGENCY, "alert.hint_what_where"),
    AlertKind(0x03, "alert.evacuation", Priority.EMERGENCY, "alert.hint_area"),
    AlertKind(0x10, "alert.qrt", Priority.ROUTINE, "alert.hint_reason"),
    AlertKind(0x11, "alert.qsy", Priority.PRIORITY, "alert.hint_frequency"),
    AlertKind(0x12, "alert.qrv", Priority.ROUTINE, "alert.hint_none"),
    AlertKind(0x20, "alert.net_test", Priority.ROUTINE, "alert.hint_exercise"),
    AlertKind(0x30, "alert.power_outage", Priority.PRIORITY, "alert.hint_area"),
    AlertKind(0x31, "alert.battery_only", Priority.PRIORITY, "alert.hint_endurance"),
)

_BY_CODE = {kind.code: kind for kind in ALERTS}


def alert_kind(code: int) -> AlertKind | None:
    """The known alert for a code, or None for one this build does not know."""
    return _BY_CODE.get(int(code) & 0xFF)


def max_note_length(source: str) -> int:
    """Note bytes that still fit beside this station's callsign."""
    room = MAX_CONTROL_FRAME_BYTES - _FRAME_OVERHEAD - max(0, len(source) - 5)
    return max(0, room)


def encode_alert(code: int, note: str, source: str) -> str:
    """Pack code + note into the string the frame's destination field carries.

    The note is truncated to what fits, on a character boundary, after
    dropping anything that cannot travel as ASCII -- the on-air format has no
    room for an encoding negotiation.
    """
    ascii_note = note.strip().encode("ascii", errors="replace").decode("ascii")
    room = max_note_length(source)
    return chr(int(code) & 0xFF) + ascii_note[:room]


def decode_alert(payload: str) -> tuple[int, str]:
    """(code, note) from a received alert payload; code 0 if it was empty."""
    if not payload:
        return 0, ""
    return ord(payload[0]) & 0xFF, payload[1:]
