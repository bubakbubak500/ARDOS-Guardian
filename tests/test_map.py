import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QPointF, QTimer
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from guardian.qt.map_tiles import (
    CUZK_ZTM,
    TILE_PIXELS,
    TileCache,
    TileSource,
    tile_for,
    tiles_for_bounds,
)
from guardian.qt.map_tools import destination_point, locator_cells
from guardian.qt.map_window import MAX_TILES_PER_DRAW, MapCanvas
from guardian.routing import distance_bearing, from_locator


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _canvas(width: int = 760, height: int = 480) -> MapCanvas:
    _application()
    canvas = MapCanvas()
    canvas.resize(width, height)
    return canvas


def _visible(canvas: MapCanvas, latitude: float, longitude: float) -> bool:
    point = canvas.to_screen(latitude, longitude)
    return 0 <= point.x() <= canvas.width() and 0 <= point.y() <= canvas.height()


# Spreads that broke the old fit, plus the shapes of window they broke in.
SPREADS = {
    "north-south": ["JO70FB", "JN88EE", "JO62QM"],       # Praha, Vienna, Berlin
    "east-west": ["JO70FB", "JN89HE", "JO80AB", "JN99AA"],
    "one station": ["JO70FB28MC"],
    "two neighbours": ["JO70FB28MC", "JO70FB29MC"],
    "half of Europe": ["IO91WM", "JO62QM", "JN36", "KO85", "IM76"],
}


@pytest.mark.parametrize("name", sorted(SPREADS))
@pytest.mark.parametrize("size", [(760, 480), (500, 700), (1200, 300)])
def test_show_all_really_shows_them_all(name, size) -> None:
    # The old fit took the larger of the latitude and longitude spans as a
    # width, forgetting that a window is wider than it is tall and that
    # latitude degrees are stretched by the projection. On a Praha-centred
    # view it asked for 4.33 degrees of latitude and showed 3.49: Vienna and
    # Berlin fell off the screen with no hint that they existed.
    canvas = _canvas(*size)
    points = [from_locator(grid) for grid in SPREADS[name]]

    canvas.fit(points)

    for grid, (latitude, longitude) in zip(SPREADS[name], points):
        assert _visible(canvas, latitude, longitude), f"{grid} off screen in {size}"


def test_a_lone_station_is_not_framed_at_arms_length() -> None:
    # Fitting a single point has no span to work from, and the old floor gave
    # a view 57 km wide -- correct, and useless for finding anyone.
    canvas = _canvas()
    canvas.fit([from_locator("JO70FB28MC")])
    assert canvas.degrees_across >= 2.0


def test_an_empty_net_gets_the_world_rather_than_a_division_by_zero() -> None:
    canvas = _canvas()
    canvas.fit([])
    assert canvas.degrees_across > 300


def test_the_projection_is_reversible() -> None:
    # Screen and map have to agree in both directions, or picking a position
    # would store somewhere other than where the operator clicked.
    canvas = _canvas()
    canvas.look_at(49.8, 15.5, 6.0)
    for latitude, longitude in [(50.0755, 14.4378), (49.1951, 16.6068), (0.0, 0.0)]:
        point = canvas.to_screen(latitude, longitude)
        back_lat, back_lon = canvas.to_position(point)
        assert abs(back_lat - latitude) < 1e-6
        assert abs(back_lon - longitude) < 1e-6


def test_stations_land_inside_the_tile_that_covers_them() -> None:
    # The registration check: the background is served in Web Mercator, and
    # plotting stations in any other projection would draw them off the towns
    # they are standing in.
    canvas = _canvas(800, 560)
    canvas.set_source(CUZK_ZTM)
    canvas.look_at(49.8, 15.4, 4.0)
    zoom = canvas.tile_zoom()
    scale = canvas.pixels_per_world()
    size = scale / 2 ** zoom
    origin_x = canvas.world_x(canvas.center[1]) * scale - canvas.width() / 2
    origin_y = canvas.world_y(canvas.center[0]) * scale - canvas.height() / 2

    for latitude, longitude in [(50.0755, 14.4378), (49.1951, 16.6068)]:
        x, y = tile_for(latitude, longitude, zoom)
        corner = QPointF(x * size - origin_x, y * size - origin_y)
        station = canvas.to_screen(latitude, longitude)
        assert 0 <= station.x() - corner.x() <= size
        assert 0 <= station.y() - corner.y() <= size
    canvas.set_source(None)


