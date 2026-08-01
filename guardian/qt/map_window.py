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

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..i18n import dual, tr
from ..message import Folder, Status
from .mail_workspace import ComposeDialog
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
        self.stations: list[tuple[str, str, float]] = []   # call, grid, age
        self.links: list[tuple[str, str, str]] = []  # call, grid, mail activity
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
        for callsign, grid, activity in self.links:
            self._draw_link(painter, callsign, grid, activity)
        for callsign, grid, age in self.stations:
            self._draw_station(painter, callsign, grid, age, own=False)
        if self.own_grid:
            self._draw_station(painter, tr("map.you"), self.own_grid, 0.0, own=True)
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
        self, painter: QPainter, label: str, grid: str, age: float, *, own: bool
    ) -> None:
        try:
            south, west, north, east = locator_bounds(grid)
        except ValueError:
            return
        colour = QColor("#1683ff") if own else QColor("#14a83b")
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

    def __init__(self, runtime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.setWindowTitle(tr("map.title"))
        self.setMinimumSize(760, 560)

        outer = QVBoxLayout(self)
        intro = QLabel(tr("map.intro"))
        intro.setObjectName("Metadata")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        self.canvas = MapCanvas()
        self.canvas.picked.connect(self._picked)
        self.canvas.station_picked.connect(self._compose_to)
        outer.addWidget(self.canvas, 1)

        controls = QHBoxLayout()
        self.pick_button = QPushButton(tr("map.pick"))
        self.pick_button.setCheckable(True)
        self.pick_button.toggled.connect(self._pick_toggled)
        controls.addWidget(self.pick_button)
        controls.addWidget(QLabel(tr("map.locator")))
        self.locator_edit = QLineEdit(self.runtime.config.station_grid)
        self.locator_edit.setMaxLength(MAX_LOCATOR_CHARS)
        self.locator_edit.setPlaceholderText("JN89HE12AB")
        self.locator_edit.setFixedWidth(140)
        self.locator_edit.editingFinished.connect(self._typed)
        controls.addWidget(self.locator_edit)
        self.transmit = QCheckBox(tr("map.transmit"))
        self.transmit.setChecked(self.runtime.config.beacon_position)
        self.transmit.setToolTip(tr("map.transmit_hint"))
        self.transmit.toggled.connect(self._transmit_toggled)
        controls.addWidget(self.transmit)
        controls.addStretch(1)
        self.background = QCheckBox(tr("map.background"))
        self.background.setChecked(self.runtime.config.map_background)
        self.background.setToolTip(tr("map.background_hint"))
        self.background.toggled.connect(self._background_toggled)
        controls.addWidget(self.background)
        home = QPushButton(tr("map.centre"))
        home.clicked.connect(self._centre)
        controls.addWidget(home)
        outer.addLayout(controls)

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
        self.canvas.update()
        self._describe()
        self._describe_background()

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
    def _pick_toggled(self, enabled: bool) -> None:
        self.canvas.picking = enabled
        self.canvas.setCursor(
            Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor
        )

    def _compose_to(self, callsign: str) -> None:
        """Clicking a heard marker starts a message addressed to that peer."""
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
