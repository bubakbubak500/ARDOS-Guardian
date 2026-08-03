"""Network routes, shared topology and heard-stations workspace."""

from __future__ import annotations

import time

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
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

from ..i18n import dual, tr
from ..routing import Route, Topology, locator_distance_bearing, write_topology_csv
from .inputs import FrequencySpinBox, RowTable, UppercaseLineEdit
from .runtime import ShellRuntime
from .topology_wizard import TOPOLOGY_FILTER, TopologyWizard, topology_warning_text


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
        tabs.addTab(self._topology_page(), tr("network.topology"))
        outer.addWidget(tabs, 1)
        self.refresh()

    def _routes_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.routes_table = RowTable(0, 8)
        self.routes_table.setHorizontalHeaderLabels(
            [
                tr("network.destination"),
                tr("network.preferred"),
                tr("network.backup"),
                tr("network.frequency"),
                tr("network.mode"),
                tr("network.working_frequency"),
                tr("network.working_mode"),
                tr("network.route_source"),
            ]
        )
        self.routes_table.itemSelectionChanged.connect(self._load_selected_route)
        header = self.routes_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (2, 3, 4, 5, 6, 7):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.routes_table, 1)

        form = QFormLayout()
        self.destination = UppercaseLineEdit()
        self.preferred = UppercaseLineEdit()
        self.backup = UppercaseLineEdit()
        self.frequency = FrequencySpinBox()
        self.mode = QComboBox()
        self.mode.addItem(tr("network.mode_vara_fm"), "FM")
        self.mode.addItem(tr("network.mode_vara_hf"), "USB")
        self.working_frequency = FrequencySpinBox()
        self.working_mode = QComboBox()
        self.working_mode.addItem(tr("network.mode_vara_fm"), "FM")
        self.working_mode.addItem(tr("network.mode_vara_hf"), "USB")
        form.addRow(tr("network.destination"), self.destination)
        form.addRow(tr("network.preferred"), self.preferred)
        form.addRow(tr("network.backup"), self.backup)
        form.addRow(tr("network.frequency"), self.frequency)
        form.addRow(tr("network.mode"), self.mode)
        self.working_frequency_label = QLabel(tr("network.working_frequency"))
        self.working_mode_label = QLabel(tr("network.working_mode"))
        form.addRow(self.working_frequency_label, self.working_frequency)
        form.addRow(self.working_mode_label, self.working_mode)
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
        self._sync_working_channel_visibility()
        return page

    def _sync_working_channel_visibility(self) -> None:
        visible = bool(self.runtime.config.separate_working_channels)
        for column in (5, 6):
            self.routes_table.setColumnHidden(column, not visible)
        for widget in (
            self.working_frequency_label,
            self.working_frequency,
            self.working_mode_label,
            self.working_mode,
        ):
            widget.setVisible(visible)

    def _heard_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        detail = QLabel(tr("network.heard_hint"))
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

    def _topology_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        hint = QLabel(tr("network.topology_hint"))
        hint.setWordWrap(True)
        hint.setObjectName("Metadata")
        layout.addWidget(hint)
        self.topology_summary = QLabel()
        self.topology_summary.setWordWrap(True)
        layout.addWidget(self.topology_summary)
        self.topology_table = RowTable(0, 7)
        self.topology_table.setHorizontalHeaderLabels(
            [
                tr("network.station_a"),
                tr("network.station_b"),
                tr("network.direction"),
                tr("network.frequency"),
                tr("network.mode"),
                tr("network.cost"),
                tr("network.enabled"),
            ]
        )
        header = self.topology_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 7):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.topology_table, 1)
        self.topology_warnings = QLabel()
        self.topology_warnings.setWordWrap(True)
        self.topology_warnings.setObjectName("Metadata")
        layout.addWidget(self.topology_warnings)
        actions = QHBoxLayout()
        wizard = QPushButton(tr("network.topology_wizard"))
        wizard.setObjectName("primaryAction")
        wizard.clicked.connect(self._open_topology_wizard)
        recompute = QPushButton(tr("network.topology_recompute"))
        recompute.clicked.connect(self._apply_topology)
        export = QPushButton(tr("network.topology_export"))
        export.clicked.connect(self._export_topology)
        actions.addWidget(wizard)
        actions.addWidget(recompute)
        actions.addStretch()
        actions.addWidget(export)
        layout.addLayout(actions)
        return page

    def _open_topology_wizard(self) -> None:
        heard = {
            station.callsign
            for station in self.runtime.heard.active(time.monotonic())
        }
        wizard = TopologyWizard(
            self.runtime.topology,
            self.runtime.config.callsign,
            heard,
            self,
        )
        if wizard.exec() != QDialog.DialogCode.Accepted:
            return
        self.runtime.topology = Topology(wizard.topology.links)
        self.runtime.topology.save()
        self._apply_topology()

    def _apply_topology(self) -> None:
        routes = self.runtime.topology.derive_routes(self.runtime.config.callsign)
        self.runtime.routes.replace_topology(routes)
        self.runtime.routes.save()
        self.runtime.events.publish(
            tr(
                "network.topology_applied",
                count=len(routes),
                callsign=self.runtime.config.callsign,
            ),
            source="network",
        )
        self.runtime.refresh()
        self.refresh()

    def _export_topology(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("network.topology_export"),
            "guardian-topology.csv",
            TOPOLOGY_FILTER,
        )
        if not path:
            return
        try:
            write_topology_csv(path, self.runtime.topology)
        except OSError as exc:
            QMessageBox.warning(self, tr("network.topology"), str(exc))

    def _selected_route(self) -> Route | None:
        row = self.routes_table.currentRow()
        item = self.routes_table.item(row, 0) if row >= 0 else None
        return self.runtime.routes.lookup(item.text()) if item is not None else None

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
        self.working_frequency.setValue(route.working_freq_hz)
        index = self.working_mode.findData(route.working_mode)
        if index >= 0:
            self.working_mode.setCurrentIndex(index)

    def _clear_form(self) -> None:
        self.destination.clear()
        self.preferred.clear()
        self.backup.clear()
        self.frequency.setValue(0)
        self.mode.setCurrentIndex(0)
        self.working_frequency.setValue(0)
        self.working_mode.setCurrentIndex(0)

    def _save_route(self) -> None:
        destination = self.destination.text().strip().upper()
        preferred = self.preferred.text().strip().upper()
        if not destination:
            QMessageBox.warning(self, tr("network.routes"), tr("network.route_required"))
            return
        self.runtime.routes.add(
            Route(
                destination,
                preferred,
                self.backup.text(),
                self.frequency.value(),
                self.mode.currentData(),
                self.working_frequency.value(),
                self.working_mode.currentData(),
                "manual",
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
        if route.source == "topology":
            QMessageBox.information(
                self,
                tr("network.topology"),
                tr("network.topology_remove_hint"),
            )
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
        self._sync_working_channel_visibility()
        routes = self.runtime.routes.routes
        self.routes_table.setRowCount(len(routes))
        for row, route in enumerate(routes):
            values = (
                route.destination,
                route.preferred,
                route.backup,
                self.frequency.textFromValue(route.freq_hz) if route.freq_hz else "",
                route.mode,
                (
                    self.working_frequency.textFromValue(route.working_freq_hz)
                    if route.working_freq_hz
                    else ""
                ),
                route.working_mode,
                tr(f"network.source_{route.source}"),
            )
            for column, value in enumerate(values):
                self.routes_table.setItem(row, column, QTableWidgetItem(value))

        now = time.monotonic()
        heard = self.runtime.heard.active(now)
        own_grid = (self.runtime.config.station_grid or "").upper()
        self.heard_table.setRowCount(len(heard))
        for row, station in enumerate(heard):
            relative = (
                locator_distance_bearing(own_grid, station.grid)
                if own_grid and station.grid
                else None
            )
            values = (
                station.callsign,
                f"{station.age(now):.0f} s",
                str(station.count),
                "-" if station.last_snr is None else f"{station.last_snr:.1f} dB",
                self.frequency.textFromValue(station.last_freq_hz) if station.last_freq_hz else "-",
                station.grid or "-",
                "-" if relative is None else f"{relative[0]:.0f} km  {relative[1]:.0f}°",
                station.last_frame,
            )
            for column, value in enumerate(values):
                self.heard_table.setItem(row, column, QTableWidgetItem(value))

        topology = self.runtime.topology
        derived = topology.derive_routes(self.runtime.config.callsign)
        self.topology_summary.setText(
            tr(
                "network.topology_summary",
                nodes=len(topology.nodes),
                links=len(topology.links),
                routes=len(derived),
                callsign=self.runtime.config.callsign or "—",
            )
        )
        links = topology.links
        self.topology_table.setRowCount(len(links))
        for row, link in enumerate(links):
            values = (
                link.station_a,
                link.station_b,
                tr(f"network.direction_{link.direction}"),
                self.frequency.textFromValue(link.freq_hz) if link.freq_hz else "",
                link.mode,
                f"{link.cost:g}",
                tr("common.yes") if link.enabled else tr("common.no"),
            )
            for column, value in enumerate(values):
                self.topology_table.setItem(row, column, QTableWidgetItem(value))
        heard_callsigns = {station.callsign for station in heard}
        warnings = (
            topology.warnings(self.runtime.config.callsign, heard_callsigns)
            if links
            else []
        )
        self.topology_warnings.setText(
            "\n".join(f"• {topology_warning_text(warning)}" for warning in warnings)
            if warnings
            else tr("network.topology_no_warnings")
        )
