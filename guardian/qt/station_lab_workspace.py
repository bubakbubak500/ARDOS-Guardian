"""SC-FTN Station Lab and paired AutoTune workspace.

The workspace only presents the consent, progress, and report surfaces.  The
runtime owns the calibration protocol and the radio/audio operations, so a
headless preview can exercise this widget with a small operations double and
never touches hardware.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
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

from ..station_lab import CalibrationState, QUICK_TUNE_LEVELS, QUICK_TUNE_REPEATS
from ..i18n import dual
from .window_geometry import fit_dialog_to_screen


_STATE_LABELS = {
    CalibrationState.IDLE.value: dual("Idle", "Nečinné"),
    CalibrationState.OFFERING.value: dual("Calling peer", "Volám protistanici"),
    CalibrationState.WAITING_APPROVAL.value: dual(
        "Waiting for consent", "Čekám na souhlas"
    ),
    CalibrationState.PREPARING.value: dual("Preparing", "Připravuji"),
    CalibrationState.MEASURING.value: dual("Measuring", "Měřím"),
    CalibrationState.WAITING_REPORT.value: dual(
        "Waiting for report", "Čekám na výsledek"
    ),
    CalibrationState.COMPLETE.value: dual("Complete", "Dokončeno"),
    CalibrationState.CANCELLED.value: dual("Cancelled", "Zrušeno"),
    CalibrationState.FAILED.value: dual("Failed", "Selhalo"),
}


class StationLabReportDialog(QDialog):
    """Compact read-only view of one saved AutoTune report."""

    def __init__(self, runtime, report, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        status = getattr(
            getattr(runtime, "operations", None), "station_lab", None
        )
        json_path = str(getattr(status, "report_json", "") or "")
        csv_path = str(getattr(status, "report_csv", "") or "")
        self.setWindowTitle(dual("SC-FTN AutoTune report", "Report AutoTune SC-FTN"))
        outer = QVBoxLayout(self)

        if report is None:
            label = QLabel(
                dual(
                    "The report object is no longer in memory. Saved files:",
                    "Objekt reportu již není v paměti. Uložené soubory:",
                )
                + f"\n{json_path or '—'}\n{csv_path or '—'}"
            )
            label.setWordWrap(True)
            outer.addWidget(label)
        else:
            recommendation = getattr(report, "recommendation", None)
            summary = QLabel(self._summary(report, recommendation, json_path, csv_path))
            summary.setObjectName("Metadata")
            summary.setWordWrap(True)
            outer.addWidget(summary)

            results = list(getattr(report, "results", ()) or ())
            table = QTableWidget(len(results), 8)
            table.setHorizontalHeaderLabels(
                [
                    "#",
                    dual("TX", "TX"),
                    "MCS/FEC",
                    dual("Frame", "Rámec"),
                    "SNR",
                    "EVM",
                    dual("Peak", "Špička"),
                    "Sync",
                ]
            )
            table.setAlternatingRowColors(True)
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
            for row, result in enumerate(results):
                values = (
                    str(getattr(result, "sequence", row + 1)),
                    f"{float(getattr(result, 'tx_scale', 0.0)) * 100:.1f}%",
                    f"MCS{getattr(result, 'mcs', '?')} / {getattr(result, 'fec', '?')}",
                    "OK" if getattr(result, "frame_ok", False) else "—",
                    self._number(getattr(result, "snr_db", None), " dB"),
                    self._number(getattr(result, "evm_rms", None), ""),
                    self._number(getattr(result, "audio_peak", None), ""),
                    self._number(getattr(result, "sync_confidence", None), ""),
                )
                for column, value in enumerate(values):
                    table.setItem(row, column, QTableWidgetItem(value))
            table.resizeColumnsToContents()
            table.horizontalHeader().setStretchLastSection(True)
            outer.addWidget(table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        fit_dialog_to_screen(
            self,
            preferred_size=(860, 460),
            minimum_size=(560, 340),
        )

    @staticmethod
    def _number(value, suffix: str) -> str:
        if value is None:
            return "—"
        try:
            return f"{float(value):.3f}{suffix}"
        except (TypeError, ValueError):
            return "—"

    @staticmethod
    def _summary(report, recommendation, json_path: str, csv_path: str) -> str:
        lines = [
            dual("Peer", "Protistanice") + f": {getattr(report, 'peer', '') or '—'}",
            dual("Direction", "Směr") + f": {getattr(report, 'direction', '') or '—'}",
            dual("Result", "Výsledek") + f": {getattr(report, 'stop_reason', '') or '—'}",
        ]
        if recommendation is not None:
            lines.append(
                dual("Recommendation", "Doporučení")
                + ": "
                + f"SC-FTN {getattr(recommendation, 'bandwidth', '2K7')} · "
                + f"MCS{getattr(recommendation, 'mcs', '?')} · "
                + f"{float(getattr(recommendation, 'tx_scale', 0.0)) * 100:.1f}% · "
                + str(getattr(recommendation, "reason", ""))
            )
        if json_path or csv_path:
            lines.append(
                dual("Saved", "Uloženo")
                + f": JSON {json_path or '—'}; CSV {csv_path or '—'}"
            )
        return "\n".join(lines)


class StationLabWorkspace(QWidget):
    """Operator-facing paired SC-FTN AutoTune workflow."""

    def __init__(self, runtime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self._last_session = 0
        self._last_offer = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(8)

        title = QLabel(dual("Auto Tune", "Auto Tune"))
        title.setObjectName("PanelHeader")
        outer.addWidget(title)
        intro = QLabel(
            dual(
                "Measure one SC-FTN audio path with a consenting peer. The run is "
                "receive-safe, bounded, and saved as a station calibration record.",
                "Změřte jednu zvukovou cestu SC-FTN se souhlasem protistanice. Běh "
                "je omezený, bezpečný pro příjem a uloží kalibrační záznam stanice.",
            )
        )
        intro.setObjectName("Metadata")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        self.availability_hint = QLabel(dual(
            "To enable calibration, select Guardian SC-FTN in Settings → "
            "Station settings → VARA & payload. Auto Tune calibrates "
            "the Guardian modem; it does not calibrate VARA.",
            "Pro aktivaci kalibrace vyberte Guardian SC-FTN v Nastavení → "
            "Nastavení stanice → VARA a přenos. Auto Tune kalibruje modem "
            "Guardian, nikoli VARA.",
        ))
        self.availability_hint.setWordWrap(True)
        outer.addWidget(self.availability_hint)

        setup = QFrame()
        setup.setObjectName("WorkspacePanel")
        setup_layout = QFormLayout(setup)
        setup_layout.setContentsMargins(10, 8, 10, 8)
        self.peer = QLineEdit()
        self.peer.setPlaceholderText("OK2XYZ")
        self.peer.setMaxLength(16)
        self.start_button = QPushButton(dual("Call and start", "Zavolat a spustit"))
        self.start_button.setObjectName("primaryAction")
        self.start_button.clicked.connect(self._start)
        self.start = self.start_button
        self.cancel_button = QPushButton(dual("Cancel", "Zrušit"))
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.setEnabled(False)
        self.cancel = self.cancel_button
        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(6)
        controls_layout.addWidget(self.start_button)
        controls_layout.addWidget(self.cancel_button)
        controls_layout.addStretch(1)
        setup_layout.addRow(dual("Peer callsign", "Volací značka protistanice"), self.peer)
        setup_layout.addRow(dual("Quick sweep", "Rychlé měření"), QLabel(
            dual(
                f"{len(QUICK_TUNE_LEVELS)} levels · {QUICK_TUNE_REPEATS} repeats; "
                "the peer must consent before RF starts.",
                f"{len(QUICK_TUNE_LEVELS)} úrovní · {QUICK_TUNE_REPEATS} opakování; "
                "před vysíláním musí protistanice souhlasit.",
            )
        ))
        setup_layout.addRow("", controls)
        outer.addWidget(setup)

        self.offer = QFrame()
        self.offer.setObjectName("AttentionCard")
        offer_layout = QVBoxLayout(self.offer)
        self.offer_text = QLabel()
        self.offer_text.setWordWrap(True)
        offer_layout.addWidget(self.offer_text)
        offer_actions = QHBoxLayout()
        self.accept = QPushButton(dual("Accept test", "Přijmout test"))
        self.accept.setObjectName("primaryAction")
        self.accept.clicked.connect(self.accept_offer)
        self.reject = QPushButton(dual("Refuse", "Odmítnout"))
        self.reject.clicked.connect(self.reject_offer)
        offer_actions.addWidget(self.accept)
        offer_actions.addWidget(self.reject)
        offer_actions.addStretch(1)
        offer_layout.addLayout(offer_actions)
        self.offer.hide()
        outer.addWidget(self.offer)

        status_panel = QFrame()
        status_panel.setObjectName("WorkspacePanel")
        status_layout = QVBoxLayout(status_panel)
        status_layout.setContentsMargins(10, 8, 10, 8)
        status_layout.setSpacing(6)
        self.state_label = QLabel()
        self.state_label.setObjectName("SectionLabel")
        self.state = self.state_label
        self.message_label = QLabel()
        self.message_label.setObjectName("Metadata")
        self.detail = self.message_label
        self.message_label.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, max(1, len(QUICK_TUNE_LEVELS) * QUICK_TUNE_REPEATS))
        self.progress.setTextVisible(True)
        self.current_label = QLabel()
        self.current_label.setObjectName("Metadata")
        self.segments: list[QLabel] = []
        segment_row = QHBoxLayout()
        segment_row.setSpacing(3)
        for index in range(1, len(QUICK_TUNE_LEVELS) + 1):
            segment = QLabel(str(index))
            segment.setAlignment(Qt.AlignmentFlag.AlignCenter)
            segment.setMinimumHeight(20)
            self.segments.append(segment)
            segment_row.addWidget(segment, 1)
        status_layout.addWidget(self.state_label)
        status_layout.addWidget(self.message_label)
        status_layout.addWidget(self.progress)
        status_layout.addLayout(segment_row)
        status_layout.addWidget(self.current_label)
        outer.addWidget(status_panel)

        report_panel = QFrame()
        report_panel.setObjectName("WorkspacePanel")
        report_layout = QHBoxLayout(report_panel)
        report_layout.setContentsMargins(10, 8, 10, 8)
        self.report_label = QLabel()
        self.report_label.setObjectName("Metadata")
        self.report_label.setWordWrap(True)
        self.report_button = QPushButton(dual("View report", "Zobrazit report"))
        self.report_button.clicked.connect(self._show_report)
        report_layout.addWidget(self.report_label, 1)
        report_layout.addWidget(self.report_button)
        outer.addWidget(report_panel)
        outer.addStretch(1)
        self.refresh()

    def _status(self):
        return getattr(getattr(self.runtime, "operations", None), "station_lab", None)

    def _sc_selected(self) -> bool:
        config = getattr(self.runtime, "config", None)
        return getattr(config, "payload_backend", None) == "ofdm_vhf"

    def _start(self) -> None:
        if not self._sc_selected():
            return
        peer = self.peer.text().strip().upper()
        if not peer:
            self.message_label.setText(
                dual("Enter the peer callsign first.", "Nejprve zadejte volací značku protistanice.")
            )
            return
        answer = QMessageBox.question(
            self,
            dual("Start SC-FTN AutoTune?", "Spustit AutoTune SC-FTN?"),
            dual(
                f"Ask {peer} to consent before the bounded calibration sweep. "
                "The sweep will key the configured radio.",
                f"Požádat {peer} o souhlas před omezeným měřením. "
                "Měření zaklíčuje nastavené rádio.",
            ),
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Ok:
            return
        operations = self.runtime.operations
        if not operations.start_station_calibration(peer, "quick"):
            self.refresh()
            return
        self._last_offer = None
        self.refresh()

    def _cancel(self) -> None:
        operations = self.runtime.operations
        if operations.cancel_station_calibration():
            self.refresh()

    def accept_offer(self) -> bool:
        if not self._sc_selected():
            return False
        operations = self.runtime.operations
        accepted = bool(operations.accept_station_calibration())
        if accepted:
            self.refresh()
        return accepted

    def reject_offer(self) -> bool:
        operations = self.runtime.operations
        rejected = bool(operations.reject_station_calibration())
        if rejected:
            self.refresh()
        return rejected

    def _show_report(self) -> None:
        status = self._status()
        if status is None:
            return
        report = getattr(status, "report", None)
        if report is None and not (
            getattr(status, "report_json", "") or getattr(status, "report_csv", "")
        ):
            return
        StationLabReportDialog(
            self.runtime,
            report,
            self,
        ).exec()

    def refresh(self) -> None:
        status = self._status()
        sc_selected = self._sc_selected()
        self.availability_hint.setVisible(not sc_selected)
        self.start_button.setEnabled(sc_selected and status is not None)
        self.accept.setEnabled(sc_selected)
        self.reject.setEnabled(sc_selected)
        self.peer.setEnabled(sc_selected)
        if status is None:
            self.state_label.setText(dual("Auto Tune unavailable", "Auto Tune není dostupný"))
            self.message_label.clear()
            self.progress.setValue(0)
            self.current_label.clear()
            self.cancel_button.setEnabled(False)
            self.report_button.setEnabled(False)
            return

        state = str(getattr(status, "state", CalibrationState.IDLE.value) or CalibrationState.IDLE.value)
        peer = str(getattr(status, "peer", "") or "")
        session_id = int(getattr(status, "session_id", 0) or 0)
        self._last_session = session_id
        pending_offer = bool(getattr(status, "pending_offer", False))
        self.offer.setVisible(pending_offer)
        self.offer_text.setText(
            str(getattr(status, "message", "") or "")
            or dual(
                f"{peer} requests an SC-FTN station test.",
                f"{peer} žádá o test stanice SC-FTN.",
            )
        )
        self.state_label.setText(
            f"{_STATE_LABELS.get(state, state)}"
            + (f" · {peer}" if peer else "")
        )
        message = str(getattr(status, "error", "") or getattr(status, "message", "") or "")
        self.message_label.setText(message)
        total = int(getattr(status, "total", 0) or 0)
        progress = int(getattr(status, "progress", 0) or 0)
        maximum = max(1, total, len(QUICK_TUNE_LEVELS) * QUICK_TUNE_REPEATS)
        self.progress.setRange(0, maximum)
        self.progress.setValue(max(0, min(maximum, progress)))
        current = str(getattr(status, "current", "") or "")
        self.current_label.setText(
            current
            or dual(
                f"{progress}/{total or maximum} probe points",
                f"{progress}/{total or maximum} měřicích bodů",
            )
        )
        active = state not in {
            CalibrationState.IDLE.value,
            CalibrationState.COMPLETE.value,
            CalibrationState.CANCELLED.value,
            CalibrationState.FAILED.value,
        }
        self.cancel_button.setEnabled(sc_selected and active)
        self.start_button.setEnabled(sc_selected and not active and not pending_offer)
        self.peer.setEnabled(sc_selected and not active and not pending_offer)
        segment_progress = max(
            0,
            min(
                len(self.segments),
                (progress + QUICK_TUNE_REPEATS - 1) // QUICK_TUNE_REPEATS,
            ),
        )
        for index, segment in enumerate(self.segments, 1):
            if index <= segment_progress:
                colours = ("#159a70", "#ffffff")
            elif index == segment_progress + 1 and active:
                colours = ("#2879c7", "#ffffff")
            else:
                colours = ("#263442", "#9eb0c0")
            segment.setStyleSheet(
                f"background: {colours[0]}; color: {colours[1]}; "
                "border-radius: 4px; padding: 3px;"
            )
        if pending_offer:
            self.message_label.setText(
                dual(
                    f"{peer} requests a bounded SC-FTN calibration. Review and "
                    "accept or reject the request in the station prompt.",
                    f"{peer} žádá o omezenou kalibraci SC-FTN. Žádost přijměte "
                    "nebo odmítněte v dialogu stanice.",
                )
            )
        report = getattr(status, "report", None)
        has_report = report is not None or bool(
            getattr(status, "report_json", "") or getattr(status, "report_csv", "")
        )
        self.report_button.setEnabled(sc_selected and has_report)
        if has_report:
            recommendation = getattr(report, "recommendation", None) if report else None
            if recommendation is not None:
                self.report_label.setText(
                    dual("Recommendation saved", "Doporučení uloženo")
                    + f": {getattr(recommendation, 'bandwidth', '2K7')} · "
                    + f"MCS{getattr(recommendation, 'mcs', '?')} · "
                    + f"{float(getattr(recommendation, 'tx_scale', 0.0)) * 100:.1f}%"
                )
            else:
                self.report_label.setText(
                    dual("A saved report is ready.", "Uložený report je připraven.")
                )
        else:
            self.report_label.setText(
                dual("No completed report yet.", "Zatím není dokončený report.")
            )


__all__ = ["StationLabReportDialog", "StationLabWorkspace"]