def test_tile_zoom_follows_the_view_and_stops_at_the_source_ceiling() -> None:
    canvas = _canvas()
    canvas.set_source(CUZK_ZTM)
    canvas.look_at(49.8, 15.5, 180.0)
    wide = canvas.tile_zoom()
    canvas.look_at(49.8, 15.5, 1.0)
    close = canvas.tile_zoom()
    assert 0 <= wide < close <= CUZK_ZTM.max_zoom

    canvas.look_at(49.8, 15.5, 0.02)
    assert canvas.tile_zoom() == CUZK_ZTM.max_zoom

    # And the level chosen renders tiles near their own resolution. Off by
    # one is still geometrically correct -- the draw scales to fit -- but it
    # means either a blurry background or four times the requests.
    for degrees in (0.5, 2.0, 8.0, 40.0):
        canvas.look_at(49.8, 15.5, degrees)
        size = canvas.pixels_per_world() / 2 ** canvas.tile_zoom()
        assert 0.7 <= size / TILE_PIXELS <= 1.5, (degrees, size)
    canvas.set_source(None)


def test_no_source_means_no_requests_at_all() -> None:
    canvas = _canvas()
    canvas.set_source(None)
    assert canvas.tile_zoom() == 0
    assert canvas._tile(5, 1, 1) is None
    assert canvas._pending == set()


def test_a_view_of_the_whole_world_does_not_ask_for_thousands_of_tiles() -> None:
    # Zoomed all the way out the grid is enormous; drawing it tile by tile
    # would hammer a service that is given away for free.
    canvas = _canvas()
    canvas.set_source(CUZK_ZTM)
    canvas.look_at(20.0, 0.0, 340.0)
    zoom = canvas.tile_zoom()
    assert (2 ** zoom) ** 2 <= MAX_TILES_PER_DRAW or zoom <= 4
    canvas.set_source(None)


def test_tiles_survive_on_disk_so_the_same_ground_works_offline(tmp_path) -> None:
    cache = TileCache(CUZK_ZTM, directory=tmp_path)
    try:
        assert cache.get(8, 138, 86) is None
        assert not cache.contains(8, 138, 86)
        cache.put(8, 138, 86, b"\xff\xd8\xff\xe0 pretend jpeg")
        assert cache.contains(8, 138, 86)
        assert cache.get(8, 138, 86) == b"\xff\xd8\xff\xe0 pretend jpeg"
        assert cache.count() == 1
        cache.put(8, 138, 86, b"newer")            # replaced, not duplicated
        assert cache.count() == 1
        cache.put(8, 138, 87, b"")                 # an empty reply is not a tile
        assert cache.count() == 1
    finally:
        cache.close()

    # A second run finds what the first one downloaded.
    again = TileCache(CUZK_ZTM, directory=tmp_path)
    try:
        assert again.get(8, 138, 86) == b"newer"
        assert again.megabytes() >= 0.0
    finally:
        again.close()

    limited = TileCache(CUZK_ZTM, directory=tmp_path / "limited", max_megabytes=0)
    try:
        assert not limited.put(8, 138, 86, b"tile beyond the cap")
        assert not limited.contains(8, 138, 86)
    finally:
        limited.close()


def test_tile_numbering_matches_the_slippy_map_convention() -> None:
    # Praha at zoom 10 is tile 553/346 -- the number the service answers to.
    assert tile_for(50.0755, 14.4378, 10) == (553, 346)
    # At zoom 1 the world is four tiles; the equator is the boundary, so a
    # point on it belongs to the southern row.
    assert tile_for(45.0, -180.0, 1) == (0, 0)
    assert tile_for(0.0, -180.0, 1) == (0, 1)
    assert tile_for(-85.0, 179.9, 1) == (1, 1)
    # The edges of the world must land on a tile that exists: longitude
    # exactly 180 indexes one past the last column, and a pole runs the
    # Mercator formula off to infinity.
    count = 2 ** 4
    for latitude, longitude in [(0.0, 180.0), (89.9, 180.0), (-89.9, -180.0)]:
        x, y = tile_for(latitude, longitude, 4)
        assert 0 <= x < count and 0 <= y < count, (latitude, longitude, x, y)
    assert tile_for(89.9, 0.0, 4) == tile_for(85.05, 0.0, 4)


def test_the_tile_url_is_the_service_we_verified() -> None:
    assert CUZK_ZTM.tile_url(10, 553, 346).endswith("/tile/10/346/553")
    assert "cuzk" in CUZK_ZTM.url
    assert "ČÚZK" in CUZK_ZTM.attribution


