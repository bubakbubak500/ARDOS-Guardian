"""Network routes, shared topology and heard-stations workspace."""

from __future__ import annotations

import time
import re

from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QDialog,
    QFileDialog,
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
        self.tabs = QTabWidget()
        self.tabs.addTab(self._routes_page(), tr("network.routes"))
        self.tabs.addTab(self._heard_page(), tr("network.heard"))
        self.tabs.addTab(self._topology_page(), tr("network.topology"))
        self.tabs.addTab(self._discovery_page(), tr("network.discovery"))
        outer.addWidget(self.tabs, 1)
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

    def _discovery_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        hint = QLabel(tr("network.discovery_hint"))
        hint.setWordWrap(True)
        hint.setObjectName("Metadata")
        layout.addWidget(hint)

        self.discovery_sections = QTabWidget()
        settings_page = QWidget()
        settings_layout = QVBoxLayout(settings_page)
        form = QFormLayout()
        self.discovery_mode = QComboBox()
        for mode in ("off", "monitor", "assisted"):
            self.discovery_mode.addItem(tr(f"network.discovery_mode_{mode}"), mode)
        index = self.discovery_mode.findData(self.runtime.config.discovery_mode)
        self.discovery_mode.setCurrentIndex(max(0, index))
        self.discovery_forward = QCheckBox(tr("network.discovery_forward"))
        self.discovery_forward.setChecked(self.runtime.config.discovery_forward)
        self.discovery_ttl = QSpinBox()
        self.discovery_ttl.setRange(2, 8)
        self.discovery_ttl.setValue(self.runtime.config.discovery_ttl)
        self.discovery_lifetime = QSpinBox()
        self.discovery_lifetime.setRange(1, 1440)
        self.discovery_lifetime.setSuffix(" min")
        self.discovery_lifetime.setValue(
            max(1, int(self.runtime.config.discovery_route_lifetime / 60))
        )
        self.discovery_budget = QSpinBox()
        self.discovery_budget.setRange(1, 120)
        self.discovery_budget.setSuffix(tr("network.discovery_frames_minute_suffix"))
        self.discovery_budget.setValue(self.runtime.config.discovery_frame_budget)
        self.discovery_allowlist = UppercaseLineEdit(
            ", ".join(self.runtime.config.discovery_allowlist)
        )
        self.discovery_denylist = UppercaseLineEdit(
            ", ".join(self.runtime.config.discovery_denylist)
        )
        form.addRow(tr("network.discovery_mode"), self.discovery_mode)
        form.addRow(self.discovery_forward)
        form.addRow(tr("network.discovery_ttl"), self.discovery_ttl)
        form.addRow(tr("network.discovery_lifetime"), self.discovery_lifetime)
        form.addRow(tr("network.discovery_budget"), self.discovery_budget)
        form.addRow(tr("network.discovery_allowlist"), self.discovery_allowlist)
        form.addRow(tr("network.discovery_denylist"), self.discovery_denylist)
        settings_layout.addLayout(form)

        settings_row = QHBoxLayout()
        save = QPushButton(tr("network.discovery_save"))
        save.clicked.connect(self._save_discovery_settings)
        self.discovery_status = QLabel()
        self.discovery_status.setWordWrap(True)
        self.discovery_status.setObjectName("Metadata")
        settings_row.addWidget(save)
        settings_row.addWidget(self.discovery_status, 1)
        settings_layout.addStretch()

        route_page = QWidget()
        route_layout = QVBoxLayout(route_page)
        self.discovery_auto_use = QCheckBox(tr("network.discovery_auto_use"))
        self.discovery_auto_use.setChecked(self.runtime.config.discovery_auto_use)
        route_layout.addWidget(self.discovery_auto_use)

        query_row = QHBoxLayout()
        self.discovery_destination = UppercaseLineEdit()
        self.discovery_destination.setPlaceholderText("S1 / OK1AAA")
        query = QPushButton(tr("network.discovery_start"))
        query.setObjectName("primaryAction")
        query.clicked.connect(self._start_discovery)
        query_row.addWidget(QLabel(tr("network.destination")))
        query_row.addWidget(self.discovery_destination, 1)
        query_row.addWidget(query)
        route_layout.addLayout(query_row)

        self.discovery_routes = RowTable(0, 9)
        self.discovery_routes.setHorizontalHeaderLabels(
            [
                tr("network.destination"),
                tr("network.discovery_next_hop"),
                tr("network.discovery_hops"),
                tr("network.discovery_metric"),
                tr("network.age"),
                tr("network.discovery_expires"),
                tr("network.discovery_approved"),
                tr("network.discovery_state"),
                tr("network.route_source"),
            ]
        )
        discovery_header = self.discovery_routes.horizontalHeader()
        discovery_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        discovery_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 9):
            discovery_header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        route_layout.addWidget(self.discovery_routes, 1)

        route_actions = QHBoxLayout()
        approve = QPushButton(tr("network.discovery_approve"))
        approve.clicked.connect(self._approve_discovered_route)
        clear = QPushButton(tr("network.discovery_clear"))
        clear.clicked.connect(self._clear_discovered_routes)
        route_actions.addWidget(approve)
        route_actions.addStretch()
        route_actions.addWidget(clear)
        route_layout.addLayout(route_actions)

        self.discovery_pending = RowTable(0, 5)
        self.discovery_pending.setMaximumHeight(130)
        self.discovery_pending.setHorizontalHeaderLabels(
            [
                tr("network.discovery_query_id"),
                tr("network.destination"),
                tr("network.discovery_ttl_short"),
                tr("network.discovery_context"),
                tr("network.discovery_state"),
            ]
        )
        self.discovery_pending.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        route_layout.addWidget(self.discovery_pending)
        self.discovery_recent = QLabel()
        self.discovery_recent.setWordWrap(True)
        self.discovery_recent.setObjectName("Metadata")
        route_layout.addWidget(self.discovery_recent)
        self.discovery_sections.addTab(
            route_page, tr("network.discovery_routes_tab")
        )

        live_page = QWidget()
        live_layout = QVBoxLayout(live_page)
        live_hint = QLabel(tr("network.link_advert_hint"))
        live_hint.setWordWrap(True)
        live_hint.setObjectName("Metadata")
        live_layout.addWidget(live_hint)
        live_form = QFormLayout()
        self.link_advert_enabled = QCheckBox(tr("network.link_advert_enabled"))
        self.link_advert_enabled.setChecked(
            self.runtime.config.link_advert_enabled
        )
        self.link_advert_interval = QSpinBox()
        self.link_advert_interval.setRange(1, 1440)
        self.link_advert_interval.setSuffix(" min")
        self.link_advert_interval.setValue(
            max(1, int(self.runtime.config.link_advert_interval / 60))
        )
        live_form.addRow(self.link_advert_enabled)
        live_form.addRow(
            tr("network.link_advert_interval"), self.link_advert_interval
        )
        live_layout.addLayout(live_form)
        self.live_links = RowTable(0, 7)
        self.live_links.setHorizontalHeaderLabels(
            [
                tr("network.link_owner"),
                tr("network.link_neighbor"),
                tr("network.link_reciprocal"),
                tr("network.link_quality"),
                tr("network.age"),
                tr("network.discovery_expires"),
                tr("network.link_last_sender"),
            ]
        )
        live_header = self.live_links.horizontalHeader()
        live_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        live_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 7):
            live_header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        live_layout.addWidget(self.live_links, 1)
        self.live_status = QLabel()
        self.live_status.setWordWrap(True)
        self.live_status.setObjectName("Metadata")
        live_layout.addWidget(self.live_status)
        live_actions = QHBoxLayout()
        advertise = QPushButton(tr("network.link_advert_now"))
        advertise.setObjectName("primaryAction")
        advertise.clicked.connect(self._advertise_live_links)
        clear_live = QPushButton(tr("network.link_advert_clear"))
        clear_live.clicked.connect(self._clear_live_topology)
        live_actions.addWidget(advertise)
        live_actions.addStretch()
        live_actions.addWidget(clear_live)
        live_layout.addLayout(live_actions)
        self.discovery_sections.addTab(
            live_page, tr("network.discovery_live_tab")
        )
        self.discovery_sections.addTab(
            settings_page, tr("network.discovery_settings_tab")
        )
        layout.addWidget(self.discovery_sections, 1)
        layout.addLayout(settings_row)
        return page

    @staticmethod
    def _callsign_list(text: str) -> list[str]:
        return [
            item.strip().upper()
            for item in re.split(r"[,;\s]+", text or "")
            if item.strip()
        ]

    def _save_discovery_settings(self) -> None:
        config = self.runtime.config
        config.discovery_mode = self.discovery_mode.currentData()
        config.discovery_forward = self.discovery_forward.isChecked()
        config.discovery_ttl = self.discovery_ttl.value()
        config.discovery_route_lifetime = float(self.discovery_lifetime.value() * 60)
        config.discovery_frame_budget = self.discovery_budget.value()
        config.discovery_allowlist = self._callsign_list(
            self.discovery_allowlist.text()
        )
        config.discovery_denylist = self._callsign_list(
            self.discovery_denylist.text()
        )
        config.discovery_auto_use = self.discovery_auto_use.isChecked()
        config.link_advert_enabled = self.link_advert_enabled.isChecked()
        config.link_advert_interval = float(self.link_advert_interval.value() * 60)
        config.save()
        self.runtime.operations.apply_network_settings()
        self.runtime.events.publish(
            tr("network.discovery_saved"), source="discovery"
        )
        self.refresh()

    def _start_discovery(self) -> None:
        destination = self.discovery_destination.text().strip().upper()
        if not destination:
            QMessageBox.warning(
                self, tr("network.discovery"), tr("network.route_required")
            )
            return
        pending = self.runtime.operations.discover_route(destination)
        if pending is None:
            QMessageBox.information(
                self,
                tr("network.discovery"),
                tr("network.discovery_not_started"),
            )
            return
        self.refresh()

    def _selected_discovered_destination(self) -> str:
        row = self.discovery_routes.currentRow()
        item = self.discovery_routes.item(row, 0) if row >= 0 else None
        return item.text() if item is not None else ""

    def _approve_discovered_route(self) -> None:
        destination = self._selected_discovered_destination()
        if not destination:
            return
        route = self.runtime.operations.approve_discovered_route(destination)
        if route is None:
            return
        self.runtime.events.publish(
            tr(
                "network.discovery_route_approved",
                destination=route.destination,
                next_hop=route.next_hop,
            ),
            source="discovery",
        )
        self.refresh()

    def _clear_discovered_routes(self) -> None:
        self.runtime.operations.clear_discovered_routes()
        self.refresh()

    def _advertise_live_links(self) -> None:
        sent = self.runtime.operations.advertise_live_links()
        self.runtime.events.publish(
            tr("network.link_advert_sent", count=sent), source="discovery"
        )
        self.refresh()

    def _clear_live_topology(self) -> None:
        self.runtime.operations.clear_live_topology()
        self.refresh()

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

        discovery = self.runtime.operations.net.discovery
        routes = discovery.routes.routes(now, include_expired=True)
        self.discovery_routes.setRowCount(len(routes))
        for row, route in enumerate(routes):
            remaining = max(0.0, route.expires_at - now)
            state = (
                tr("network.discovery_state_expired")
                if not route.active(now)
                else tr("network.discovery_state_degraded")
                if route.failures
                else tr("network.discovery_state_live")
            )
            values = (
                route.destination,
                route.next_hop,
                str(route.hops),
                str(route.metric),
                f"{max(0.0, now - route.learned_at):.0f} s",
                f"{remaining / 60:.0f} min",
                tr("common.yes") if route.approved else tr("common.no"),
                state,
                route.source,
            )
            for column, value in enumerate(values):
                self.discovery_routes.setItem(
                    row, column, QTableWidgetItem(value)
                )

        pending = list(discovery.pending.values())
        self.discovery_pending.setRowCount(len(pending))
        for row, query in enumerate(pending):
            state = (
                tr("network.discovery_state_settling")
                if query.best_route is not None
                else tr("network.discovery_state_querying")
            )
            values = (
                f"{query.query_id:08X}",
                query.destination,
                str(query.round_ttl),
                query.context,
                state,
            )
            for column, value in enumerate(values):
                self.discovery_pending.setItem(
                    row, column, QTableWidgetItem(value)
                )
        recent = list(discovery.events)[:5]
        self.discovery_recent.setText(
            "\n".join(
                f"• {event.kind}: {event.source} → "
                f"{event.destination or '—'} · {event.detail}"
                for event in recent
            )
            if recent
            else tr("network.discovery_no_activity")
        )
        warnings = []
        if self.runtime.config.discovery_forward and not self.runtime.config.auto_relay:
            warnings.append(tr("network.discovery_relay_warning"))
        if (
            self.runtime.config.discovery_auto_use
            and discovery.mode != "assisted"
        ):
            warnings.append(tr("network.discovery_auto_inactive"))
        if (
            self.runtime.config.link_advert_enabled
            and discovery.mode != "assisted"
        ):
            warnings.append(tr("network.link_advert_monitor_warning"))
        self.discovery_status.setText(
            tr(
                "network.discovery_status",
                mode=tr(f"network.discovery_mode_{discovery.mode}"),
                routes=len([route for route in routes if route.active(now)]),
                pending=len(pending),
            )
            + (f"  {' '.join(warnings)}" if warnings else "")
        )

        links = discovery.live_topology.links(now, include_expired=True)
        self.live_links.setRowCount(len(links))
        reciprocal_count = 0
        for row, link in enumerate(links):
            reciprocal = discovery.live_topology.reciprocal(link, now)
            if reciprocal:
                reciprocal_count += 1
            values = (
                link.owner,
                link.neighbor,
                tr("common.yes") if reciprocal else tr("common.no"),
                str(link.penalty),
                f"{max(0.0, now - link.learned_at):.0f} s",
                f"{max(0.0, link.expires_at - now) / 60:.0f} min",
                link.last_sender,
            )
            for column, value in enumerate(values):
                self.live_links.setItem(row, column, QTableWidgetItem(value))
        live_routes = [
            route
            for route in routes
            if route.source == "link-advert" and route.active(now)
        ]
        self.live_status.setText(
            tr(
                "network.link_advert_status",
                observations=len([link for link in links if link.active(now)]),
                reciprocal=reciprocal_count // 2,
                routes=len(live_routes),
            )
        )
