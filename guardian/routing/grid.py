"""Maidenhead locators, and the geometry a map and a heard-list need.

A position has to survive the control burst, and the burst's address fields
are ASCII-only and upper-cased on the way out (see protocol/frames.py), so
binary latitude/longitude is not an option -- it comes back as "?". A
Maidenhead locator is text, upper-case, amateur-standard, and small enough
that even the finest form fits beside the longest callsign:

    JN89        100 x 200 km      JN89HW12    460 x 930 m
    JN89HW      4.6 x 9.3 km      JN89HW12AB   ~50 x 90 m

The alternating 18/10/24/10/24 divisions are the locator system itself: a
field is 20 deg of longitude, a square 2 deg, a subsquare 5 minutes, and so
on down. Writing them as a table keeps every precision on one code path
rather than four hand-rolled special cases.
"""

from __future__ import annotations

import math

# Divisions per pair of characters, longitude and latitude alike. The first
# pair spans the globe (360/18 = 20 deg of longitude, 180/18 = 10 deg of
# latitude); each later pair subdivides the one before it.
_DIVISIONS = (18, 10, 24, 10, 24)
MAX_LOCATOR_CHARS = 2 * len(_DIVISIONS)      # 10
_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

EARTH_RADIUS_KM = 6371.0088                  # IUGG mean radius


def is_locator(value: str) -> bool:
    """True for a well-formed locator of 2, 4, 6, 8 or 10 characters."""
    text = (value or "").strip().upper()
    if len(text) < 2 or len(text) > MAX_LOCATOR_CHARS or len(text) % 2:
        return False
    for index, pair in enumerate(zip(text[::2], text[1::2])):
        divisions = _DIVISIONS[index]
        for char in pair:
            if divisions == 10:
                if not char.isdigit():
                    return False
            elif char not in _LETTERS[:divisions]:
                return False
    return True


def to_locator(latitude: float, longitude: float, characters: int = MAX_LOCATOR_CHARS) -> str:
    """Encode a WGS-84 position, to `characters` (even, 2..10) precision."""
    characters = max(2, min(int(characters), MAX_LOCATOR_CHARS))
    characters -= characters % 2
    # Work in "distance from the south-west corner of the world" so every
    # subdivision is a plain modulo, and clamp the poles/dateline inside the
    # last square rather than overflowing into a 19th field.
    lon = min(max(float(longitude), -180.0), 180.0) + 180.0
    lat = min(max(float(latitude), -90.0), 90.0) + 90.0
    lon_span, lat_span = 360.0, 180.0
    out: list[str] = []
    for divisions in _DIVISIONS[: characters // 2]:
        lon_span /= divisions
        lat_span /= divisions
        lon_index = min(int(lon / lon_span), divisions - 1)
        lat_index = min(int(lat / lat_span), divisions - 1)
        lon -= lon_index * lon_span
        lat -= lat_index * lat_span
        if divisions == 10:
            out.append(str(lon_index))
            out.append(str(lat_index))
        else:
            out.append(_LETTERS[lon_index])
            out.append(_LETTERS[lat_index])
    return "".join(out)


def locator_bounds(locator: str) -> tuple[float, float, float, float]:
    """(south, west, north, east) of the square a locator names, in degrees."""
    text = (locator or "").strip().upper()
    if not is_locator(text):
        raise ValueError(f"not a Maidenhead locator: {locator!r}")
    west, south = -180.0, -90.0
    lon_span, lat_span = 360.0, 180.0
    for index, (lon_char, lat_char) in enumerate(zip(text[::2], text[1::2])):
        divisions = _DIVISIONS[index]
        lon_span /= divisions
        lat_span /= divisions
        if divisions == 10:
            lon_index, lat_index = int(lon_char), int(lat_char)
        else:
            lon_index, lat_index = _LETTERS.index(lon_char), _LETTERS.index(lat_char)
        west += lon_index * lon_span
        south += lat_index * lat_span
    return south, west, south + lat_span, west + lon_span


def from_locator(locator: str) -> tuple[float, float]:
    """(latitude, longitude) of the centre of the square."""
    south, west, north, east = locator_bounds(locator)
    return (south + north) / 2.0, (west + east) / 2.0


def distance_bearing(
    latitude: float,
    longitude: float,
    other_latitude: float,
    other_longitude: float,
) -> tuple[float, float]:
    """Great-circle (kilometres, initial bearing in degrees true) to another point."""
    lat1, lon1 = math.radians(latitude), math.radians(longitude)
    lat2, lon2 = math.radians(other_latitude), math.radians(other_longitude)
    delta_lon = lon2 - lon1
    # Haversine: numerically well behaved for the short hops a VHF net makes,
    # where the law of cosines loses precision.
    haversine = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    distance = 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(haversine)))
    bearing = math.degrees(
        math.atan2(
            math.sin(delta_lon) * math.cos(lat2),
            math.cos(lat1) * math.sin(lat2)
            - math.sin(lat1) * math.cos(lat2) * math.cos(delta_lon),
        )
    )
    return distance, bearing % 360.0


def locator_distance_bearing(locator: str, other: str) -> tuple[float, float] | None:
    """Distance and bearing between two locator squares, or None if either is bad."""
    try:
        latitude, longitude = from_locator(locator)
        other_latitude, other_longitude = from_locator(other)
    except ValueError:
        return None
    return distance_bearing(latitude, longitude, other_latitude, other_longitude)
