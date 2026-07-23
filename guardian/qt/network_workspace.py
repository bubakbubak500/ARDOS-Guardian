"""Network routes and heard-stations workspace."""

from __future__ import annotations

import time

from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..routing import Route
from .runtime import ShellRuntime


class NetworkWorkspace(QWidget):
    def __init__(self, runtime: ShellRuntime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        title = QLabel("Network")
        title.setObjectName("PanelHeader")
        outer.addWidget(title)
        tabs = QTabWidget()
        tabs.addTab(self._routes_page(), "Routes")
        tabs.addTab(self._heard_page(), "Heard stations")
        outer.addWidget(tabs, 1)
        self.refresh()

    def _routes_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.routes_table = QTableWidget(0, 5)
        self.routes_table.setHorizontalHeaderLabels(
            ["Destination", "Preferred hop", "Backup", "Frequency (Hz)", "Mode"]
        )
        self.routes_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.routes_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.routes_table, 1)
        form = QFormLayout()
        self.destination = QLineEdit()
        self.preferred = QLineEdit()
        self.backup = QLineEdit()
        self.frequency = QSpinBox()
        self.frequency.setRange(0, 2_147_483_647)
        self.mode = QLineEdit()
        form.addRow("Destination", self.destination)
        form.addRow("Preferred next hop", self.preferred)
        form.addRow("Backup", self.backup)
        form.addRow("Frequency (Hz)", self.frequency)
        form.addRow("Mode", self.mode)
        layout.addLayout(form)
        actions = QHBoxLayout()
        add = QPushButton("Add or replace route")
        add.setObjectName("primaryAction")
        add.clicked.connect(self._save_route)
        remove = QPushButton("Remove selected")
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
            "Stations appear here only after a real control frame is received."
        )
        detail.setObjectName("Metadata")
        layout.addWidget(detail)
        self.heard_table = QTableWidget(0, 5)
        self.heard_table.setHorizontalHeaderLabels(
            ["Callsign", "Age", "Frames", "Last SNR", "Last frame"]
        )
        self.heard_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.heard_table, 1)
        return page

    def _save_route(self) -> None:
        destination = self.destination.text().strip().upper()
        preferred = self.preferred.text().strip().upper()
        if not destination or not preferred:
            QMessageBox.warning(
                self,
                "Route",
                "Destination and preferred next hop are required.",
            )
            return
        self.runtime.routes.add(
            Route(
                destination,
                preferred,
                self.backup.text(),
                self.frequency.value(),
                self.mode.text(),
            )
        )
        self.runtime.routes.save()
        self.runtime.events.publish(
            f"Route {destination} via {preferred} saved.",
            source="network",
        )
        self.refresh()

    def _remove_route(self) -> None:
        row = self.routes_table.currentRow()
        if row < 0:
            return
        destination = self.routes_table.item(row, 0).text()
        self.runtime.routes.remove(destination)
        self.runtime.routes.save()
        self.runtime.events.publish(
            f"Route {destination} removed.",
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
                str(route.freq_hz or ""),
                route.mode,
            )
            for column, value in enumerate(values):
                self.routes_table.setItem(row, column, QTableWidgetItem(value))
        now = time.monotonic()
        heard = self.runtime.heard.active(now)
        self.heard_table.setRowCount(len(heard))
        for row, station in enumerate(heard):
            values = (
                station.callsign,
                f"{station.age(now):.0f} s",
                str(station.count),
                "-" if station.last_snr is None else f"{station.last_snr:.1f} dB",
                station.last_frame,
            )
            for column, value in enumerate(values):
                self.heard_table.setItem(row, column, QTableWidgetItem(value))
