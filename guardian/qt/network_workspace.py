"""Network routes and heard-stations workspace."""

from __future__ import annotations

import time

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
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
        tabs.addTab(self._scanner_page(), tr("network.scanner"))
        outer.addWidget(tabs, 1)
        self.refresh()

    def _routes_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.routes_table = RowTable(0, 7)
        self.routes_table.setHorizontalHeaderLabels(
            [
                tr("network.destination"),
                tr("network.preferred"),
                tr("network.backup"),
                tr("network.frequency"),
                tr("network.mode"),
                tr("network.working_frequency"),
                tr("network.working_mode"),
            ]
        )
        # Selecting a row loads it into the form below, so an existing entry
        # can be corrected in place instead of being retyped from scratch.
        self.routes_table.itemSelectionChanged.connect(self._load_selected_route)
        route_header = self.routes_table.horizontalHeader()
        route_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        route_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (2, 3, 4, 5, 6):
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

    def _scanner_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        hint = QLabel(tr("network.scanner_hint"))
        hint.setWordWrap(True)
        hint.setObjectName("Metadata")
        layout.addWidget(hint)
        form = QFormLayout()
        self.scanner_status = QLabel()
        self.scanner_current = QLabel("—")
        self.scanner_channels = QLabel("0")
        self.scanner_dwell = QSpinBox()
        self.scanner_dwell.setRange(1, 300)
        self.scanner_dwell.setValue(max(1, int(self.runtime.config.scan_dwell)))
        self.scanner_use_signal = QCheckBox(tr("network.scanner_use_signal"))
        self.scanner_threshold = QSpinBox()
        self.scanner_threshold.setRange(-200, 200)
        threshold = self.runtime.config.scan_signal_threshold
        self.scanner_use_signal.setChecked(threshold is not None)
        self.scanner_threshold.setValue(int(threshold or 0))
        self.scanner_threshold.setEnabled(threshold is not None)
        self.scanner_use_signal.toggled.connect(self.scanner_threshold.setEnabled)
        form.addRow(tr("network.scanner_status"), self.scanner_status)
        form.addRow(tr("network.scanner_current"), self.scanner_current)
        form.addRow(tr("network.scanner_channels"), self.scanner_channels)
        form.addRow(tr("network.scanner_dwell"), self.scanner_dwell)
        form.addRow(self.scanner_use_signal)
        form.addRow(tr("network.scanner_threshold"), self.scanner_threshold)
        layout.addLayout(form)
        self.scanner_toggle = QPushButton()
        self.scanner_toggle.setObjectName("primaryAction")
        self.scanner_toggle.clicked.connect(self._toggle_scanner)
        layout.addWidget(self.scanner_toggle)
        layout.addStretch()
        return page

    def _toggle_scanner(self) -> None:
        operations = self.runtime.operations
        if operations.scanner is not None:
            operations.stop_scanner()
        else:
            self.runtime.config.scan_dwell = float(self.scanner_dwell.value())
            self.runtime.config.scan_signal_threshold = (
                int(self.scanner_threshold.value())
                if self.scanner_use_signal.isChecked()
                else None
            )
            self.runtime.config.save()
            operations.start_scanner()
        self.refresh()

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
                self.working_frequency.value(),
                self.working_mode.currentData(),
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
        self._sync_working_channel_visibility()
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
                (
                    self.working_frequency.textFromValue(route.working_freq_hz)
                    if route.working_freq_hz
                    else ""
                ),
                route.working_mode,
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
        network = self.runtime.snapshots.read().network
        if network.scanner_paused:
            scanner_status = tr("network.scanner_paused")
        elif network.scanner_holding:
            scanner_status = tr("network.scanner_holding")
        elif network.scanner_active:
            scanner_status = tr("network.scanner_scanning")
        else:
            scanner_status = tr("network.scanner_stopped")
        self.scanner_status.setText(scanner_status)
        self.scanner_current.setText(
            (
                f"{network.scanner_frequency_hz / 1_000_000:.4f} MHz"
                + (f" — {network.scanner_channel}" if network.scanner_channel else "")
            )
            if network.scanner_frequency_hz
            else "—"
        )
        available = (
            network.scanner_channels
            if network.scanner_active
            else len(self.runtime.operations.scanner_channels())
        )
        self.scanner_channels.setText(str(available))
        self.scanner_toggle.setText(
            tr("network.scanner_stop")
            if network.scanner_active
            else tr("network.scanner_start")
        )
        self.scanner_dwell.setEnabled(not network.scanner_active)
        self.scanner_use_signal.setEnabled(not network.scanner_active)
        self.scanner_threshold.setEnabled(
            not network.scanner_active and self.scanner_use_signal.isChecked()
        )
