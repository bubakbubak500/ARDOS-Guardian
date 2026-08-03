"""Pure geometry used by the interactive station-map overlays."""

from __future__ import annotations

import math

from ..routing import locator_bounds, to_locator

EARTH_RADIUS_KM = 6_371.0088


def locator_cells(
    south: float,
    west: float,
    north: float,
    east: float,
    characters: int,
    *,
    max_cells: int = 400,
) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Return visible 4/6-character Maidenhead cells without unbounded work."""
    if characters not in (4, 6) or north <= south or east <= west:
        return []
    latitude_step = 1.0 if characters == 4 else 1.0 / 24.0
    longitude_step = 2.0 if characters == 4 else 1.0 / 12.0
    south = max(-90.0, south)
    north = min(90.0, north)
    west = max(-180.0, west)
    east = min(180.0, east)
    rows = int(math.ceil((north - south) / latitude_step)) + 2
    columns = int(math.ceil((east - west) / longitude_step)) + 2
    if rows * columns > max_cells:
        return []

    latitude = math.floor((south + 90.0) / latitude_step) * latitude_step - 90.0
    longitude_start = (
        math.floor((west + 180.0) / longitude_step) * longitude_step - 180.0
    )
    cells: list[tuple[str, tuple[float, float, float, float]]] = []
    while latitude < north and latitude < 90.0:
        longitude = longitude_start
        while longitude < east and longitude < 180.0:
            centre_latitude = latitude + latitude_step / 2.0
            centre_longitude = longitude + longitude_step / 2.0
            locator = to_locator(centre_latitude, centre_longitude, characters)
            cells.append((locator, locator_bounds(locator)))
            longitude += longitude_step
        latitude += latitude_step
    return cells


def destination_point(
    latitude: float,
    longitude: float,
    distance_km: float,
    bearing_degrees: float,
) -> tuple[float, float]:
    """Great-circle destination used to draw honest kilometre range rings."""
    angular = float(distance_km) / EARTH_RADIUS_KM
    bearing = math.radians(bearing_degrees)
    latitude_1 = math.radians(latitude)
    longitude_1 = math.radians(longitude)
    latitude_2 = math.asin(
        math.sin(latitude_1) * math.cos(angular)
        + math.cos(latitude_1) * math.sin(angular) * math.cos(bearing)
    )
    longitude_2 = longitude_1 + math.atan2(
        math.sin(bearing) * math.sin(angular) * math.cos(latitude_1),
        math.cos(angular) - math.sin(latitude_1) * math.sin(latitude_2),
    )
    return (
        math.degrees(latitude_2),
        (math.degrees(longitude_2) + 540.0) % 360.0 - 180.0,
    )
