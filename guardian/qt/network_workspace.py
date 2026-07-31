"""Network routes and heard-stations workspace."""

from __future__ import annotations

import time

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..routing import Route, locator_distance_bearing
from ..i18n import dual, tr
from .inputs import FrequencySpinBox, RowTable, UppercaseLineEdit
from .runtime import ShellRuntime


class NetworkWorkspace(QWidget):
    def __init__(self, runtime: ShellRuntime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        title = QLabel(tr("network.title"))
        title.setObjectName("PanelHeader")
        outer.addWidget(title)
        tabs = QTabWidget()
        tabs.addTab(self._routes_page(), tr("network.routes"))
        tabs.addTab(self._heard_page(), tr("network.heard"))
        outer.addWidget(tabs, 1)
        self.refresh()

    def _routes_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.routes_table = RowTable(0, 5)
        self.routes_table.setHorizontalHeaderLabels(
            [
                tr("network.destination"),
                tr("network.preferred"),
                tr("network.backup"),
                tr("network.frequency"),
                tr("network.mode"),
            ]
        )
        # Selecting a row loads it into the form below, so an existing entry
        # can be corrected in place instead of being retyped from scratch.
        self.routes_table.itemSelectionChanged.connect(self._load_selected_route)
        route_header = self.routes_table.horizontalHeader()
        route_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        route_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (2, 3, 4):
            route_header.setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.ResizeToContents,
            )
        layout.addWidget(self.routes_table, 1)
        form = QFormLayout()
        self.destination = UppercaseLineEdit()
        self.preferred = UppercaseLineEdit()
        self.backup = UppercaseLineEdit()
        self.frequency = FrequencySpinBox()
        self.mode = QComboBox()
        self.mode.addItem(tr("network.mode_vara_fm"), "FM")
        self.mode.addItem(tr("network.mode_vara_hf"), "USB")
        form.addRow(tr("network.destination"), self.destination)
        form.addRow(tr("network.preferred"), self.preferred)
        form.addRow(tr("network.backup"), self.backup)
        form.addRow(tr("network.frequency"), self.frequency)
        form.addRow(tr("network.mode"), self.mode)
        layout.addLayout(form)
        actions = QHBoxLayout()
        add = QPushButton(tr("network.add"))
        add.setObjectName("primaryAction")
        add.clicked.connect(self._save_route)
        remove = QPushButton(tr("network.remove"))
        remove.clicked.connect(self._remove_route)
        actions.addWidget(add)
        actions.addStretch()
        actions.addWidget(remove)
        layout.addLayout(actions)
        return page

    def _heard_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        detail = QLabel(
            tr("network.heard_hint")
        )
        detail.setObjectName("Metadata")
        layout.addWidget(detail)
        self.heard_table = RowTable(0, 8)
        self.heard_table.setHorizontalHeaderLabels(
            [
                tr("network.callsign"),
                tr("network.age"),
                tr("network.frames"),
                tr("network.snr"),
                tr("network.heard_on"),
                tr("network.locator"),
                tr("network.distance"),
                tr("network.last_frame"),
            ]
        )
        self.heard_table.horizontalHeader().setSectionResizeMode(
            7, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.heard_table, 1)
        return page

    def _selected_route(self) -> Route | None:
        row = self.routes_table.currentRow()
        item = self.routes_table.item(row, 0) if row >= 0 else None
        if item is None:
            return None
        return self.runtime.routes.lookup(item.text())

    def _load_selected_route(self) -> None:
        route = self._selected_route()
        if route is None:
            return
        self.destination.setText(route.destination)
        self.preferred.setText(route.preferred)
        self.backup.setText(route.backup)
        self.frequency.setValue(route.freq_hz)
        index = self.mode.findData(route.mode)
        if index >= 0:
            self.mode.setCurrentIndex(index)

    def _clear_form(self) -> None:
        self.destination.clear()
        self.preferred.clear()
        self.backup.clear()
        self.frequency.setValue(0)
        self.mode.setCurrentIndex(0)

    def _save_route(self) -> None:
        destination = self.destination.text().strip().upper()
        preferred = self.preferred.text().strip().upper()
        if not destination:
            QMessageBox.warning(
                self,
                tr("network.routes"),
                tr("network.route_required"),
            )
            return
        self.runtime.routes.add(
            Route(
                destination,
                preferred,
                self.backup.text(),
                self.frequency.value(),
                self.mode.currentData(),
            )
        )
        self.runtime.routes.save()
        route_description = (
            dual(f"via {preferred}", f"přes {preferred}")
            if preferred
            else dual("directly", "přímo")
        )
        self.runtime.events.publish(
            dual(
                f"Route {destination} saved {route_description}.",
                f"Trasa {destination} byla uložena {route_description}.",
            ),
            source="network",
        )
        self.refresh()

    def _remove_route(self) -> None:
        route = self._selected_route()
        if route is None:
            return
        destination = route.destination
        self.runtime.routes.remove(destination)
        self._clear_form()
        self.runtime.routes.save()
        self.runtime.events.publish(
            dual(
                f"Route {destination} removed.",
                f"Trasa {destination} byla odstraněna.",
            ),
            source="network",
        )
        self.refresh()

    def refresh(self) -> None:
        routes = self.runtime.routes.routes
        self.routes_table.setRowCount(len(routes))
        for row, route in enumerate(routes):
            values = (
                route.destination,
                route.preferred,
                route.backup,
                (
                    self.frequency.textFromValue(route.freq_hz)
                    if route.freq_hz
                    else ""
                ),
                route.mode,
            )
            for column, value in enumerate(values):
                self.routes_table.setItem(row, column, QTableWidgetItem(value))
        now = time.monotonic()
        heard = self.runtime.heard.active(now)
        own_grid = (self.runtime.config.station_grid or "").upper()
        self.heard_table.setRowCount(len(heard))
        for row, station in enumerate(heard):
            # Distance needs both ends of the path, so it stays empty until
            # this station knows where it is itself.
            relative = locator_distance_bearing(own_grid, station.grid) if (
                own_grid and station.grid
            ) else None
            values = (
                station.callsign,
                f"{station.age(now):.0f} s",
                str(station.count),
                "-" if station.last_snr is None else f"{station.last_snr:.1f} dB",
                (
                    self.frequency.textFromValue(station.last_freq_hz)
                    if station.last_freq_hz
                    else "-"
                ),
                station.grid or "-",
                "-" if relative is None else f"{relative[0]:.0f} km  {relative[1]:.0f}°",
                station.last_frame,
            )
            for column, value in enumerate(values):
                self.heard_table.setItem(row, column, QTableWidgetItem(value))
