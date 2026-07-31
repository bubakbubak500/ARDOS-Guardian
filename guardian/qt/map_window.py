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

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
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

from ..i18n import dual, tr
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


class MapCanvas(QWidget):
    """Pans, zooms, and reports where the operator clicked."""

    picked = Signal(float, float)          # latitude, longitude

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(480, 360)
        self.setMouseTracking(True)
        self.center = (49.8, 15.5)          # lat, lon
        self.degrees_across = DEFAULT_DEGREES_ACROSS
        self.picking = False
        self.own_grid = ""
        self.stations: list[tuple[str, str, float]] = []   # call, grid, age
        self._drag_from: QPointF | None = None
        self._dragged = False

    # --- projection ---------------------------------------------------- #
    def _scale(self) -> tuple[float, float]:
        """Pixels per degree of longitude and of latitude.

        Longitude degrees shrink with latitude, so the vertical scale is
        stretched by 1/cos(lat) to keep the picture from looking squashed --
        the standard equirectangular compromise, honest enough at the size of
        a VHF net and cheap enough to compute per frame.
        """
        per_lon = self.width() / max(self.degrees_across, MIN_DEGREES_ACROSS)
        return per_lon, per_lon / max(0.15, math.cos(math.radians(self.center[0])))

    def to_screen(self, latitude: float, longitude: float) -> QPointF:
        per_lon, per_lat = self._scale()
        return QPointF(
            self.width() / 2 + (longitude - self.center[1]) * per_lon,
            self.height() / 2 - (latitude - self.center[0]) * per_lat,
        )

    def to_position(self, point: QPointF) -> tuple[float, float]:
        per_lon, per_lat = self._scale()
        longitude = self.center[1] + (point.x() - self.width() / 2) / per_lon
        latitude = self.center[0] - (point.y() - self.height() / 2) / per_lat
        return (
            min(max(latitude, -90.0), 90.0),
            (longitude + 180.0) % 360.0 - 180.0,
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
        per_lon, per_lat = self._scale()
        self.center = (
            min(max(self.center[0] + delta.y() / per_lat, -90.0), 90.0),
            self.center[1] - delta.x() / per_lon,
        )
        self._drag_from = QPointF(event.position())
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        # A click is a press that did not become a drag; otherwise panning the
        # map would keep moving the operator's own station around with it.
        if self.picking and not self._dragged:
            latitude, longitude = self.to_position(QPointF(event.position()))
            self.picked.emit(latitude, longitude)
        self._drag_from = None

    def wheelEvent(self, event) -> None:
        step = 0.8 if event.angleDelta().y() > 0 else 1.25
        self.degrees_across = min(
            MAX_DEGREES_ACROSS, max(MIN_DEGREES_ACROSS, self.degrees_across * step)
        )
        self.update()

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
        self._draw_graticule(painter)
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
        colour = QColor("#4ea1ff") if own else QColor("#7ddc7d")
        if age > 600:
            colour = QColor("#8a949e")      # heard a while ago, faded back
        top_left = self.to_screen(north, west)
        bottom_right = self.to_screen(south, east)
        square = QRectF(top_left, bottom_right)
        painter.setPen(QPen(colour, 1, Qt.PenStyle.DashLine))
        # Below a few pixels the square is a smudge; the dot is the honest
        # rendering of "somewhere in here" at that zoom.
        if square.width() >= 6 and square.height() >= 6:
            painter.drawRect(square)
        centre = square.center()
        painter.setPen(QPen(colour, 2))
        painter.setBrush(colour)
        painter.drawEllipse(centre, 4, 4)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawText(centre + QPointF(8, 4), f"{label}  {grid}")


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
        self.canvas.update()
        self._describe()

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

    def _centre(self) -> None:
        """Frame what there is: us, or whoever we can hear, or the world."""
        points = [
            from_locator(grid)
            for grid in [self.canvas.own_grid] + [s[1] for s in self.canvas.stations]
            if is_locator(grid)
        ]
        if not points:
            self.canvas.look_at(20.0, 0.0, 340.0)
            return
        latitudes = [point[0] for point in points]
        longitudes = [point[1] for point in points]
        span = max(
            max(longitudes) - min(longitudes),
            max(latitudes) - min(latitudes),
            0.4,
        )
        self.canvas.look_at(
            (min(latitudes) + max(latitudes)) / 2,
            (min(longitudes) + max(longitudes)) / 2,
            span * 2.0,
        )
