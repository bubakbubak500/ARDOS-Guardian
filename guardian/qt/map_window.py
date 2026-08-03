"""Station map: where this station is, and where the ones it hears are.

Drawn with QPainter on an equirectangular projection. That is a deliberate
choice over embedding a web map: Leaflet in QtWebEngine would add roughly
150 MB to a 41 MB installer for a picture we can draw ourselves, and it would
want a network the whole point of ARDOS is to survive without.

The map is useful with **no map data at all** -- a graticule, the stations,
and the bearing and distance to each is most of what an operator wants from
it. An offline raster background is a layer to add later, never a thing the
window depends on.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontMetricsF,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..i18n import dual, tr
from ..location import LocationFailure, LocationFix
from ..message import Folder, Status
from .alerts import alert_headline
from .mail_workspace import ComposeDialog
from .location import WindowsLocationRequest
from .map_tiles import SOURCES, TILE_PIXELS, TileCache, TileSource, tile_for
from ..routing import (
    MAX_LOCATOR_CHARS,
    distance_bearing,
    from_locator,
    is_locator,
    locator_bounds,
    to_locator,
)

MIN_DEGREES_ACROSS = 0.02       # ~2 km wide; finer than any locator square
MAX_DEGREES_ACROSS = 360.0
DEFAULT_DEGREES_ACROSS = 8.0    # a comfortable regional view
# A lone station has no span to fit, and framing it at the old floor gave a
# view 57 km wide -- technically correct and useless. Show it some country.
MIN_FIT_DEGREES = 2.0
MERCATOR_LIMIT = 85.05112878     # where the projection runs to infinity
MAX_TILES_PER_DRAW = 400         # a zoomed-out view must not ask for thousands
MAX_TILES_IN_FLIGHT = 8          # polite to a service given away for free
# An alert stops pulsing on the map after this long; the banner and the log
# keep the history. Matches what the notifier considers worth interrupting for.
ALERT_MAP_WINDOW = 900.0
PULSE_INTERVAL_MS = 200


class MapCanvas(QWidget):
    """Pans, zooms, and reports where the operator clicked."""

    picked = Signal(float, float)          # latitude, longitude
    station_picked = Signal(str)           # callsign

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(480, 360)
        self.setMouseTracking(True)
        self.center = (49.8, 15.5)          # lat, lon
        self.degrees_across = DEFAULT_DEGREES_ACROSS
        self.picking = False
        self.own_grid = ""
        self.preview_grid = ""
        self.stations: list[tuple[str, str, float]] = []   # call, grid, age
        self.links: list[tuple[str, str, str]] = []  # call, grid, mail activity
        # Relay paths mail actually travelled: each chain is the mapped grids
        # of consecutive hops, origin first, this station last.
        self.chains: list[tuple[str, ...]] = []
        # Grids of stations that originated an alert still worth interrupting
        # for; painted as a pulsing ring, so the geography of the emergency is
        # one glance, not a callsign lookup.
        self.alert_grids: list[str] = []
        self._drag_from: QPointF | None = None
        self._dragged = False
        self.source: TileSource | None = None
        self._cache: TileCache | None = None
        self._pixmaps: dict[tuple[int, int, int], QPixmap] = {}
        self._pending: set[tuple[int, int, int]] = set()
        self._missing: set[tuple[int, int, int]] = set()
        self._network = QNetworkAccessManager(self)
        self._network.finished.connect(self._tile_arrived)

    # --- tiles ------------------------------------------------------------ #
    def set_source(self, source: TileSource | None) -> None:
        if self._cache is not None:
            self._cache.close()
            self._cache = None
        self.source = source
        self._pixmaps.clear()
        self._missing.clear()
        if source is not None:
            self._cache = TileCache(source)
        self.update()

    @property
    def cache(self) -> TileCache | None:
        return self._cache

    def tile_zoom(self) -> int:
        """The tile level whose pixels come closest to the screen's."""
        if self.source is None:
            return 0
        scale = self.pixels_per_world()
        zoom = int(round(math.log2(max(scale, 1.0) / TILE_PIXELS)))
        return max(0, min(zoom, self.source.max_zoom))

    def _draw_tiles(self, painter: QPainter) -> None:
        if self.source is None or self._cache is None:
            return
        zoom = self.tile_zoom()
        count = 2 ** zoom
        scale = self.pixels_per_world()
        size = scale / count                    # on-screen size of one tile
        if size <= 0:
            return
        north_west = self.to_position(QPointF(0, 0))
        south_east = self.to_position(QPointF(self.width(), self.height()))
        first_x, first_y = tile_for(north_west[0], north_west[1], zoom)
        last_x, last_y = tile_for(south_east[0], south_east[1], zoom)
        # A view wider than the world would otherwise ask for thousands.
        if (last_x - first_x + 1) * (last_y - first_y + 1) > MAX_TILES_PER_DRAW:
            return
        origin_x = self.world_x(self.center[1]) * scale - self.width() / 2
        origin_y = self.world_y(self.center[0]) * scale - self.height() / 2
        for x in range(first_x, last_x + 1):
            for y in range(first_y, last_y + 1):
                pixmap = self._tile(zoom, x, y)
                if pixmap is None:
                    continue
                painter.drawPixmap(
                    QRectF(x * size - origin_x, y * size - origin_y, size, size),
                    pixmap,
                    QRectF(0, 0, pixmap.width(), pixmap.height()),
                )

    def _tile(self, zoom: int, x: int, y: int) -> QPixmap | None:
        key = (zoom, x, y)
        if key in self._pixmaps:
            return self._pixmaps[key]
        if self._cache is None or key in self._missing:
            return None
        data = self._cache.get(zoom, x, y)
        if data is not None:
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                self._pixmaps[key] = pixmap
                return pixmap
            self._missing.add(key)
            return None
        self._request(key)
        return None

    def _request(self, key: tuple[int, int, int]) -> None:
        """Ask for one tile, on demand only.

        Never a region the operator has not looked at: prefetching is exactly
        what tile providers forbid, and what would turn a courtesy into abuse.
        """
        if key in self._pending or len(self._pending) >= MAX_TILES_IN_FLIGHT:
            return
        if self.source is None:
            return
        zoom, x, y = key
        request = QNetworkRequest(QUrl(self.source.tile_url(zoom, x, y)))
        request.setHeader(
            QNetworkRequest.KnownHeaders.UserAgentHeader,
            f"Guardian/{__version__} (ARDOS emergency messaging)",
        )
        request.setAttribute(
            QNetworkRequest.Attribute.User, f"{zoom}/{x}/{y}"
        )
        self._pending.add(key)
        self._network.get(request)

    def _tile_arrived(self, reply) -> None:
        try:
            coordinates = reply.request().attribute(QNetworkRequest.Attribute.User)
            zoom, x, y = (int(part) for part in str(coordinates).split("/"))
            key = (zoom, x, y)
            self._pending.discard(key)
            if reply.error() != reply.error().NoError:
                self._missing.add(key)
                return
            data = bytes(reply.readAll())
            pixmap = QPixmap()
            if not data or not pixmap.loadFromData(data):
                self._missing.add(key)
                return
            if self._cache is not None:
                self._cache.put(zoom, x, y, data)
            self._pixmaps[key] = pixmap
            self.update()
        finally:
            reply.deleteLater()

    # --- projection ---------------------------------------------------- #
    # Web Mercator, in the slippy-map convention where the whole world is a
    # unit square. Not a cosmetic choice: the raster background is served in
    # exactly this projection, and drawing it under stations plotted in any
    # other would put the two out of register -- on a map used to find people,
    # an unacceptable kind of wrong.
    @staticmethod
    def world_x(longitude: float) -> float:
        return (longitude + 180.0) / 360.0

    @staticmethod
    def world_y(latitude: float) -> float:
        latitude = min(max(latitude, -MERCATOR_LIMIT), MERCATOR_LIMIT)
        return (
            1.0 - math.asinh(math.tan(math.radians(latitude))) / math.pi
        ) / 2.0

    @staticmethod
    def longitude_at(world_x: float) -> float:
        return world_x * 360.0 - 180.0

    @staticmethod
    def latitude_at(world_y: float) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * world_y))))

    def pixels_per_world(self) -> float:
        """Pixels the whole world would span at the current zoom."""
        return self.width() * 360.0 / max(self.degrees_across, MIN_DEGREES_ACROSS)

    def to_screen(self, latitude: float, longitude: float) -> QPointF:
        scale = self.pixels_per_world()
        return QPointF(
            self.width() / 2
            + (self.world_x(longitude) - self.world_x(self.center[1])) * scale,
            self.height() / 2
            + (self.world_y(latitude) - self.world_y(self.center[0])) * scale,
        )

    def to_position(self, point: QPointF) -> tuple[float, float]:
        scale = self.pixels_per_world()
        world_x = self.world_x(self.center[1]) + (point.x() - self.width() / 2) / scale
        world_y = self.world_y(self.center[0]) + (point.y() - self.height() / 2) / scale
        return (
            self.latitude_at(min(max(world_y, 0.0), 1.0)),
            (self.longitude_at(world_x) + 180.0) % 360.0 - 180.0,
        )

    # --- interaction ---------------------------------------------------- #
    def mousePressEvent(self, event) -> None:
        self._drag_from = QPointF(event.position())
        self._dragged = False

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is None:
            return
        delta = QPointF(event.position()) - self._drag_from
        if abs(delta.x()) + abs(delta.y()) < 3 and not self._dragged:
            return
        self._dragged = True
        scale = self.pixels_per_world()
        world_x = self.world_x(self.center[1]) - delta.x() / scale
        world_y = min(max(self.world_y(self.center[0]) - delta.y() / scale, 0.0), 1.0)
        self.center = (self.latitude_at(world_y), self.longitude_at(world_x))
        self._drag_from = QPointF(event.position())
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        # A click is a press that did not become a drag; otherwise panning the
        # map would keep moving the operator's own station around with it.
        if self.picking and not self._dragged:
            latitude, longitude = self.to_position(QPointF(event.position()))
            self.picked.emit(latitude, longitude)
        elif not self._dragged:
            callsign = self.station_at(QPointF(event.position()))
            if callsign:
                self.station_picked.emit(callsign)
        self._drag_from = None

    def station_at(self, point: QPointF, radius: float = 16.0) -> str:
        """Return the nearest plotted peer under a click, if there is one."""
        nearest = ""
        nearest_distance = radius
        for callsign, grid, _age in self.stations:
            try:
                latitude, longitude = from_locator(grid)
            except ValueError:
                continue
            centre = self.to_screen(latitude, longitude)
            distance = math.hypot(point.x() - centre.x(), point.y() - centre.y())
            if distance <= nearest_distance:
                nearest = callsign
                nearest_distance = distance
        return nearest

    def wheelEvent(self, event) -> None:
        step = 0.8 if event.angleDelta().y() > 0 else 1.25
        self.degrees_across = min(
            MAX_DEGREES_ACROSS, max(MIN_DEGREES_ACROSS, self.degrees_across * step)
        )
        self.update()

    def fit(self, points: list[tuple[float, float]], margin: float = 1.25) -> None:
        """Frame every point, allowing for the shape of the window.

        The width is what `degrees_across` measures, but a north-south spread
        is limited by the *height* -- and latitude degrees are stretched by
        1/cos(lat) on top of that. Fitting to the larger of the two spans
        alone dropped Vienna and Berlin off a Prague-centred view: 4.33
        degrees of latitude asked for, 3.49 shown.
        """
        if not points:
            self.look_at(20.0, 0.0, 340.0)
            return
        # In world units both axes are directly comparable, so "does it fit"
        # is one division per axis instead of trigonometry that forgets the
        # window is wider than it is tall.
        xs = [self.world_x(point[1]) for point in points]
        ys = [self.world_y(point[0]) for point in points]
        span_x = max(max(xs) - min(xs), 1e-9)
        span_y = max(max(ys) - min(ys), 1e-9)
        scale = min(self.width() / span_x, self.height() / span_y) / margin
        across = min(
            MAX_DEGREES_ACROSS,
            max(self.width() * 360.0 / scale, MIN_FIT_DEGREES),
        )
        self.look_at(
            self.latitude_at((min(ys) + max(ys)) / 2),
            self.longitude_at((min(xs) + max(xs)) / 2),
            across,
        )

    def look_at(self, latitude: float, longitude: float, degrees: float | None = None) -> None:
        self.center = (latitude, longitude)
        if degrees is not None:
            self.degrees_across = min(
                MAX_DEGREES_ACROSS, max(MIN_DEGREES_ACROSS, degrees)
            )
        self.update()

    # --- painting -------------------------------------------------------- #
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#101418"))
        self._draw_tiles(painter)
        self._draw_graticule(painter)
        for chain in self.chains:
            self._draw_chain(painter, chain)
        for callsign, grid, activity in self.links:
            self._draw_link(painter, callsign, grid, activity)
        for callsign, grid, age in self.stations:
            self._draw_station(painter, callsign, grid, age, own=False)
        if self.own_grid:
            self._draw_station(painter, tr("map.you"), self.own_grid, 0.0, own=True)
        if self.preview_grid and self.preview_grid != self.own_grid:
            self._draw_station(
                painter,
                tr("map.detected_marker"),
                self.preview_grid,
                0.0,
                own=False,
                preview=True,
            )
        for grid in self.alert_grids:
            self._draw_alert_ring(painter, grid)
        painter.end()

    def _graticule_step(self) -> float:
        """A round step giving roughly six lines across the view."""
        for step in (30.0, 10.0, 5.0, 2.0, 1.0, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01):
            if self.degrees_across / step >= 4:
                return step
        return 0.01

    def _draw_graticule(self, painter: QPainter) -> None:
        step = self._graticule_step()
        painter.setPen(QPen(QColor("#243040"), 1))
        font = QFont(painter.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1))
        painter.setFont(font)
        south, west = self.to_position(QPointF(0, self.height()))
        north, east = self.to_position(QPointF(self.width(), 0))
        line = math.floor(west / step) * step
        while line <= east:
            x = self.to_screen(0, line).x()
            painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            painter.drawText(QPointF(x + 3, self.height() - 4), f"{line:g}°")
            line += step
        line = math.floor(south / step) * step
        while line <= north:
            y = self.to_screen(line, 0).y()
            painter.drawLine(QPointF(0, y), QPointF(self.width(), y))
            painter.drawText(QPointF(3, y - 3), f"{line:g}°")
            line += step

    def _draw_station(
        self,
        painter: QPainter,
        label: str,
        grid: str,
        age: float,
        *,
        own: bool,
        preview: bool = False,
    ) -> None:
        try:
            south, west, north, east = locator_bounds(grid)
        except ValueError:
            return
        colour = (
            QColor("#ffb000")
            if preview
            else QColor("#1683ff") if own else QColor("#14a83b")
        )
        if age > 600:
            colour = QColor("#66717c")      # heard a while ago, faded back
        top_left = self.to_screen(north, west)
        bottom_right = self.to_screen(south, east)
        square = QRectF(top_left, bottom_right)
        painter.save()
        # A dark under-stroke preserves the vector outline over white roads,
        # yellow fields, and other high-contrast raster detail.
        painter.setPen(QPen(QColor(0, 0, 0, 190), 5, Qt.PenStyle.DashLine))
        # Below a few pixels the square is a smudge; the dot is the honest
        # rendering of "somewhere in here" at that zoom.
        if square.width() >= 6 and square.height() >= 6:
            painter.drawRect(square)
            painter.setPen(QPen(colour, 3, Qt.PenStyle.DashLine))
            painter.drawRect(square)
        centre = square.center()
        painter.setPen(QPen(QColor(0, 0, 0, 210), 2))
        painter.setBrush(QColor(0, 0, 0, 210))
        painter.drawEllipse(centre, 9, 9)
        painter.setPen(QPen(colour, 2))
        painter.setBrush(colour)
        painter.drawEllipse(centre, 6, 6)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        font = QFont(painter.font())
        font.setBold(True)
        painter.setFont(font)
        text = f"{label}  {grid}"
        metrics = QFontMetricsF(font)
        bounds = metrics.boundingRect(text).adjusted(-5, -3, 5, 3)
        bounds.moveTopLeft(centre + QPointF(10, -bounds.height() / 2))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 185))
        painter.drawRoundedRect(bounds, 4, 4)
        painter.setPen(QPen(QColor("#ffffff"), 1))
        painter.drawText(
            bounds,
            Qt.AlignmentFlag.AlignCenter,
            text,
        )
        painter.restore()

    def _grid_centre(self, grid: str) -> QPointF | None:
        try:
            south, west, north, east = locator_bounds(grid)
        except ValueError:
            return None
        return QRectF(
            self.to_screen(north, west), self.to_screen(south, east)
        ).center()

    def _draw_chain(self, painter: QPainter, grids: tuple[str, ...]) -> None:
        """The path a message actually took, hop by hop.

        Dashed and in a different voice than the orange correspondent links:
        those say who the operator talked to, this says who carried it.
        """
        points = [self._grid_centre(grid) for grid in grids]
        if any(point is None for point in points) or len(points) < 2:
            return
        colour = QColor("#00c8ff")
        painter.save()
        for start, end in zip(points, points[1:]):
            painter.setPen(QPen(QColor(0, 0, 0, 190), 5, Qt.PenStyle.DashLine))
            painter.drawLine(start, end)
            painter.setPen(QPen(colour, 3, Qt.PenStyle.DashLine))
            painter.drawLine(start, end)
            # A small arrowhead at the middle of each segment, pointing the
            # way the message travelled -- toward this station.
            dx, dy = end.x() - start.x(), end.y() - start.y()
            length = math.hypot(dx, dy)
            if length > 30:
                ux, uy = dx / length, dy / length
                mid = (start + end) / 2
                painter.drawLine(
                    mid, mid + QPointF(-ux * 10 - uy * 6, -uy * 10 + ux * 6)
                )
                painter.drawLine(
                    mid, mid + QPointF(-ux * 10 + uy * 6, -uy * 10 - ux * 6)
                )
        painter.restore()

    def _draw_alert_ring(self, painter: QPainter, grid: str) -> None:
        """Pulse where an alert came from; repainted by the owner's timer."""
        centre = self._grid_centre(grid)
        if centre is None:
            return
        phase = (time.monotonic() % 1.6) / 1.6
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(QColor(255, 59, 48, max(0, int(230 * (1.0 - phase)))), 4)
        )
        radius = 12.0 + 30.0 * phase
        painter.drawEllipse(centre, radius, radius)
        painter.setPen(QPen(QColor(255, 59, 48, 220), 3))
        painter.drawEllipse(centre, 11, 11)
        painter.restore()

    def _draw_link(
        self, painter: QPainter, callsign: str, grid: str, _activity: str
    ) -> None:
        """Draw an operator-visible path to a station with mail activity."""
        if not self.own_grid:
            return
        try:
            own_lat, own_lon = from_locator(self.own_grid)
            other_lat, other_lon = from_locator(grid)
        except ValueError:
            return
        start = self.to_screen(own_lat, own_lon)
        end = self.to_screen(other_lat, other_lon)
        distance, bearing = distance_bearing(
            own_lat, own_lon, other_lat, other_lon
        )
        painter.save()
        painter.setPen(QPen(QColor(0, 0, 0, 205), 7, Qt.PenStyle.SolidLine))
        painter.drawLine(start, end)
        colour = QColor("#ff7a00")
        painter.setPen(QPen(colour, 4, Qt.PenStyle.SolidLine))
        painter.drawLine(start, end)

        # Arrowhead points toward the correspondent, not merely between the
        # two markers, so the direction remains obvious in a busy map.
        dx, dy = end.x() - start.x(), end.y() - start.y()
        length = math.hypot(dx, dy)
        if length > 20:
            ux, uy = dx / length, dy / length
            arrow = 13.0
            wing = 7.0
            base = end - QPointF(ux * 11, uy * 11)
            left = (
                base
                - QPointF(ux * arrow, uy * arrow)
                + QPointF(-uy * wing, ux * wing)
            )
            right = (
                base
                - QPointF(ux * arrow, uy * arrow)
                - QPointF(-uy * wing, ux * wing)
            )
            painter.drawLine(base, left)
            painter.drawLine(base, right)

        text = f"{callsign}  {distance:.0f} km  {bearing:.0f}°"
        font = QFont(painter.font())
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        bounds = metrics.boundingRect(text).adjusted(-6, -4, 6, 4)
        midpoint = (start + end) / 2
        bounds.moveCenter(midpoint + QPointF(0, -12))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 205))
        painter.drawRoundedRect(bounds, 5, 5)
        painter.setPen(QPen(QColor("#ffffff"), 1))
        painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


