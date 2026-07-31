import pytest

from guardian.routing import (
    MAX_LOCATOR_CHARS,
    distance_bearing,
    from_locator,
    is_locator,
    locator_bounds,
    locator_distance_bearing,
    to_locator,
)


# Ground truth: squares these cities are known by. Coordinates are kept off
# square boundaries -- Vienna at 48.2082 sits 0.0001 deg below the JN88EE/EF
# edge, which says nothing about the encoder.
KNOWN = [
    ("Praha", 50.0755, 14.4378, "JO70FB"),
    ("London", 51.5074, -0.1278, "IO91WM"),
    ("Berlin", 52.5200, 13.4050, "JO62QM"),
    ("Tokyo", 35.6762, 139.6503, "PM95TQ"),
    ("Washington", 38.8951, -77.0364, "FM18"),
    ("Sydney", -33.8688, 151.2093, "QF56"),
]


@pytest.mark.parametrize("name,latitude,longitude,locator", KNOWN)
def test_known_squares_encode_the_way_operators_write_them(
    name, latitude, longitude, locator
) -> None:
    assert to_locator(latitude, longitude, len(locator)) == locator, name


def test_finer_precision_only_extends_the_coarser_square() -> None:
    # A 10-character locator must start with the 4-character one, or the two
    # would name different places at different zoom levels.
    latitude, longitude = 50.0755, 14.4378
    coarse = to_locator(latitude, longitude, 4)
    fine = to_locator(latitude, longitude, MAX_LOCATOR_CHARS)
    assert fine.startswith(coarse)
    assert len(fine) == MAX_LOCATOR_CHARS


def test_every_precision_brackets_the_point_it_came_from() -> None:
    latitude, longitude = 49.1951, 16.6068          # Brno
    for characters in (2, 4, 6, 8, 10):
        locator = to_locator(latitude, longitude, characters)
        south, west, north, east = locator_bounds(locator)
        assert south <= latitude <= north, locator
        assert west <= longitude <= east, locator


def test_the_finest_square_is_tens_of_metres_and_round_trips() -> None:
    latitude, longitude = 50.0755, 14.4378
    locator = to_locator(latitude, longitude)
    south, west, north, east = locator_bounds(locator)
    assert 15 < (north - south) * 111_320 < 25          # ~19 m tall
    # Back to a position: the centre of the square, so within half its size.
    kilometres, _bearing = distance_bearing(
        latitude, longitude, *from_locator(locator)
    )
    assert kilometres * 1000 < 30


def test_distance_and_bearing_match_the_map() -> None:
    # Praha -> Brno is about 185 km to the south-east.
    kilometres, bearing = distance_bearing(50.0755, 14.4378, 49.1951, 16.6068)
    assert 180 < kilometres < 190
    assert 110 < bearing < 135
    # And the way back points north-west.
    _back_km, back_bearing = distance_bearing(49.1951, 16.6068, 50.0755, 14.4378)
    assert 290 < back_bearing < 315


def test_a_station_at_its_own_position_is_zero_away() -> None:
    kilometres, _bearing = distance_bearing(50.0, 15.0, 50.0, 15.0)
    assert kilometres == 0.0


def test_locator_validation_refuses_what_is_not_a_square() -> None:
    for good in ("JN", "JN89", "JN89HE", "JN89HE12", "JN89HE12AB", "jn89he"):
        assert is_locator(good), good
    for bad in ("", "J", "JN8", "JN89H", "ZZ99", "JN89HE12ABCD", "JN89YZ", "12AB"):
        assert not is_locator(bad), bad
    with pytest.raises(ValueError):
        locator_bounds("nonsense")


def test_positions_beyond_the_world_still_produce_a_real_square() -> None:
    # Not hypothetical arithmetic hygiene: a longitude past the dateline runs
    # the index negative, and Python indexes a list from the *end*, so
    # to_locator would hand back a "Z" that no locator alphabet contains.
    assert is_locator(to_locator(0.0, -205.0))
    assert to_locator(0.0, -205.0) == to_locator(0.0, -180.0)
    assert to_locator(0.0, 205.0) == to_locator(0.0, 180.0)
    assert to_locator(-120.0, 0.0) == to_locator(-90.0, 0.0)
    assert to_locator(120.0, 0.0) == to_locator(90.0, 0.0)


def test_the_corners_of_the_world_stay_inside_the_grid() -> None:
    # Clamping matters: a pole or the dateline must not overflow into a
    # 19th field that no locator alphabet has a letter for.
    assert to_locator(-90.0, -180.0, 4) == "AA00"
    assert to_locator(90.0, 180.0, 4) == "RR99"
    assert is_locator(to_locator(90.0, 180.0))
    assert is_locator(to_locator(-90.0, -180.0))


def test_distance_between_locators_is_none_when_either_is_unknown() -> None:
    assert locator_distance_bearing("JO70FB", "") is None
    assert locator_distance_bearing("", "JN89HE") is None
    assert locator_distance_bearing("JO70FB", "rubbish") is None
    pair = locator_distance_bearing("JO70FB", "JN89HE")
    assert pair is not None and 180 < pair[0] < 190
