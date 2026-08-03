"""Wizard for importing or building one shared radio-network topology."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from ..i18n import dual
from ..routing import Link, Topology, read_topology_csv
from .inputs import FrequencySpinBox, RowTable, UppercaseLineEdit


TOPOLOGY_FILTER = "Guardian topology CSV (*.csv);;CSV (*.csv);;*"


def topology_warning_text(warning: str) -> str:
    if warning == "station callsign is not configured":
        return dual(warning, "Není nastavena volací značka stanice")
    if warning.endswith(" is not present in the topology"):
        callsign = warning.removesuffix(" is not present in the topology")
        return dual(warning, f"{callsign} není součástí topologie")
    if " is not reachable from " in warning:
        destination, source = warning.split(" is not reachable from ", 1)
        return dual(warning, f"{destination} není dosažitelná z {source}")
    if warning.startswith("next hop ") and " has not been heard by " in warning:
        rest = warning.removeprefix("next hop ")
        hop, source = rest.split(" has not been heard by ", 1)
        return dual(warning, f"Další bod {hop} zatím stanice {source} neslyšela")
    return warning


class TopologyEditor(QWidget):
    topology_changed = Signal()

    def __init__(self, topology: Topology, parent=None) -> None:
        super().__init__(parent)
        self.topology = Topology(topology.links)
        layout = QVBoxLayout(self)
        self.table = RowTable(0, 9)
        self.table.setHorizontalHeaderLabels(
            [
                dual("Station A", "Stanice A"),
                dual("Station B", "Stanice B"),
                dual("Direction", "Směr"),
                dual("Calling channel", "Volací kanál"),
                dual("Mode", "Režim"),
                dual("Working channel", "Pracovní kanál"),
                dual("Working mode", "Pracovní režim"),
                dual("Cost", "Cena"),
                dual("Enabled", "Povoleno"),
            ]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 9):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._load_selected)
        layout.addWidget(self.table, 1)

        form = QFormLayout()
        self.station_a = UppercaseLineEdit()
        self.station_b = UppercaseLineEdit()
        self.direction = QComboBox()
        self.direction.addItem(dual("Both directions", "Oba směry"), "both")
        self.direction.addItem(dual("A to B", "A do B"), "a_to_b")
        self.direction.addItem(dual("B to A", "B do A"), "b_to_a")
        self.frequency = FrequencySpinBox()
        self.mode = QComboBox()
        self.mode.addItem("—", "")
        self.mode.addItem("FM", "FM")
        self.mode.addItem("USB", "USB")
        self.working_frequency = FrequencySpinBox()
        self.working_mode = QComboBox()
        self.working_mode.addItem("—", "")
        self.working_mode.addItem("FM", "FM")
        self.working_mode.addItem("USB", "USB")
        self.cost = QDoubleSpinBox()
        self.cost.setRange(0.01, 1_000_000.0)
        self.cost.setDecimals(2)
        self.cost.setValue(1.0)
        self.enabled = QCheckBox(dual("Link is available", "Linka je dostupná"))
        self.enabled.setChecked(True)
        form.addRow(dual("Station A", "Stanice A"), self.station_a)
        form.addRow(dual("Station B", "Stanice B"), self.station_b)
        form.addRow(dual("Direction", "Směr"), self.direction)
        form.addRow(dual("Calling frequency", "Volací frekvence"), self.frequency)
        form.addRow(dual("Mode", "Režim"), self.mode)
        form.addRow(
            dual("VARA working frequency", "Pracovní frekvence VARA"),
            self.working_frequency,
        )
        form.addRow(dual("Working mode", "Pracovní režim"), self.working_mode)
        form.addRow(dual("Link cost", "Cena linky"), self.cost)
        form.addRow(self.enabled)
        layout.addLayout(form)

        actions = QHBoxLayout()
        add = QPushButton(dual("Add or replace link", "Přidat nebo nahradit linku"))
        add.setObjectName("primaryAction")
        add.clicked.connect(self._save_link)
        remove = QPushButton(dual("Remove selected", "Odstranit vybranou"))
        remove.clicked.connect(self._remove_selected)
        import_button = QPushButton(dual("Import topology CSV…", "Importovat topologii CSV…"))
        import_button.clicked.connect(self.import_csv)
        actions.addWidget(add)
        actions.addWidget(remove)
        actions.addStretch()
        actions.addWidget(import_button)
        layout.addLayout(actions)
        self.refresh()

    def _selected_link(self) -> Link | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.topology.links):
            return None
        return self.topology.links[row]

    def _load_selected(self) -> None:
        link = self._selected_link()
        if link is None:
            return
        self.station_a.setText(link.station_a)
        self.station_b.setText(link.station_b)
        index = self.direction.findData(link.direction)
        if index >= 0:
            self.direction.setCurrentIndex(index)
        self.frequency.setValue(link.freq_hz)
        index = self.mode.findData(link.mode)
        if index >= 0:
            self.mode.setCurrentIndex(index)
        self.working_frequency.setValue(link.working_freq_hz)
        index = self.working_mode.findData(link.working_mode)
        if index >= 0:
            self.working_mode.setCurrentIndex(index)
        self.cost.setValue(link.cost)
        self.enabled.setChecked(link.enabled)

    def _save_link(self) -> None:
        link = Link(
            self.station_a.text(),
            self.station_b.text(),
            self.direction.currentData(),
            self.frequency.value(),
            self.mode.currentData(),
            self.working_frequency.value(),
            self.working_mode.currentData(),
            self.cost.value(),
            self.enabled.isChecked(),
        ).normalised()
        if link.problems():
            QMessageBox.warning(
                self,
                dual("Invalid link", "Neplatná linka"),
                "; ".join(link.problems()),
            )
            return
        self.topology.add(link)
        self.refresh()
        self.topology_changed.emit()

    def _remove_selected(self) -> None:
        link = self._selected_link()
        if link is None:
            return
        self.topology.remove(link.key)
        self.refresh()
        self.topology_changed.emit()

    def import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            dual("Import shared topology", "Import sdílené topologie"),
            "",
            TOPOLOGY_FILTER,
        )
        if not path:
            return
        try:
            report = read_topology_csv(path)
        except OSError as exc:
            QMessageBox.warning(self, dual("Import failed", "Import selhal"), str(exc))
            return
        if not report.topology.links:
            QMessageBox.warning(
                self,
                dual("Import failed", "Import selhal"),
                "\n".join(report.problems),
            )
            return
        self.topology = report.topology
        self.refresh()
        self.topology_changed.emit()
        if report.problems:
            QMessageBox.information(
                self,
                dual("Imported with warnings", "Importováno s upozorněními"),
                "\n".join(report.problems[:12]),
            )

    def refresh(self) -> None:
        links = self.topology.links
        self.table.setRowCount(len(links))
        for row, link in enumerate(links):
            values = (
                link.station_a,
                link.station_b,
                link.direction,
                self.frequency.textFromValue(link.freq_hz) if link.freq_hz else "",
                link.mode,
                (
                    self.working_frequency.textFromValue(link.working_freq_hz)
                    if link.working_freq_hz
                    else ""
                ),
                link.working_mode,
                f"{link.cost:g}",
                dual("yes", "ano") if link.enabled else dual("no", "ne"),
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))


class _IdentityPage(QWizardPage):
    def __init__(self, callsign: str, parent=None) -> None:
        super().__init__(parent)
        self.setTitle(dual("Local station", "Místní stanice"))
        layout = QVBoxLayout(self)
        text = QLabel(
            dual(
                "One topology is shared by the whole network. Guardian derives a "
                "different route table from the configured callsign on each PC.",
                "Jedna topologie je společná pro celou síť. Guardian na každém PC "
                "odvodí jinou tabulku tras podle nastavené volací značky.",
            )
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        form = QFormLayout()
        self.callsign = QLineEdit(callsign.strip().upper())
        self.callsign.setReadOnly(True)
        form.addRow(dual("Configured callsign", "Nastavená značka"), self.callsign)
        layout.addLayout(form)
        if not callsign.strip():
            missing = QLabel(
                dual(
                    "Close the wizard and configure the station callsign first.",
                    "Zavřete průvodce a nejdříve nastavte volací značku stanice.",
                )
            )
            missing.setWordWrap(True)
            missing.setObjectName("WarningText")
            layout.addWidget(missing)
        layout.addStretch()

    def isComplete(self) -> bool:
        return bool(self.callsign.text().strip())


class _EditorPage(QWizardPage):
    def __init__(self, topology: Topology, parent=None) -> None:
        super().__init__(parent)
        self.setTitle(dual("Stations and links", "Stanice a linky"))
        self.setSubTitle(
            dual(
                "Import one shared CSV or enter links manually. Direction describes "
                "real RF reachability; cost chooses between alternatives.",
                "Importujte jeden společný CSV nebo zadejte linky ručně. Směr popisuje "
                "skutečný RF dosah; cena rozhoduje mezi alternativami.",
            )
        )
        layout = QVBoxLayout(self)
        self.editor = TopologyEditor(topology)
        self.editor.topology_changed.connect(self.completeChanged.emit)
        layout.addWidget(self.editor)

    def isComplete(self) -> bool:
        return bool(self.editor.topology.links)


class _PreviewPage(QWizardPage):
    def __init__(self, wizard: "TopologyWizard") -> None:
        super().__init__(wizard)
        self.owner = wizard
        self.setTitle(dual("Generated local routes", "Odvozené místní trasy"))
        layout = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.routes = RowTable(0, 5)
        self.routes.setHorizontalHeaderLabels(
            [
                dual("Destination", "Cíl"),
                dual("Next hop", "Další bod"),
                dual("Backup", "Záloha"),
                dual("Calling channel", "Volací kanál"),
                dual("Mode", "Režim"),
            ]
        )
        self.routes.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.routes, 1)
        self.warnings = QLabel()
        self.warnings.setWordWrap(True)
        self.warnings.setObjectName("Metadata")
        layout.addWidget(self.warnings)

    def initializePage(self) -> None:
        topology = self.owner.topology
        callsign = self.owner.callsign
        routes = topology.derive_routes(callsign)
        self.summary.setText(
            dual(
                f"{len(topology.nodes)} stations, {len(topology.links)} links; "
                f"{len(routes)} routes will be generated for {callsign}.",
                f"{len(topology.nodes)} stanic, {len(topology.links)} linek; "
                f"pro {callsign} bude odvozeno {len(routes)} tras.",
            )
        )
        frequency = FrequencySpinBox()
        self.routes.setRowCount(len(routes))
        for row, route in enumerate(routes):
            values = (
                route.destination,
                route.preferred or route.destination,
                route.backup,
                frequency.textFromValue(route.freq_hz) if route.freq_hz else "",
                route.mode,
            )
            for column, value in enumerate(values):
                self.routes.setItem(row, column, QTableWidgetItem(value))
        warnings = topology.warnings(callsign, self.owner.heard_callsigns)
        self.warnings.setText(
            "\n".join(f"• {topology_warning_text(warning)}" for warning in warnings)
            if warnings
            else dual("No topology warnings.", "Topologie je bez upozornění.")
        )


class TopologyWizard(QWizard):
    def __init__(
        self,
        topology: Topology,
        callsign: str,
        heard_callsigns: set[str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(dual("Guardian network builder", "Sestavovač sítě Guardian"))
        self.setMinimumSize(980, 720)
        self.heard_callsigns = heard_callsigns or set()
        self.identity_page = _IdentityPage(callsign, self)
        self.editor_page = _EditorPage(topology, self)
        self.preview_page = _PreviewPage(self)
        self.addPage(self.identity_page)
        self.addPage(self.editor_page)
        self.addPage(self.preview_page)

    @property
    def callsign(self) -> str:
        return self.identity_page.callsign.text().strip().upper()

    @property
    def topology(self) -> Topology:
        return self.editor_page.editor.topology