def test_station_markers_have_a_thick_contrast_ring_and_are_clickable() -> None:
    canvas = _canvas()
    canvas.set_source(None)
    canvas.own_grid = "JO70FB28MC"
    canvas.stations = [("OK2IPW", "JO70FB29MC", 0.0)]
    canvas.look_at(*from_locator("JO70FB28MC"), 0.08)

    pixmap = QPixmap(canvas.size())
    canvas.render(pixmap)
    image = pixmap.toImage()
    own = canvas.to_screen(*from_locator(canvas.own_grid))
    colours = [
        image.pixelColor(round(own.x()) + x, round(own.y()) + y)
        for x in range(-9, 10)
        for y in range(-9, 10)
    ]
    assert any(colour.name() == "#1683ff" for colour in colours)
    assert any(
        max(colour.red(), colour.green(), colour.blue()) < 12
        for colour in colours
    ), "dark under-stroke keeps marker visible on tiles"

    peer = canvas.to_screen(*from_locator("JO70FB29MC"))
    assert canvas.station_at(peer) == "OK2IPW"
    assert canvas.station_at(peer + QPointF(40, 40)) == ""


def test_locator_overlay_cells_are_real_maidenhead_bounds_and_bounded() -> None:
    cells = locator_cells(49.9, 14.0, 50.2, 15.0, 4)
    locators = {locator for locator, _bounds in cells}
    assert "JO70" in locators
    jo70 = dict(cells)["JO70"]
    assert jo70[0] <= 50.0 <= jo70[2]
    assert jo70[1] <= 14.5 <= jo70[3]

    fine = locator_cells(50.0, 14.3, 50.1, 14.5, 6)
    assert fine and all(len(locator) == 6 for locator, _bounds in fine)
    assert locator_cells(-80, -170, 80, 170, 6, max_cells=100) == []


def test_range_ring_destination_is_geodesically_honest() -> None:
    end = destination_point(50.0755, 14.4378, 100, 90)
    kilometres, bearing = distance_bearing(50.0755, 14.4378, *end)
    assert kilometres == pytest.approx(100, abs=0.02)
    assert bearing == pytest.approx(90, abs=0.1)


def test_offline_plan_is_visible_bounded_and_clipped_to_cuzk() -> None:
    keys = tiles_for_bounds(
        49.9, 14.2, 50.2, 14.8, 9, 11, source=CUZK_ZTM
    )
    assert keys
    assert {zoom for zoom, _x, _y in keys} == {9, 10, 11}
    assert len(keys) == len(set(keys))
    assert tiles_for_bounds(40, -10, 41, -9, 8, 10, source=CUZK_ZTM) == []
    with pytest.raises(ValueError, match="exceeds"):
        tiles_for_bounds(-80, -170, 80, 170, 0, 10, limit=10)


def test_offline_prefetch_runs_through_qt_and_populates_the_cache(tmp_path) -> None:
    _application()
    tile_path = tmp_path / "8-138-86.png"
    tile = QPixmap(32, 32)
    tile.fill(QColor("#2468a0"))
    assert tile.save(str(tile_path), "PNG")
    source = TileSource(
        key=f"test-{tmp_path.name}",
        label="Test tiles",
        url=(tmp_path.as_uri() + "/{z}-{x}-{y}.png"),
        attribution="test",
        max_zoom=8,
    )
    canvas = _canvas()
    canvas.set_source(source)
    finished: list[tuple[bool, int]] = []
    loop = QEventLoop()
    canvas.prefetch_finished.connect(
        lambda cancelled, errors: (finished.append((cancelled, errors)), loop.quit())
    )
    QTimer.singleShot(5_000, loop.quit)

    canvas.start_prefetch([(8, 138, 86)])
    loop.exec()

    assert finished == [(False, 0)]
    assert canvas.cache is not None
    assert canvas.cache.contains(8, 138, 86)
    canvas.set_source(None)


def test_map_snapshot_exports_the_rendered_overlays(tmp_path) -> None:
    application = _application()
    canvas = _canvas(640, 420)
    canvas.set_source(None)
    canvas.own_grid = "JO70FB28MC"
    canvas.stations = [("OK2IPW", "JN89HE", 0.0)]
    canvas.station_states = {"OK2IPW": "relay"}
    canvas.locator_grid_chars = 4
    canvas.range_rings = True
    canvas.measure_start = from_locator("JO70FB28MC")
    canvas.measure_end = from_locator("JN89HE")
    canvas.show()
    application.processEvents()
    path = tmp_path / "situation.png"

    assert canvas.export_png(str(path))
    image = QPixmap(str(path))
    assert not image.isNull()
    assert image.size() == canvas.size()
    canvas.close()
