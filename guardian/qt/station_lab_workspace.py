"""Operator-facing Guardian Pair AutoTune and radio-path characterizer."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
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
            "Guardian calls one consenting peer, measures the complete radio/audio "
            "path in both directions, and proposes a safe modem drive. Nothing is "
            "applied until you approve the final report.",
            "Guardian zavolá jedné souhlasící protistanici, změří celou rádiovou a "
            "zvukovou cestu a navrhne bezpečnou úroveň modemu. Nic se nepoužije, "
            "dokud neschválíte finální report.",
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
        self.mode = QComboBox()
        self.mode.addItem(dual("Quick Tune (recommended)", "Rychlé ladění (doporučeno)"),
                          "quick")
        self.mode.addItem(dual("Full radio characterizer", "Úplný test rádia"), "full")
        form.addRow(dual("Test", "Test"), self.mode)
        self.windows_gain = QCheckBox(dual(
            "Temporarily tune Windows volume for the dedicated radio output",
            "Dočasně ladit hlasitost Windows pro vyhrazený výstup rádia",
        ))
        self.windows_gain.setChecked(bool(runtime.config.calibration_windows_gain))
        self.windows_gain.setToolTip(dual(
            "Guardian snapshots and restores the mixer on cancel, error, and completion.",
            "Guardian uloží stav mixeru a obnoví jej při zrušení, chybě i dokončení.",
        ))
        form.addRow("", self.windows_gain)
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
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        progress_layout.addWidget(self.state)
        progress_layout.addWidget(self.detail)
        progress_layout.addWidget(self.progress)
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
        full = self.mode.currentData() == "full"
        answer = QMessageBox.question(
            self, dual("Start on-air station test?", "Spustit test stanice on-air?"),
            dual(
                f"Guardian will call {peer} and, after they accept, key this radio "
                f"for a {'full' if full else 'quick'} calibration. PTT and mixer "
                "settings are restored on every exit path.",
                f"Guardian zavolá {peer} a po přijetí zaklíčuje toto rádio pro "
                f"{'úplnou' if full else 'rychlou'} kalibraci. PTT i mixer se "
                "obnoví při každém ukončení.",
            ),
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Ok:
            return
        self.runtime.config.calibration_windows_gain = self.windows_gain.isChecked()
        self.runtime.config.save()
        if not self.runtime.operations.start_station_calibration(
            peer, str(self.mode.currentData())
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
        self.mode.setEnabled(not active and not status.pending_offer)
        self.windows_gain.setEnabled(not active and not status.pending_offer)
        self.cancel.setVisible(active)
        total = max(1, int(status.total))
        self.progress.setRange(0, total)
        self.progress.setValue(min(total, int(status.progress)))
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
                "No point met the safety and reliability gates. Nothing can be applied.",
                "Žádný bod nesplnil bezpečnostní a spolehlivostní podmínky. "
                "Nelze nic použít.",
            ))
        else:
            summary.setText(dual(
                f"Recommended: {recommendation.waveform} MCS{recommendation.mcs}, "
                f"digital drive {recommendation.tx_scale * 100:.1f}%, measured "
                f"goodput {recommendation.score_bps / 1000:.2f} kbit/s.",
                f"Doporučeno: {recommendation.waveform} MCS{recommendation.mcs}, "
                f"digitální úroveň {recommendation.tx_scale * 100:.1f} %, naměřený "
                f"goodput {recommendation.score_bps / 1000:.2f} kbit/s.",
            ))
        outer.addWidget(summary)

        settings = QFrame()
        settings.setObjectName("SurfaceCard")
        form = QFormLayout(settings)
        self.scale = QDoubleSpinBox()
        self.scale.setRange(5.0, 100.0)
        self.scale.setDecimals(1)
        self.scale.setSuffix(" %")
        self.scale.setValue((recommendation.tx_scale * 100.0)
                            if recommendation is not None else 100.0)
        form.addRow(dual("Digital drive", "Digitální úroveň"), self.scale)
        self.endpoint = QDoubleSpinBox()
        self.endpoint.setRange(1.0, 100.0)
        self.endpoint.setDecimals(1)
        self.endpoint.setSuffix(" %")
        self.endpoint.setValue(
            recommendation.endpoint_volume * 100.0
            if recommendation is not None and recommendation.endpoint_volume is not None
            else 100.0
        )
        self.endpoint.setEnabled(
            recommendation is not None and recommendation.endpoint_volume is not None
        )
        form.addRow(dual("Windows endpoint", "Výstup Windows"), self.endpoint)
        outer.addWidget(settings)

        columns = [
            dual("Waveform", "Waveform"), "MCS", dual("Drive", "Úroveň"),
            dual("Windows", "Windows"), "CRC", "SNR", "EVM", "Peak",
            dual("Clipped", "Clip"), dual("Goodput", "Goodput"),
        ]
        table = QTableWidget(len(report.results), len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for row, item in enumerate(report.results):
            values = (
                item.waveform, str(item.mcs), f"{item.tx_scale * 100:.0f}%",
                "—" if item.endpoint_volume is None else f"{item.endpoint_volume * 100:.0f}%",
                dual("OK", "OK") if item.frame_ok else dual("fail", "chyba"),
                "—" if item.snr_db is None else f"{item.snr_db:.1f} dB",
                "—" if item.evm_rms is None else f"{item.evm_rms * 100:.1f}%",
                "—" if item.audio_peak is None else f"{item.audio_peak:.3f}",
                str(item.clipped_samples), f"{item.expected_goodput_bps / 1000:.2f} kbit/s",
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
        self.apply = buttons.addButton(
            dual("Apply measured profile", "Použít změřený profil"),
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self.apply.setEnabled(recommendation is not None)
        self.apply.clicked.connect(self._apply)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _apply(self) -> None:
        recommendation = self.report.recommendation
        if recommendation is None:
            return
        endpoint = (self.endpoint.value() / 100.0
                    if recommendation.endpoint_volume is not None else None)
        self.report.recommendation = replace(
            recommendation, tx_scale=self.scale.value() / 100.0,
            endpoint_volume=endpoint,
            reason=recommendation.reason + "; operator-reviewed final value",
        )
        if self.runtime.operations.apply_station_calibration():
            QMessageBox.information(
                self, dual("Profile applied", "Profil použit"),
                dual("The profile was saved. Future payload transfers use it.",
                     "Profil byl uložen. Další datové přenosy jej použijí."),
            )
            self.apply.setEnabled(False)