class MapWindow(QDialog):
    """The map, the operator's own position, and what it will transmit."""

    _compose_requested = Signal(str)

    def __init__(
        self,
        runtime,
        parent=None,
        *,
        location_request_factory=None,
        location_consent=None,
    ) -> None:
        super().__init__(parent)
        self._compose_requested.connect(
            self._open_compose,
            Qt.ConnectionType.QueuedConnection,
        )
        self.runtime = runtime
        self._location_request_factory = (
            location_request_factory or WindowsLocationRequest
        )
        self._location_consent = location_consent or self._ask_location_consent
        self._location_request = None
        self._detected_fix: LocationFix | None = None
        self._detected_grid = ""
        self.setWindowTitle(tr("map.title"))
        self.setMinimumSize(760, 560)

        outer = QVBoxLayout(self)
        intro = QLabel(tr("map.intro"))
        intro.setObjectName("Metadata")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        # Where the last alert came from, one line above the map. The pulsing
        # ring says "there"; this says what and who.
        self.alert_chip = QLabel()
        self.alert_chip.setProperty("statusRole", "danger")
        self.alert_chip.setWordWrap(True)
        self.alert_chip.hide()
        outer.addWidget(self.alert_chip)

        self.canvas = MapCanvas()
        self.canvas.picked.connect(self._picked)
        self.canvas.station_picked.connect(self._compose_to)

        # The situational panel: every heard station with the numbers an
        # operator wants at a glance, whether or not it sent a position yet.
        self.panel = QTableWidget(0, 8)
        self.panel.setHorizontalHeaderLabels(
            [
                tr("map.col_station"),
                tr("map.col_grid"),
                tr("map.col_distance"),
                tr("map.col_bearing"),
                tr("map.col_snr"),
                tr("map.col_age"),
                tr("map.col_channel"),
                tr("map.col_reaches"),
            ]
        )
        self.panel.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.panel.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.panel.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.panel.verticalHeader().setVisible(False)
        header = self.panel.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.panel.cellClicked.connect(self._panel_clicked)
        self.panel.cellDoubleClicked.connect(self._panel_double_clicked)
        self._panel_calls: list[str] = []

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.canvas)
        split.addWidget(self.panel)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([760, 380])
        outer.addWidget(split, 1)

        # Repaint while an alert ring is pulsing; a no-op otherwise, so the
        # ordinary once-a-second poll stays the only refresh the map needs.
        self._pulse = QTimer(self)
        self._pulse.setInterval(PULSE_INTERVAL_MS)
        self._pulse.timeout.connect(self._pulse_tick)
        self._pulse.start()

        position_group = QGroupBox(tr("map.position_group"))
        position_layout = QVBoxLayout(position_group)
        controls = QHBoxLayout()
        self.detect_button = QPushButton(tr("map.detect"))
        self.detect_button.setToolTip(tr("map.detect_hint"))
        self.detect_button.clicked.connect(self._detect_location)
        controls.addWidget(self.detect_button)
        self.detect_cancel = QPushButton(tr("map.detect_cancel"))
        self.detect_cancel.clicked.connect(self._cancel_location)
        self.detect_cancel.hide()
        controls.addWidget(self.detect_cancel)
        self.pick_button = QPushButton(tr("map.pick"))
        self.pick_button.setCheckable(True)
        self.pick_button.toggled.connect(self._pick_toggled)
        controls.addWidget(self.pick_button)
        controls.addStretch(1)
        position_layout.addLayout(controls)

        locator_controls = QHBoxLayout()
        locator_controls.addWidget(QLabel(tr("map.locator")))
        self.locator_edit = QLineEdit(self.runtime.config.station_grid)
        self.locator_edit.setMaxLength(MAX_LOCATOR_CHARS)
        self.locator_edit.setPlaceholderText("JN89HE12AB")
        self.locator_edit.setFixedWidth(140)
        self.locator_edit.editingFinished.connect(self._typed)
        locator_controls.addWidget(self.locator_edit)
        self.transmit = QCheckBox(tr("map.transmit"))
        self.transmit.setChecked(self.runtime.config.beacon_position)
        self.transmit.setToolTip(tr("map.transmit_hint"))
        self.transmit.toggled.connect(self._transmit_toggled)
        locator_controls.addWidget(self.transmit)
        locator_controls.addStretch(1)
        position_layout.addLayout(locator_controls)

        self.location_status = QLabel()
        self.location_status.setObjectName("Metadata")
        self.location_status.setWordWrap(True)
        self.location_status.hide()
        position_layout.addWidget(self.location_status)

        self.detected_panel = QFrame()
        self.detected_panel.setFrameShape(QFrame.Shape.StyledPanel)
        detected_layout = QHBoxLayout(self.detected_panel)
        self.detected_text = QLabel()
        self.detected_text.setWordWrap(True)
        detected_layout.addWidget(self.detected_text, 1)
        self.use_detected = QPushButton(tr("map.detect_use"))
        self.use_detected.setObjectName("primaryAction")
        self.use_detected.clicked.connect(self._use_detected)
        detected_layout.addWidget(self.use_detected)
        discard = QPushButton(tr("map.detect_discard"))
        discard.clicked.connect(self._discard_detected)
        detected_layout.addWidget(discard)
        self.detected_panel.hide()
        position_layout.addWidget(self.detected_panel)

        self.location_settings = QPushButton(tr("map.location_settings"))
        self.location_settings.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("ms-settings:privacy-location"))
        )
        self.location_settings.hide()
        position_layout.addWidget(self.location_settings, 0, Qt.AlignmentFlag.AlignLeft)
        outer.addWidget(position_group)

        map_controls = QHBoxLayout()
        map_controls.addStretch(1)
        self.background = QCheckBox(tr("map.background"))
        self.background.setChecked(self.runtime.config.map_background)
        self.background.setToolTip(tr("map.background_hint"))
        self.background.toggled.connect(self._background_toggled)
        map_controls.addWidget(self.background)
        home = QPushButton(tr("map.centre"))
        home.clicked.connect(self._centre)
        map_controls.addWidget(home)
        outer.addLayout(map_controls)

        self.status = QLabel()
        self.status.setObjectName("Metadata")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(
            tr("common.close")
        )
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self.attribution = QLabel()
        self.attribution.setObjectName("Metadata")
        outer.addWidget(self.attribution)

        self._framed = False
        self._background_toggled(self.background.isChecked())
        self.refresh()
        self._centre()

    # --- data ------------------------------------------------------------ #
    def stations(self) -> list[tuple[str, str, float]]:
        """Heard stations that have told us where they are."""
        now = time.monotonic()
        return [
            (station.callsign, station.grid, station.age(now))
            for station in self.runtime.heard.active(now)
            if is_locator(station.grid)
        ]

    def refresh(self) -> None:
        self.canvas.own_grid = (self.runtime.config.station_grid or "").upper()
        self.canvas.stations = self.stations()
        self.canvas.links = self.interactions()
        self.canvas.chains = self.hop_chains()
        self.canvas.alert_grids = self._alert_grids()
        self._refresh_alert_chip()
        self._refresh_panel()
        self.canvas.update()
        self._describe()
        self._describe_background()

    def active_alerts(self) -> list:
        """Alerts recent enough to still mark the map, newest first."""
        now = time.time()
        return [
            record
            for record in getattr(self.runtime.operations, "alerts", [])
            if not record.mine and now - record.received <= ALERT_MAP_WINDOW
        ]

    def _alert_grids(self) -> list[str]:
        positions = {
            callsign.upper(): grid
            for callsign, grid, _age in self.canvas.stations
        }
        return sorted(
            {
                positions[record.source]
                for record in self.active_alerts()
                if record.source in positions
            }
        )

    def _refresh_alert_chip(self) -> None:
        alerts = self.active_alerts()
        if not alerts:
            self.alert_chip.hide()
            return
        newest = alerts[0]
        minutes = max(0, int((time.time() - newest.received) // 60))
        note = f" — {newest.note}" if newest.note else ""
        self.alert_chip.setText(
            f"⚠ {alert_headline(newest)}{note} · {newest.source} · "
            + tr("map.alert_age", minutes=minutes)
        )
        self.alert_chip.show()

    def _pulse_tick(self) -> None:
        if self.canvas.alert_grids and self.isVisible():
            self.canvas.update()

    def hop_chains(self) -> list[tuple[str, ...]]:
        """Relay paths mail actually took, as runs of mapped hop positions.

        Direct exchanges stay with the orange links; a chain is only worth
        drawing when at least one relay carried the message. Unmapped hops
        split a chain rather than invent a position for it -- every drawn
        segment really was one hop.
        """
        positions = {
            callsign.upper(): grid
            for callsign, grid, _age in self.canvas.stations
        }
        mine = (self.runtime.config.callsign or "").strip().upper()
        own = (self.runtime.config.station_grid or "").upper()
        if mine and is_locator(own):
            positions[mine] = own
        chains: set[tuple[str, ...]] = set()
        for meta in self.runtime.mailstore.list():
            hops = [str(hop).strip().upper() for hop in meta.get("hops") or []]
            if (
                meta.get("folder") in (Folder.INBOX, Folder.TRANSIT)
                and mine
                and mine not in hops
            ):
                hops.append(mine)
            if len(hops) < 3:
                continue
            run: list[str] = []
            for hop in hops:
                grid = positions.get(hop, "")
                if grid:
                    run.append(grid)
                else:
                    if len(run) >= 2:
                        chains.add(tuple(run))
                    run = []
            if len(run) >= 2:
                chains.add(tuple(run))
        return sorted(chains)

    # --- situational panel ------------------------------------------------ #
    @staticmethod
    def _age_text(age: float) -> str:
        if age < 60:
            return f"{age:.0f} s"
        return f"{age / 60:.0f} min"

    def panel_rows(self) -> list[tuple[str, ...]]:
        """One row per heard station: the numbers, already formatted."""
        now = time.monotonic()
        own = (self.runtime.config.station_grid or "").upper()
        own_position = from_locator(own) if is_locator(own) else None
        rows: list[tuple[str, ...]] = []
        for station in self.runtime.heard.active(now):
            distance_text = bearing_text = ""
            if own_position is not None and is_locator(station.grid):
                latitude, longitude = from_locator(station.grid)
                distance, bearing = distance_bearing(
                    own_position[0], own_position[1], latitude, longitude
                )
                distance_text = f"{distance:.0f}"
                bearing_text = f"{bearing:.0f}°"
            rows.append(
                (
                    station.callsign,
                    station.grid or "—",
                    distance_text,
                    bearing_text,
                    f"{station.last_snr:+.1f}" if station.last_snr is not None else "",
                    self._age_text(station.age(now)),
                    f"{station.last_freq_hz / 1_000_000:.4f}"
                    if station.last_freq_hz
                    else "",
                    ", ".join(sorted(station.reaches)),
                )
            )
        return rows

    def _refresh_panel(self) -> None:
        rows = self.panel_rows()
        calls = [row[0] for row in rows]
        if calls == self._panel_calls:
            # Same stations in the same order: update the numbers in place so
            # the selection and scroll position survive the 1 Hz poll.
            for index, row in enumerate(rows):
                for column, value in enumerate(row):
                    item = self.panel.item(index, column)
                    if item is not None and item.text() != value:
                        item.setText(value)
            return
        selected = ""
        picked = self.panel.selectedItems()
        if picked:
            selected = self.panel.item(picked[0].row(), 0).text()
        self.panel.setRowCount(len(rows))
        for index, row in enumerate(rows):
            for column, value in enumerate(row):
                self.panel.setItem(index, column, QTableWidgetItem(value))
        self._panel_calls = calls
        if selected in calls:
            self.panel.selectRow(calls.index(selected))

    def _panel_clicked(self, row: int, _column: int) -> None:
        """A click brings the station into view; it must never transmit."""
        item = self.panel.item(row, 1)
        grid = item.text() if item is not None else ""
        if is_locator(grid):
            latitude, longitude = from_locator(grid)
            self.canvas.look_at(latitude, longitude)

    def _panel_double_clicked(self, row: int, _column: int) -> None:
        item = self.panel.item(row, 0)
        if item is not None and item.text():
            self._compose_to(item.text())

    def interactions(self) -> list[tuple[str, str, str]]:
        """Mapped peers involved in sending, sent, or received mail."""
        positions = {
            callsign.upper(): grid
            for callsign, grid, _age in self.canvas.stations
        }
        mine = (self.runtime.config.callsign or "").strip().upper()
        activity: dict[str, str] = {}
        for meta in self.runtime.mailstore.list():
            folder = meta.get("folder")
            status = meta.get("status")
            callsign = ""
            kind = ""
            if folder == Folder.INBOX and status == Status.RECEIVED:
                callsign = str(meta.get("source") or "").strip().upper()
                kind = "received"
            elif folder == Folder.SENT and status == Status.DELIVERED:
                callsign = str(meta.get("final_dest") or "").strip().upper()
                kind = "sent"
            elif folder == Folder.OUTBOX and status == Status.SENDING:
                callsign = str(meta.get("final_dest") or "").strip().upper()
                kind = "sending"
            if callsign and callsign != mine and callsign in positions:
                # Active transfer wins if a peer also has older history.
                if kind == "sending" or callsign not in activity:
                    activity[callsign] = kind
        return [
            (callsign, positions[callsign], kind)
            for callsign, kind in sorted(activity.items())
        ]

    def _describe(self) -> None:
        own = self.canvas.own_grid
        stations = self.canvas.stations
        if not own:
            self.status.setText(tr("map.no_position"))
            return
        latitude, longitude = from_locator(own)
        parts = [
            tr(
                "map.own_position",
                locator=own,
                latitude=f"{latitude:.4f}",
                longitude=f"{longitude:.4f}",
            )
        ]
        for callsign, grid, _age in sorted(stations):
            other_lat, other_lon = from_locator(grid)
            distance, bearing = distance_bearing(
                latitude, longitude, other_lat, other_lon
            )
            parts.append(f"{callsign} {grid}  {distance:.0f} km  {bearing:.0f}°")
        self.status.setText("   ·   ".join(parts))

    # --- interaction ------------------------------------------------------ #
    def _detect_location(self) -> None:
        if not self._location_consent():
            self.location_status.setText(tr("map.location_failure_cancelled"))
            self.location_status.show()
            return
        self._discard_detected()
        self.location_settings.hide()
        request = self._location_request_factory(self)
        self._location_request = request
        request.state_changed.connect(self._location_state_changed)
        request.fix_ready.connect(self._location_ready)
        request.failed.connect(self._location_failed)
        self.detect_button.setEnabled(False)
        self.detect_cancel.show()
        request.start()

    def _ask_location_consent(self) -> bool:
        answer = QMessageBox.question(
            self,
            tr("map.location_consent_title"),
            tr("map.location_consent_body"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _cancel_location(self) -> None:
        if self._location_request is not None:
            self._location_request.cancel()

    def _location_state_changed(self, state: str) -> None:
        key = {"locating": "map.location_locating"}.get(
            state, "map.location_locating"
        )
        self.location_status.setText(tr(key))
        self.location_status.show()

    def _location_ready(self, fix: LocationFix) -> None:
        self._location_request = None
        self.detect_button.setEnabled(True)
        self.detect_cancel.hide()
        self.location_settings.hide()
        grid = to_locator(fix.latitude, fix.longitude, MAX_LOCATOR_CHARS)
        self._detected_fix = fix
        self._detected_grid = grid
        self.canvas.preview_grid = grid
        self.canvas.look_at(fix.latitude, fix.longitude, 0.08)
        accuracy = self._accuracy_text(fix.accuracy_m)
        source = tr(f"map.location_source_{fix.source.value}")
        warning = " " + tr("map.location_approximate") if fix.is_approximate else ""
        self.detected_text.setText(
            tr(
                "map.location_result",
                locator=grid,
                accuracy=accuracy,
                source=source,
            ) + warning
        )
        self.location_status.setText(tr("map.location_review"))
        self.location_status.show()
        self.detected_panel.show()
        self.canvas.update()

    def _location_failed(self, failure: LocationFailure, _detail: str) -> None:
        self._location_request = None
        self.detect_button.setEnabled(True)
        self.detect_cancel.hide()
        self.location_status.setText(tr(f"map.location_failure_{failure.value}"))
        self.location_status.show()
        self.location_settings.setVisible(
            failure in (LocationFailure.DENIED, LocationFailure.DISABLED)
        )
        self.runtime.events.publish(
            dual(
                f"Device location failed: {failure.value}.",
                f"Zjištění polohy zařízení selhalo: {failure.value}.",
            ),
            source="location",
        )

    @staticmethod
    def _accuracy_text(accuracy_m: float) -> str:
        return (
            f"±{accuracy_m:.0f} m"
            if accuracy_m < 1_000
            else f"±{accuracy_m / 1_000:.1f} km"
        )

    def _use_detected(self) -> None:
        if self._detected_grid:
            grid = self._detected_grid
            self._discard_detected(clear_status=False)
            self._apply(grid)

    def _discard_detected(self, *, clear_status: bool = True) -> None:
        self._detected_fix = None
        self._detected_grid = ""
        self.canvas.preview_grid = ""
        self.detected_panel.hide()
        if clear_status:
            self.location_status.hide()
        self.canvas.update()

    def _pick_toggled(self, enabled: bool) -> None:
        self.canvas.picking = enabled
        self.canvas.setCursor(
            Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor
        )

    def _compose_to(self, callsign: str) -> None:
        """Clicking a heard marker starts a message addressed to that peer.

        Opened from the idle event loop, not from the click itself. This
        arrives from the canvas mouse-release handler, and running a modal
        dialog there parks its event loop inside a mouse event the canvas has
        not finished -- the implicit mouse grab is still held for as long as
        the dialog lives. On some Windows machines that grab outlives the
        dialog: the message queues, the dialog is told to close, and the
        window stays on screen. Letting the click finish first costs nothing
        and leaves the dialog an ordinary top-level modal window.
        """
        self._compose_requested.emit(callsign)

    def _open_compose(self, callsign: str) -> None:
        dialog = ComposeDialog(self.runtime, self, destination=callsign)
        dialog.exec()

    def _picked(self, latitude: float, longitude: float) -> None:
        # Always store the finest locator: the beacon can carry all ten
        # characters beside any callsign, and a coarse square can be derived
        # from a fine one but never the other way round.
        self._apply(to_locator(latitude, longitude, MAX_LOCATOR_CHARS))
        self.pick_button.setChecked(False)

    def _typed(self) -> None:
        text = self.locator_edit.text().strip().upper()
        if text and not is_locator(text):
            self.status.setText(tr("map.bad_locator", locator=text))
            self.locator_edit.setText(self.runtime.config.station_grid)
            return
        self._apply(text)

    def _apply(self, locator: str) -> None:
        self._discard_detected(clear_status=False)
        self.runtime.config.station_grid = locator
        self.runtime.config.save()
        self.locator_edit.setText(locator)
        self.runtime.events.publish(
            dual(
                f"Station position set to {locator}." if locator
                else "Station position cleared.",
                f"Poloha stanice nastavena na {locator}." if locator
                else "Poloha stanice byla zrušena.",
            ),
            source="map",
        )
        self.refresh()

    def closeEvent(self, event) -> None:
        if self._location_request is not None:
            self._location_request.cancel()
            self._location_request = None
        super().closeEvent(event)

    def _transmit_toggled(self, enabled: bool) -> None:
        self.runtime.config.beacon_position = enabled
        self.runtime.config.save()

    def _background_toggled(self, enabled: bool) -> None:
        self.runtime.config.map_background = enabled
        self.runtime.config.save()
        self.canvas.set_source(SOURCES[0] if enabled else None)
        self._describe_background()

    def _describe_background(self) -> None:
        """Credit the source, and say how much of it is already on disk."""
        source = self.canvas.source
        if source is None:
            self.attribution.setText(tr("map.background_off"))
            return
        cache = self.canvas.cache
        self.attribution.setText(
            tr(
                "map.attribution",
                source=source.label,
                credit=source.attribution,
                tiles=cache.count() if cache else 0,
                megabytes=f"{cache.megabytes():.1f}" if cache else "0.0",
            )
        )

    def showEvent(self, event) -> None:
        # A widget has no real geometry until it is shown, and the fit is
        # computed from the window's shape -- framing it in __init__ used the
        # size hint and then never corrected itself.
        super().showEvent(event)
        if not self._framed:
            self._framed = True
            self._centre()

    def _centre(self) -> None:
        """Frame what there is: us, or whoever we can hear, or the world."""
        self.canvas.fit(
            [
                from_locator(grid)
                for grid in [self.canvas.own_grid]
                + [station[1] for station in self.canvas.stations]
                if is_locator(grid)
            ]
        )
