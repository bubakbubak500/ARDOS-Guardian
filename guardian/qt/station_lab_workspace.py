"""Operator-facing Guardian Pair AutoTune and radio-path characterizer."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..i18n import dual
from ..station_lab import CalibrationState


class StationLabWorkspace(QWidget):
    """One deliberate action up front; technical choices live in the report."""

    def __init__(self, runtime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self._last_session = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        title = QLabel(dual("Station test & AutoTune", "Test stanice a AutoTune"))
        title.setObjectName("PanelHeader")
        outer.addWidget(title)
        intro = QLabel(dual(
            "Guardian tests ten output levels using the currently selected waveform "
            "and modulation. The peer returns one best level, Guardian saves it, "
            "then both stations repeat the same sweep in the reverse direction.",
            "Guardian otestuje deset úrovní aktuálně vybraného waveformu a modulace. "
            "Protistanice vrátí jednu nejlepší úroveň, Guardian ji uloží a obě "
            "stanice zopakují stejný sweep opačným směrem.",
        ))
        intro.setWordWrap(True)
        intro.setObjectName("Metadata")
        outer.addWidget(intro)

        setup = QFrame()
        setup.setObjectName("SurfaceCard")
        form = QFormLayout(setup)
        self.peer = QLineEdit()
        self.peer.setMaxLength(12)
        self.peer.setPlaceholderText("OK2IPW")
        form.addRow(dual("Other station", "Protistanice"), self.peer)
        outer.addWidget(setup)

        actions = QHBoxLayout()
        self.start = QPushButton(dual("Call and start", "Zavolat a spustit"))
        self.start.setObjectName("PrimaryAction")
        self.start.clicked.connect(self._start)
        actions.addWidget(self.start)
        self.cancel = QPushButton(dual("Cancel safely", "Bezpečně zrušit"))
        self.cancel.clicked.connect(self.runtime.operations.cancel_station_calibration)
        self.cancel.hide()
        actions.addWidget(self.cancel)
        actions.addStretch(1)
        outer.addLayout(actions)

        self.offer = QFrame()
        self.offer.setObjectName("AttentionCard")
        offer_layout = QVBoxLayout(self.offer)
        self.offer_text = QLabel()
        self.offer_text.setWordWrap(True)
        offer_layout.addWidget(self.offer_text)
        offer_actions = QHBoxLayout()
        self.accept = QPushButton(dual("Accept test", "Přijmout test"))
        self.accept.setObjectName("PrimaryAction")
        self.accept.clicked.connect(self.runtime.operations.accept_station_calibration)
        self.reject = QPushButton(dual("Refuse", "Odmítnout"))
        self.reject.clicked.connect(self.runtime.operations.reject_station_calibration)
        offer_actions.addWidget(self.accept)
        offer_actions.addWidget(self.reject)
        offer_actions.addStretch(1)
        offer_layout.addLayout(offer_actions)
        self.offer.hide()
        outer.addWidget(self.offer)

        progress_card = QFrame()
        progress_card.setObjectName("SurfaceCard")
        progress_layout = QVBoxLayout(progress_card)
        self.state = QLabel(dual("Ready", "Připraveno"))
        self.state.setObjectName("SectionTitle")
        self.detail = QLabel(dual(
            "Start the control channel, enter the peer, then begin.",
            "Spusťte řídicí kanál, zadejte protistanici a začněte.",
        ))
        self.detail.setWordWrap(True)
        self.detail.setObjectName("Metadata")
        self.segments = []
        segment_row = QHBoxLayout()
        segment_row.setSpacing(4)
        for index in range(1, 11):
            segment = QLabel(str(index))
            segment.setAlignment(Qt.AlignmentFlag.AlignCenter)
            segment.setMinimumHeight(24)
            segment_row.addWidget(segment, 1)
            self.segments.append(segment)
        progress_layout.addWidget(self.state)
        progress_layout.addWidget(self.detail)
        progress_layout.addLayout(segment_row)
        outer.addWidget(progress_card)

        self.report_button = QPushButton(dual(
            "Review final report…", "Zkontrolovat finální report…"
        ))
        self.report_button.clicked.connect(self._show_report)
        self.report_button.hide()
        outer.addWidget(self.report_button, alignment=Qt.AlignmentFlag.AlignLeft)
        outer.addStretch(1)

    def _start(self) -> None:
        peer = self.peer.text().strip().upper()
        if not peer:
            QMessageBox.warning(
                self, dual("Station test", "Test stanice"),
                dual("Enter the other station's callsign.",
                     "Zadejte volací značku protistanice."),
            )
            return
        answer = QMessageBox.question(
            self, dual("Start on-air station test?", "Spustit test stanice on-air?"),
            dual(
                f"Guardian will call {peer} and, after they accept, key this radio "
                "for a ten-level Quick Tune using the currently selected waveform "
                "and modulation. The selected Guardian volume is saved automatically.",
                f"Guardian zavolá {peer} a po přijetí zaklíčuje toto rádio pro "
                "desetistupňové rychlé ladění aktuálního waveformu a modulace. "
                "Vybraná hlasitost Guardianu se uloží automaticky.",
            ),
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Ok:
            return
        if not self.runtime.operations.start_station_calibration(
            peer, "quick"
        ):
            QMessageBox.warning(
                self, dual("Station test", "Test stanice"),
                self.runtime.operations.station_lab.error,
            )
        self.refresh()

    def refresh(self) -> None:
        status = self.runtime.operations.station_lab
        self.offer.setVisible(status.pending_offer)
        self.offer_text.setText(status.message)
        active = status.state in {
            CalibrationState.OFFERING.value,
            CalibrationState.PREPARING.value,
            CalibrationState.MEASURING.value,
            CalibrationState.WAITING_REPORT.value,
        }
        self.start.setEnabled(not active and not status.pending_offer)
        self.peer.setEnabled(not active and not status.pending_offer)
        self.cancel.setVisible(active)
        progress = max(0, min(10, int(status.progress)))
        for index, segment in enumerate(self.segments, 1):
            if index <= progress:
                colours = ("#159a70", "#ffffff")
            elif index == progress + 1 and active:
                colours = ("#2879c7", "#ffffff")
            else:
                colours = ("#263442", "#9eb0c0")
            segment.setStyleSheet(
                f"background: {colours[0]}; color: {colours[1]}; "
                "border-radius: 4px; padding: 3px;"
            )
        label = {
            "idle": dual("Ready", "Připraveno"),
            "offering": dual("Calling the peer", "Volám protistanici"),
            "waiting_approval": dual("Approval required", "Je potřeba souhlas"),
            "preparing": dual("Preparing", "Připravuji"),
            "measuring": dual("Measuring on air", "Měřím on-air"),
            "waiting_report": dual("Waiting for measurements", "Čekám na měření"),
            "complete": dual("Measurement complete", "Měření dokončeno"),
            "cancelled": dual("Cancelled safely", "Bezpečně zrušeno"),
            "failed": dual("Test failed", "Test selhal"),
        }.get(status.state, status.state)
        self.state.setText(label)
        detail = status.current or status.error or status.message
        if detail:
            self.detail.setText(detail)
        self.report_button.setVisible(
            status.report is not None and status.state == CalibrationState.COMPLETE.value
        )
        if status.session_id and status.session_id != self._last_session:
            self._last_session = status.session_id

    def _show_report(self) -> None:
        report = self.runtime.operations.station_lab.report
        if report is not None:
            StationLabReportDialog(self.runtime, report, self).exec()
            self.refresh()


class StationLabReportDialog(QDialog):
    """Raw evidence plus the only place where a proposal can be applied."""

    def __init__(self, runtime, report, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.report = report
        self.setWindowTitle(dual("Station-test report", "Report testu stanice"))
        self.resize(920, 620)
        outer = QVBoxLayout(self)

        recommendation = report.recommendation
        summary = QLabel()
        summary.setWordWrap(True)
        if recommendation is None:
            summary.setText(dual(
                "The peer could not identify a byte-valid, unclipped level. "
                "The previous Guardian volume was kept.",
                "Protistanice nenašla úroveň s platným rámcem bez ořezu. "
                "Původní hlasitost Guardianu zůstala zachována.",
            ))
        else:
            summary.setText(dual(
                f"Selected and saved automatically: level "
                f"{recommendation.tx_scale * 100:.0f}% for "
                f"{recommendation.waveform} {recommendation.bandwidth}, "
                f"MCS{recommendation.mcs}.",
                f"Automaticky vybráno a uloženo: úroveň "
                f"{recommendation.tx_scale * 100:.0f} % pro "
                f"{recommendation.waveform} {recommendation.bandwidth}, "
                f"MCS{recommendation.mcs}.",
            ))
        outer.addWidget(summary)

        columns = [
            dual("Step", "Krok"), dual("Guardian volume", "Hlasitost Guardianu"),
            dual("Result", "Výsledek"), "SNR", "EVM", "Peak",
            dual("Sync", "Sync"), "CFO",
        ]
        table = QTableWidget(len(report.results), len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for row, item in enumerate(report.results):
            selected = (
                recommendation is not None
                and abs(item.tx_scale - recommendation.tx_scale) < 1e-6
            )
            if selected:
                outcome = dual("SELECTED", "VYBRÁNO")
            elif item.error.startswith("tested; peer selected"):
                outcome = dual("tested", "otestováno")
            elif item.frame_ok and item.safe:
                outcome = dual("valid", "platné")
            elif item.header_ok:
                outcome = dual("CRC error", "chyba CRC")
            else:
                outcome = dual("not decoded", "nedekódováno")
            values = (
                str(item.sequence + 1), f"{item.tx_scale * 100:.0f}%", outcome,
                "—" if item.snr_db is None else f"{item.snr_db:.1f} dB",
                "—" if item.evm_rms is None else f"{item.evm_rms * 100:.1f}%",
                "—" if item.audio_peak is None else f"{item.audio_peak:.3f}",
                "—" if item.sync_confidence is None else f"{item.sync_confidence * 100:.0f}%",
                "—" if item.cfo_hz is None else f"{item.cfo_hz:.0f} Hz",
            )
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        outer.addWidget(table, 1)

        files = QLabel(dual(
            f"Raw JSON: {self.runtime.operations.station_lab.report_json}\n"
            f"CSV: {self.runtime.operations.station_lab.report_csv}",
            f"Surový JSON: {self.runtime.operations.station_lab.report_json}\n"
            f"CSV: {self.runtime.operations.station_lab.report_csv}",
        ))
        files.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        files.setObjectName("Metadata")
        outer.addWidget(files)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
