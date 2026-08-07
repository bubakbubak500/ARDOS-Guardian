"""Network routes, shared topology and heard-stations workspace."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
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

from dataclasses import dataclass

from ..i18n import dual, tr
from ..routing import (
    DISCOVERY_ASSISTED,
    DISCOVERY_MODES,
    Route,
    Topology,
    locator_distance_bearing,
    write_topology_csv,
)
from .inputs import FrequencySpinBox, RowTable, UppercaseLineEdit
from .runtime import ShellRuntime
from .topology_wizard import TOPOLOGY_FILTER, TopologyWizard, topology_warning_text


@dataclass
class _RouteRow:
    """One line of the Routes table, planned or observed.

    Only ``planned`` rows exist in the persisted route table. Observed rows are
    a read-only view of volatile evidence -- they expire, they vanish on a
    restart, and they reach `routes.json` only when the operator presses
    "Save as manual route".
    """

    destination: str
    preferred: str          # "" means "call the destination directly"
    freq_hz: int
    mode: str
    source_label: str
    expires: str
    planned: bool


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
        # Five pages of Czech labels can outgrow a narrow window. Eliding keeps
        # every page one click away; scroll buttons would hide the last one,
        # which is exactly the page an operator is least likely to go looking
        # for.
        self.tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs.tabBar().setUsesScrollButtons(False)
        # Planned network first, then what is actually on the air, then the one
        # experiment. Nothing here nests a second row of tabs inside a tab.
        self.tabs.addTab(self._routes_page(), tr("network.routes"))
        self.tabs.addTab(self._heard_page(), tr("network.heard"))
        self.tabs.addTab(self._topology_page(), tr("network.topology"))
        self.tabs.addTab(self._discovery_page(), tr("network.discovery"))
        self.tabs.addTab(self._live_topology_page(), tr("network.live_topology"))
        outer.addWidget(self.tabs, 1)
        self.refresh()

    def _routes_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.routes_table = RowTable(0, 9)
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
                tr("network.route_expires"),
            ]
        )
        self.routes_table.itemSelectionChanged.connect(self._load_selected_route)
        header = self.routes_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (2, 3, 4, 5, 6, 7, 8):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.routes_table, 1)
        # The precedence rule is short enough to state, and without it a
        # topology row for a station that is also heard looks like a
        # contradiction rather than a hop that simply is not needed.
        self.routes_precedence = QLabel(tr("network.route_precedence"))
        self.routes_precedence.setWordWrap(True)
        self.routes_precedence.setObjectName("Metadata")
        layout.addWidget(self.routes_precedence)

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
        self.promote_route = QPushButton(tr("network.route_promote"))
        self.promote_route.setToolTip(tr("network.route_promote_hint"))
        self.promote_route.clicked.connect(self._promote_selected_route)
        remove = QPushButton(tr("network.remove"))
        remove.clicked.connect(self._remove_route)
        actions.addWidget(add)
        actions.addWidget(self.promote_route)
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
        heard_header = self.heard_table.horizontalHeader()
        # "Poslední S/N (odhad)" is wider than the default column, and a clipped
        # header is a column the operator has to guess at.
        for column in range(7):
            heard_header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        heard_header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.heard_table, 1)
        # An empty table is the normal state of a quiet channel and the normal
        # state of a channel that was never started. Saying which one it is here
        # is the difference between waiting and troubleshooting.
        self.heard_status = QLabel()
        self.heard_status.setWordWrap(True)
        layout.addWidget(self.heard_status)
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

        mode_row = QHBoxLayout()
        self.discovery_mode = QComboBox()
        for mode in DISCOVERY_MODES:
            self.discovery_mode.addItem(tr(f"network.discovery_mode_{mode}"), mode)
        index = self.discovery_mode.findData(self.runtime.config.discovery_mode)
        self.discovery_mode.setCurrentIndex(max(0, index))
        self.discovery_auto_use = QCheckBox(tr("network.discovery_auto_use"))
        self.discovery_auto_use.setChecked(self.runtime.config.discovery_auto_use)
        mode_row.addWidget(QLabel(tr("network.discovery_mode")))
        mode_row.addWidget(self.discovery_mode)
        mode_row.addSpacing(16)
        mode_row.addWidget(self.discovery_auto_use)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        query_row = QHBoxLayout()
        self.discovery_destination = UppercaseLineEdit()
        self.discovery_destination.setPlaceholderText("S1 / OK1AAA")
        self.discovery_query = QPushButton(tr("network.discovery_start"))
        self.discovery_query.setObjectName("primaryAction")
        self.discovery_query.clicked.connect(self._start_discovery)
        query_row.addWidget(QLabel(tr("network.destination")))
        query_row.addWidget(self.discovery_destination, 1)
        query_row.addWidget(self.discovery_query)
        layout.addLayout(query_row)

        self.discovery_status = QLabel()
        self.discovery_status.setWordWrap(True)
        layout.addWidget(self.discovery_status)

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
        layout.addWidget(self.discovery_routes, 1)

        route_actions = QHBoxLayout()
        approve = QPushButton(tr("network.discovery_approve"))
        approve.clicked.connect(self._approve_discovered_route)
        clear = QPushButton(tr("network.discovery_clear"))
        clear.clicked.connect(self._clear_discovered_routes)
        route_actions.addWidget(approve)
        route_actions.addStretch()
        route_actions.addWidget(clear)
        layout.addLayout(route_actions)

        self.discovery_pending = RowTable(0, 5)
        self.discovery_pending.setMaximumHeight(110)
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
        layout.addWidget(self.discovery_pending)
        self.discovery_recent = QLabel()
        self.discovery_recent.setWordWrap(True)
        self.discovery_recent.setObjectName("Metadata")
        layout.addWidget(self.discovery_recent)
        layout.addLayout(self._save_row())
        return page

    def _save_button(self) -> QPushButton:
        """One save action per page, writing every discovery setting."""
        save = QPushButton(tr("network.discovery_save"))
        save.clicked.connect(self._save_discovery_settings)
        return save

    def _save_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self._save_button())
        return row

    def _live_topology_page(self) -> QWidget:
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
        live_layout.addWidget(self.live_status)
        live_actions = QHBoxLayout()
        self.link_advert_now = QPushButton(tr("network.link_advert_now"))
        self.link_advert_now.setObjectName("primaryAction")
        self.link_advert_now.clicked.connect(self._advertise_live_links)
        clear_live = QPushButton(tr("network.link_advert_clear"))
        clear_live.clicked.connect(self._clear_live_topology)
        live_actions.addWidget(self.link_advert_now)
        live_actions.addStretch()
        live_actions.addWidget(clear_live)
        # Saving belongs beside the other actions of this page, not on a row of
        # its own -- an extra line here costs a line of the links table.
        live_actions.addWidget(self._save_button())
        live_layout.addLayout(live_actions)
        return live_page

    @property
    def _control_active(self) -> bool:
        """Whether control audio is actually running, not what was configured."""
        return self.runtime.operations.audio_transport is not None

    def _discovery_blockers(self) -> list[str]:
        """Why the discovery plane cannot act, in the order worth fixing.

        Read from the running engine rather than from the widgets: a setting
        that has not been saved yet has no effect on the air, and a button that
        pretends otherwise is how an operator ends up believing the radio is
        broken.
        """
        reasons = []
        if not self._control_active:
            reasons.append(
                tr("network.control_off_notice", action=tr("shell.start_control"))
            )
        if self.runtime.operations.net.discovery.mode != DISCOVERY_ASSISTED:
            reasons.append(tr("network.discovery_off_notice"))
        return reasons

    def _sync_discovery_controls(self) -> None:
        """Let every action say up front whether it can do anything."""
        blockers = self._discovery_blockers()
        self.discovery_query.setEnabled(not blockers)
        self.discovery_query.setToolTip(" ".join(blockers))
        advert_blockers = list(blockers)
        if not self.runtime.operations.net.discovery.link_advert_enabled:
            advert_blockers.append(tr("network.link_advert_disabled_notice"))
        self.link_advert_now.setEnabled(not advert_blockers)
        self.link_advert_now.setToolTip(" ".join(advert_blockers))

    def _unsaved_discovery_changes(self) -> bool:
        """Whether the pages hold a setting the station has not adopted yet."""
        config = self.runtime.config
        return (
            self.discovery_mode.currentData() != config.discovery_mode
            or self.discovery_auto_use.isChecked() != config.discovery_auto_use
            or self.link_advert_enabled.isChecked() != config.link_advert_enabled
            or self.link_advert_interval.value() * 60
            != int(config.link_advert_interval)
        )

    def _save_discovery_settings(self) -> None:
        """Adopt both discovery pages at once; limits live in Station settings."""
        config = self.runtime.config
        config.discovery_mode = self.discovery_mode.currentData()
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

    def _selected_route_row(self) -> _RouteRow | None:
        row = self.routes_table.currentRow()
        rows = getattr(self, "_route_rows", [])
        return rows[row] if 0 <= row < len(rows) else None

    def _load_selected_route(self) -> None:
        # Only a planned row belongs in the editor. An observed row has no
        # backup or working channel to edit and is not ours to rewrite.
        entry = self._selected_route_row()
        if entry is None or not entry.planned:
            return
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

    def _promote_selected_route(self) -> None:
        """Copy an observed or generated row into the persisted table as manual.

        The evidence itself stays volatile. This writes a new operator decision
        that happens to have been informed by it -- which is why the stored row
        is honestly labelled manual rather than keeping the source it came from.
        """
        entry = self._selected_route_row()
        if entry is None:
            return
        existing = self.runtime.routes.lookup(entry.destination)
        if existing is not None and existing.source == "manual":
            QMessageBox.information(
                self,
                tr("network.routes"),
                tr("network.route_already_manual", destination=entry.destination),
            )
            return
        self.runtime.routes.add(
            Route(
                entry.destination,
                entry.preferred,
                "",
                entry.freq_hz,
                entry.mode,
                0,
                "",
                "manual",
            )
        )
        self.runtime.routes.save()
        self.runtime.events.publish(
            tr(
                "network.route_promoted",
                destination=entry.destination,
                source=entry.source_label,
            ),
            source="network",
        )
        self.runtime.refresh()
        self.refresh()

    def _remove_route(self) -> None:
        entry = self._selected_route_row()
        if entry is not None and not entry.planned:
            QMessageBox.information(
                self,
                tr("network.routes"),
                tr("network.route_live_remove_hint"),
            )
            return
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

    def _default_channel_mode(self) -> str:
        """The mode a promoted route should carry for this control modem."""
        return "USB" if self.runtime.config.active_modem() == "mfsk16" else "FM"

    @staticmethod
    def _minutes(seconds: float) -> str:
        return f"{max(0.0, seconds) / 60:.0f} min"

    def _build_route_rows(self, now: float, heard: list, discovery) -> list[_RouteRow]:
        """Planned routes first, then observed evidence for anything they miss.

        Planned rows keep stable indices so the live rows appearing and expiring
        underneath them cannot shift the row the operator is editing.
        """
        rows = [
            _RouteRow(
                destination=route.destination,
                preferred=route.preferred,
                freq_hz=route.freq_hz,
                mode=route.mode,
                source_label=tr(f"network.source_{route.source}"),
                expires="—",
                planned=True,
            )
            for route in self.runtime.routes.routes
        ]
        seen = {row.destination for row in rows}
        # Same order _resolve_next_hop consults them in: heard beats discovered.
        for station in heard:
            if station.callsign in seen:
                continue
            seen.add(station.callsign)
            frequency = int(station.last_freq_hz or 0)
            rows.append(
                _RouteRow(
                    destination=station.callsign,
                    preferred="",
                    freq_hz=frequency,
                    mode=self._default_channel_mode() if frequency else "",
                    source_label=tr("network.source_heard"),
                    expires=self._minutes(self.runtime.heard.max_age - station.age(now)),
                    planned=False,
                )
            )
        for route in discovery.routes.routes(now):
            if route.destination in seen:
                continue
            seen.add(route.destination)
            label = tr(f"network.source_{route.source.replace('-', '_')}")
            if not route.approved:
                label = f"{label} · {tr('network.route_unapproved')}"
            rows.append(
                _RouteRow(
                    destination=route.destination,
                    preferred=(
                        "" if route.next_hop == route.destination else route.next_hop
                    ),
                    freq_hz=0,
                    mode="",
                    source_label=label,
                    expires=self._minutes(route.expires_at - now),
                    planned=False,
                )
            )
        return rows

    def _fill_routes_table(self) -> None:
        self.routes_table.setRowCount(len(self._route_rows))
        for row, entry in enumerate(self._route_rows):
            planned = (
                self.runtime.routes.lookup(entry.destination)
                if entry.planned
                else None
            )
            values = (
                entry.destination,
                entry.preferred,
                planned.backup if planned else "",
                self.frequency.textFromValue(entry.freq_hz) if entry.freq_hz else "",
                entry.mode,
                (
                    self.working_frequency.textFromValue(planned.working_freq_hz)
                    if planned and planned.working_freq_hz
                    else ""
                ),
                planned.working_mode if planned else "",
                entry.source_label,
                entry.expires,
            )
            for column, value in enumerate(values):
                self.routes_table.setItem(row, column, QTableWidgetItem(value))

    def refresh(self) -> None:
        self._sync_working_channel_visibility()
        now = time.monotonic()
        heard = self.runtime.heard.active(now)
        discovery = self.runtime.operations.net.discovery
        self._route_rows = self._build_route_rows(now, heard, discovery)
        self._fill_routes_table()

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
        self.heard_status.setText(self._heard_state_text(heard, now))

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

        routes = discovery.routes.routes(now, include_expired=True)
        # A directly heard station already resolves as a one-hop next hop, so
        # show it beside the learned routes instead of leaving the operator to
        # infer it from two separate tables.
        direct = [
            station
            for station in heard
            if not any(route.destination == station.callsign for route in routes)
        ]
        self.discovery_routes.setRowCount(len(routes) + len(direct))
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
        for offset, station in enumerate(direct):
            age = station.age(now)
            values = (
                station.callsign,
                station.callsign,
                "1",
                "-",
                f"{age:.0f} s",
                f"{max(0.0, self.runtime.heard.max_age - age) / 60:.0f} min",
                tr("common.yes"),
                tr("network.discovery_state_direct"),
                tr("network.source_heard"),
            )
            for column, value in enumerate(values):
                self.discovery_routes.setItem(
                    len(routes) + offset, column, QTableWidgetItem(value)
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
        notices = self._discovery_blockers()
        if self.runtime.config.discovery_forward and not self.runtime.config.auto_relay:
            notices.append(tr("network.discovery_relay_warning"))
        if (
            self.runtime.config.discovery_auto_use
            and discovery.mode != DISCOVERY_ASSISTED
        ):
            notices.append(tr("network.discovery_auto_inactive"))
        if self._unsaved_discovery_changes():
            notices.append(
                tr("network.discovery_unsaved", action=tr("network.discovery_save"))
            )
        self.discovery_status.setText(
            "\n".join(
                [
                    tr(
                        "network.discovery_status",
                        mode=tr(f"network.discovery_mode_{discovery.mode}"),
                        routes=len(
                            [route for route in routes if route.active(now)]
                        )
                        + len(direct),
                        pending=len(pending),
                    ),
                    *notices,
                ]
            )
        )
        self._sync_discovery_controls()

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
        # _discovery_blockers already says when the mode is the problem; the only
        # extra thing this page can be missing is its own switch.
        live_notices = list(self._discovery_blockers())
        if not discovery.link_advert_enabled:
            live_notices.append(tr("network.link_advert_disabled_notice"))
        self.live_status.setText(
            "\n".join(
                [
                    tr(
                        "network.link_advert_status",
                        observations=len(
                            [link for link in links if link.active(now)]
                        ),
                        reciprocal=reciprocal_count // 2,
                        routes=len(live_routes),
                    ),
                    *live_notices,
                ]
            )
        )

    def _heard_state_text(self, heard: list, now: float) -> str:
        """Say whether silence means a quiet channel or no channel at all."""
        if not self._control_active:
            return tr("network.control_off_notice", action=tr("shell.start_control"))
        if not heard:
            lines = [tr("network.heard_state_quiet")]
            if not self.runtime.config.beacon_enabled:
                lines.append(tr("network.heard_beacon_hint"))
            return "\n".join(lines)
        return tr(
            "network.heard_state_listening",
            count=len(heard),
            age=f"{heard[0].age(now):.0f}",
        )
