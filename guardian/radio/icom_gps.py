"""One-shot NMEA GPS input from an Icom IC-705 USB(B) GPS Out port.

The IC-705 can expose its internal receiver as a normal serial NMEA stream
when USB(B) is configured for GPS Out.  This module deliberately contains no
radio control: it opens one serial port, reads a bounded number of lines for a
single recent fix, and closes it again.  Coordinates stay in the returned
in-memory :class:`~guardian.location.LocationFix` until the map turns them
into its existing Maidenhead locator preview.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import math
import re
from threading import Event
import time
from typing import Callable, Any

from ..location import LocationFix, LocationSource


GPS_BAUDRATE = 4_800
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_AGE_SECONDS = 120.0
MAX_NMEA_SENTENCE_BYTES = 512
READ_POLL_SECONDS = 0.20

_SATELLITE_FIX_MODES = frozenset("ADFR")
_GNS_MODE_VALUES = frozenset("ADFRNEMS")
_WINDOWS_DEVICE_PREFIXES = ("\\\\.\\", "\\.\\")


class IcomGpsPortConflict(ValueError):
    """The requested GPS port is also the configured CAT/PTT port."""


def serial_port_name(value: str | None) -> str:
    """Return a case-folded bare serial device name.

    The settings page labels ports as ``COM7 — device description``.  Keeping
    the normalisation here means both the map and the low-level reader apply
    the same guard before a port can be opened.
    """

    return _serial_port_value(value).casefold()


def _serial_port_value(value: str | None) -> str:
    """Return the bare port token used for serial opening and comparison."""

    text = str(value or "").strip()
    if not text:
        return ""
    token = text.split(None, 1)[0]
    for prefix in _WINDOWS_DEVICE_PREFIXES:
        if token.startswith(prefix):
            return token[len(prefix) :]
    return token


def same_serial_port(first: str | None, second: str | None) -> bool:
    """Whether two possibly-labelled serial port values name the same port."""

    left = serial_port_name(first)
    right = serial_port_name(second)
    return bool(left and right and left == right)


def nmea_checksum(body: str | bytes) -> str:
    """Return the two-uppercase-hex NMEA XOR checksum for *body*."""

    raw = body.encode("ascii") if isinstance(body, str) else bytes(body)
    value = 0
    for byte in raw:
        value ^= byte
    return f"{value:02X}"


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


_NMEA_TIME = re.compile(r"^(\d{2})(\d{2})(\d{2})(?:\.(\d+))?$")
_NMEA_DATE = re.compile(r"^(\d{2})(\d{2})(\d{2})$")


def _parse_time(value: str) -> tuple[int, int, float] | None:
    match = _NMEA_TIME.fullmatch(value.strip())
    if match is None:
        return None
    hour, minute, second = (
        int(match.group(index)) for index in range(1, 4)
    )
    fraction = float(f"0.{match.group(4)}") if match.group(4) else 0.0
    if hour > 23 or minute > 59 or second > 59:
        return None
    return hour, minute, second + fraction


def _parse_date(value: str) -> date | None:
    match = _NMEA_DATE.fullmatch(value.strip())
    if match is None:
        return None
    day, month, year = (int(match.group(index)) for index in range(1, 4))
    # NMEA dates use a two-digit year.  GPS receivers in current use report
    # 00..79 for 2000..2079 and 80..99 for 1980..1999.
    full_year = 2000 + year if year < 80 else 1900 + year
    try:
        return date(full_year, month, day)
    except ValueError:
        return None


def _timestamp(
    time_value: str,
    *,
    date_value: str | None,
    now: datetime,
) -> datetime | None:
    parsed_time = _parse_time(time_value)
    if parsed_time is None:
        return None
    hour, minute, seconds = parsed_time
    second = int(seconds)
    microsecond = int(round((seconds - second) * 1_000_000))
    if microsecond >= 1_000_000:
        second += 1
        microsecond -= 1_000_000
    if date_value is not None:
        parsed_date = _parse_date(date_value)
        if parsed_date is None:
            return None
        try:
            return datetime(
                parsed_date.year,
                parsed_date.month,
                parsed_date.day,
                hour,
                minute,
                second,
                microsecond,
                tzinfo=timezone.utc,
            )
        except ValueError:
            return None

    # GGA/GLL/GNS carry UTC time but no date.  Pick the nearest date around
    # the current UTC clock; this avoids accepting yesterday's fix just after
    # midnight while keeping a perfectly valid fix usable from those sentence
    # types.  No system clock is ever changed.
    today = now.date()
    candidates = []
    for offset in (-1, 0, 1):
        candidate_date = today + timedelta(days=offset)
        try:
            candidate = datetime(
                candidate_date.year,
                candidate_date.month,
                candidate_date.day,
                hour,
                minute,
                second,
                microsecond,
                tzinfo=timezone.utc,
            )
        except ValueError:
            continue
        candidates.append(candidate)
    return min(candidates, key=lambda item: abs(item - now)) if candidates else None


def _coordinate(value: str, hemisphere: str, *, latitude: bool) -> float | None:
    value = value.strip()
    hemisphere = hemisphere.strip().upper()
    if not value or hemisphere not in ({"N", "S"} if latitude else {"E", "W"}):
        return None
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(raw) or raw < 0:
        return None
    degrees = int(raw // 100)
    minutes = raw - degrees * 100
    if minutes < 0 or minutes >= 60:
        return None
    limit = 90 if latitude else 180
    if degrees > limit or (degrees == limit and minutes != 0):
        return None
    result = degrees + minutes / 60
    if hemisphere in {"S", "W"}:
        result = -result
    return result


def _fresh(timestamp: datetime, now: datetime, max_age_seconds: float) -> bool:
    age = abs((now - timestamp).total_seconds())
    return math.isfinite(age) and age <= max_age_seconds


def _optional_fix_mode(
    fields: list[str], index: int, *, multi_constellation: bool = False
) -> bool:
    """Validate a mode field when the sentence variant supplies one."""

    if len(fields) <= index or not fields[index].strip():
        return True
    mode = fields[index].strip().upper()
    if multi_constellation:
        return bool(mode) and all(char in _GNS_MODE_VALUES for char in mode)
    return len(mode) == 1 and mode in _SATELLITE_FIX_MODES


def _fix(
    fields: list[str],
    *,
    kind: str,
    now: datetime,
    max_age_seconds: float,
) -> LocationFix | None:
    """Reduce one supported NMEA sentence to an ephemeral location fix."""

    latitude: float | None
    longitude: float | None
    timestamp: datetime | None

    if kind == "GGA":
        # $--GGA,time,lat,N,lon,E,quality,satellites,...
        if len(fields) < 7 or not fields[6].strip():
            return None
        try:
            quality = int(fields[6].strip())
        except ValueError:
            return None
        if quality not in {1, 2, 3, 4, 5}:
            return None
        latitude = _coordinate(fields[2], fields[3], latitude=True)
        longitude = _coordinate(fields[4], fields[5], latitude=False)
        timestamp = _timestamp(fields[1], date_value=None, now=now)
    elif kind == "RMC":
        # $--RMC,time,status,lat,N,lon,E,speed,course,date,...
        if (
            len(fields) < 10
            or fields[2].strip().upper() != "A"
            or not _optional_fix_mode(fields, 12)
        ):
            return None
        latitude = _coordinate(fields[3], fields[4], latitude=True)
        longitude = _coordinate(fields[5], fields[6], latitude=False)
        timestamp = _timestamp(fields[1], date_value=fields[9], now=now)
    elif kind == "GLL":
        # $--GLL,lat,N,lon,E,time,status,...
        if (
            len(fields) < 7
            or fields[6].strip().upper() != "A"
            or not _optional_fix_mode(fields, 7)
        ):
            return None
        latitude = _coordinate(fields[1], fields[2], latitude=True)
        longitude = _coordinate(fields[3], fields[4], latitude=False)
        timestamp = _timestamp(fields[5], date_value=None, now=now)
    elif kind == "GNS":
        # $--GNS,time,lat,N,lon,E,mode,...
        if len(fields) < 7 or not fields[6].strip():
            return None
        modes = fields[6].strip().upper()
        if (
            not _optional_fix_mode(fields, 6, multi_constellation=True)
            or not any(mode in _SATELLITE_FIX_MODES for mode in modes)
        ):
            return None
        latitude = _coordinate(fields[2], fields[3], latitude=True)
        longitude = _coordinate(fields[4], fields[5], latitude=False)
        timestamp = _timestamp(fields[1], date_value=None, now=now)
    else:
        return None

    if latitude is None or longitude is None or timestamp is None:
        return None
    if not _fresh(timestamp, now, max_age_seconds):
        return None
    # NMEA has no horizontal accuracy field that can be trusted across these
    # sentence types.  Preserve that uncertainty explicitly instead of
    # manufacturing a precision value for the map preview.
    return LocationFix(
        latitude=latitude,
        longitude=longitude,
        accuracy_m=None,
        source=LocationSource.GPS,
        timestamp=timestamp,
    )


def parse_nmea_sentence(
    sentence: str | bytes,
    *,
    now: datetime | None = None,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
) -> LocationFix | None:
    """Parse one checksummed NMEA sentence into a recent GPS fix.

    Unsupported, malformed, unchecked, stale, and no-fix sentences all return
    ``None``.  Returning no data instead of guessing is intentional: a GPS
    port can be present while the receiver has no usable lock.
    """

    try:
        raw = (
            sentence
            if isinstance(sentence, bytes)
            else str(sentence).encode("ascii")
        )
    except (UnicodeDecodeError, UnicodeEncodeError, AttributeError):
        return None
    if len(raw) > MAX_NMEA_SENTENCE_BYTES:
        return None
    try:
        text = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    if not text.startswith("$"):
        return None
    star = text.find("*")
    if star <= 1 or len(text) != star + 3:
        return None
    body = text[1:star]
    supplied = text[star + 1 :].upper()
    if not re.fullmatch(r"[0-9A-F]{2}", supplied):
        return None
    try:
        expected = nmea_checksum(body)
    except (UnicodeEncodeError, TypeError):
        return None
    if supplied != expected:
        return None
    fields = body.split(",")
    if not fields or len(fields[0]) < 3:
        return None
    kind = fields[0][-3:].upper()
    if kind not in {"GGA", "RMC", "GLL", "GNS"}:
        return None
    try:
        age = float(max_age_seconds)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(age) or age < 0:
        return None
    return _fix(
        fields,
        kind=kind,
        now=_as_utc(now),
        max_age_seconds=age,
    )


def _default_serial_factory():
    try:
        import serial  # type: ignore
    except ImportError as exc:  # pragma: no cover - dependency is packaged
        raise RuntimeError("pyserial is not installed") from exc
    return serial.Serial


def _open_serial(
    port: str,
    *,
    serial_factory: Callable[..., Any] | None,
    timeout: float,
):
    """Open a serial port with both control lines inactive first.

    Constructing ``Serial(port)`` can assert RTS/DTR before application code
    can clear them.  Opening with ``port=None`` lets us set both lines low,
    then assign and open the actual port.  This reader never uses either line
    for radio control.
    """

    factory = serial_factory or _default_serial_factory()
    serial_port = factory(port=None, baudrate=GPS_BAUDRATE, timeout=timeout)
    try:
        try:
            serial_port.rts = False
        except (AttributeError, OSError):
            pass
        try:
            serial_port.dtr = False
        except (AttributeError, OSError):
            pass
        serial_port.port = port
        opener = getattr(serial_port, "open", None)
        if callable(opener):
            opener()
        return serial_port
    except BaseException:
        try:
            serial_port.close()
        except BaseException:
            pass
        raise


def read_icom_gps(
    port: str,
    *,
    stop_event: Event | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    serial_factory: Callable[..., Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
    now: datetime | None = None,
    forbidden_port: str | None = None,
) -> LocationFix | None:
    """Read one recent valid IC-705 NMEA fix, returning ``None`` otherwise.

    The caller owns cancellation through ``stop_event``.  Each serial read is
    limited to :data:`MAX_NMEA_SENTENCE_BYTES` and at most 200 ms, while the
    whole operation is bounded by ``timeout_seconds``.  The serial object is
    always closed before this function returns.
    """

    if not serial_port_name(port):
        raise ValueError("GPS serial port is empty")
    if forbidden_port and same_serial_port(port, forbidden_port):
        raise IcomGpsPortConflict("GPS port is also the configured CAT/PTT port")
    try:
        timeout = float(timeout_seconds)
        max_age = float(max_age_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("GPS read limits must be numeric") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("GPS read timeout must be positive")
    if not math.isfinite(max_age) or max_age < 0:
        raise ValueError("GPS maximum age must be non-negative")
    stop = stop_event or Event()
    if stop.is_set():
        return None

    serial_port = _open_serial(
        _serial_port_value(port),
        serial_factory=serial_factory,
        timeout=min(READ_POLL_SECONDS, timeout),
    )
    deadline = clock() + timeout
    pending = bytearray()
    discarding = False
    try:
        while not stop.is_set():
            remaining = deadline - clock()
            if remaining <= 0:
                break
            try:
                serial_port.timeout = min(READ_POLL_SECONDS, remaining)
            except (AttributeError, OSError):
                pass
            reader = getattr(serial_port, "read_until", None)
            if not callable(reader):
                raise RuntimeError("serial driver lacks bounded read_until")
            chunk = reader(b"\n", MAX_NMEA_SENTENCE_BYTES)
            if not chunk:
                continue
            if isinstance(chunk, str):
                chunk = chunk.encode("ascii", errors="ignore")
            elif isinstance(chunk, (bytearray, memoryview)):
                chunk = bytes(chunk)
            elif not isinstance(chunk, bytes):
                continue

            # ``read_until`` can return a timeout fragment without the newline.
            # Keep those fragments bounded and do not let a truncated or
            # overlong sentence become a candidate for the parser.
            remainder = bytes(chunk)
            while remainder and not stop.is_set():
                newline = remainder.find(b"\n")
                complete = newline >= 0
                if complete:
                    fragment = remainder[: newline + 1]
                    remainder = remainder[newline + 1 :]
                else:
                    fragment = remainder
                    remainder = b""

                if discarding:
                    if complete:
                        discarding = False
                    continue

                pending.extend(fragment)
                if len(pending) > MAX_NMEA_SENTENCE_BYTES:
                    pending.clear()
                    if not complete:
                        discarding = True
                    continue
                if not complete:
                    continue

                line = bytes(pending)
                pending.clear()
                fix = parse_nmea_sentence(
                    line,
                    now=_as_utc(now),
                    max_age_seconds=max_age,
                )
                if fix is not None:
                    return fix
        return None
    finally:
        try:
            serial_port.rts = False
        except (AttributeError, OSError):
            pass
        try:
            serial_port.dtr = False
        except (AttributeError, OSError):
            pass
        try:
            serial_port.close()
        except BaseException:
            pass


__all__ = [
    "DEFAULT_MAX_AGE_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "GPS_BAUDRATE",
    "IcomGpsPortConflict",
    "MAX_NMEA_SENTENCE_BYTES",
    "nmea_checksum",
    "parse_nmea_sentence",
    "read_icom_gps",
    "same_serial_port",
    "serial_port_name",
]
