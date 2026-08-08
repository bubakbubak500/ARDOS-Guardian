"""Measuring the OFDM modem from inside Guardian, with no console anywhere.

Why a workspace and not a dialog
--------------------------------
Guardian's dialogs -- Diagnostics, Station readiness, Station settings -- are all
opened with `exec()`: they are short, modal, read-or-decide interactions, and
they block the shell while they are up. Nothing here fits that shape:

* A sweep is eleven SNR points times N runs. That is minutes of Viterbi
  decoding, and the station has to keep working through it -- the control
  channel keeps listening, mail keeps arriving, the activity log keeps filling.
  A modal dialog would freeze all of it.
* There is state worth keeping between actions: the resolved waveform facts, the
  last burst report, a transfer's log, a half-filled sweep table. A dialog throws
  those away every time it is closed, and this is a surface an operator comes
  back to repeatedly while changing one variable at a time.
* The shell's existing 500 ms poll already drains the worker pool and calls
  `refresh()` on the current workspace, which is exactly the loop a long
  background task needs. `capture_dialog` and `update_dialog` each have to run a
  timer of their own precisely because a dialog is outside that loop.

So this is `View ▸ Modem test`, a peer of Mail, Network and Log.

Everything slow runs on `runtime.operations.workers` and reports back on the UI
thread. Nothing in this module computes a measurement itself: every number comes
from `guardian.ofdm.bench`, which is also what `tools/ofdm_bench.py` calls, so the
figure an operator reads here and the figure a developer reads at a console are
the same figure by construction.

One action here leaves the simulator: Transmit into the radio hands the burst to
`Operations.transmit_test_burst`, which keys the PTT and plays it out. It exists
because saving a WAV and being told to "play it into the radio" left the operator
hunting for a media player and keying by hand -- the same manual step outside the
application that the workspace was built to remove. It is the only action that
transmits, so it is the only one behind a modal confirmation.
"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import config_dir
from ..i18n import dual, tr
from ..ofdm import MCS_TABLE
from ..ofdm import bench
from ..ofdm.config import PROFILE_LADDER, profile_or_default
from ..services import TaskResult
from .inputs import RowTable
from .measurements import measurement, repolish
from .ofdm_labels import profile_rung_label
from .runtime import ShellRuntime

#: Worker-pool task names. One per action, so a completion can never be mistaken
#: for another action's, and `is_active` is meaningful per action.
BURST_TASK = "modem-burst"
TRANSFER_TASK = "modem-transfer"
SWEEP_TASK = "modem-sweep"
FILE_TASK = "modem-test-file"
DECODE_TASK = "modem-decode"
TRANSMIT_TASK = "modem-transmit"

#: The gap `Operations.transmit_test_burst` leaves between bursts, and before the
#: first one. It does not pass `gap_seconds`, so it gets `make_test_burst`'s own
#: default -- and the estimate shown to the operator has to agree with that.
TRANSMIT_GAP_SECONDS = 1.0

#: A shorter ladder than `bench.SWEEP_POINTS`, for the common case of asking
#: "roughly where does this profile give up" without spending minutes on it.
COARSE_SWEEP_POINTS: tuple[float, ...] = (24.0, 16.0, 12.0, 8.0, 5.0)

#: How long a simulated transfer is allowed to take before it is abandoned. The
#: engine's own default is ten minutes; a stuck ARQ exchange inside the
#: application should give up long before that and say so.
TRANSFER_TIMEOUT_SECONDS = 180.0


class ModemWorkspace(QWidget):
    """Pick a waveform, then measure it: one burst, a transfer, or a sweep."""

    def __init__(self, runtime: ShellRuntime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.facts: bench.WaveformFacts | None = None
        self._running: str | None = None
        self._sweep_cancel = threading.Event()
        self._sweep_expected = 0
        # Only the on-air transmission is timed on screen. It is the one action
        # where what matters is not a number at the end but that the radio is
        # keyed right now.
        self._transmit_started: float | None = None
        self._transmit_expected = 0.0
        # Worker threads never touch a widget. They put a line or a finished
        # sweep point here and the UI thread drains it, which is the same
        # arrangement `WorkerPool` itself uses for completions.
        self._pending: queue.SimpleQueue = queue.SimpleQueue()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)

        title = QLabel(tr("modem.title"))
        title.setObjectName("PanelHeader")
        outer.addWidget(title)
        intro = QLabel(tr("modem.intro"))
        intro.setObjectName("Metadata")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        outer.addWidget(self._selection_row())
        outer.addWidget(self._facts_panel())

        self.tabs = QTabWidget()
        self.tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs.addTab(self._burst_page(), tr("modem.tab_burst"))
        self.tabs.addTab(self._transfer_page(), tr("modem.tab_transfer"))
        self.sweep_page = self._sweep_page()
        self.tabs.addTab(self.sweep_page, tr("modem.tab_sweep"))
        self.tabs.addTab(self._files_page(), tr("modem.tab_files"))
        outer.addWidget(self.tabs, 1)

        self._render_facts()

    # -- picking a waveform --------------------------------------------------

    def _selection_row(self) -> QWidget:
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.profile_picker = QComboBox()
        # PROFILE_LADDER, not profile_names(): the ladder is ordered by occupied
        # bandwidth, which is the order these are meant to be tried in.
        # Alphabetical order would put NARROW_1K2 between the WIDEs.
        for name in PROFILE_LADDER:
            self.profile_picker.addItem(
                profile_rung_label(profile_or_default(name)), name
            )
        self.profile_picker.setCurrentIndex(
            max(0, self.profile_picker.findData(self.runtime.config.ofdm_profile))
        )
        self.profile_picker.currentIndexChanged.connect(self._render_facts)

        self.mcs_picker = QComboBox()
        for scheme in MCS_TABLE:
            self.mcs_picker.addItem(scheme.label, scheme.index)
        self.mcs_picker.setCurrentIndex(
            max(0, self.mcs_picker.findData(self.runtime.config.ofdm_mcs))
        )
        self.mcs_picker.currentIndexChanged.connect(self._render_facts)

        row.addWidget(QLabel(tr("modem.profile")))
        row.addWidget(self.profile_picker, 1)
        row.addWidget(QLabel(tr("modem.mcs")))
        row.addWidget(self.mcs_picker)
        return host

    def _facts_panel(self) -> QWidget:
        # An existing panel identity rather than a new one: the theme already
        # styles WorkspacePanel, and inventing a name the stylesheet says
        # nothing about would leave this reading as loose text on the page.
        host = QFrame()
        host.setObjectName("WorkspacePanel")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        heading = QLabel(tr("modem.facts"))
        heading.setObjectName("SectionLabel")
        layout.addWidget(heading)

        self.facts_fields: dict[str, QLabel] = {}
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        rows = (
            ("band", dual("Occupied band", "Zabíraná část pásma")),
            ("sample_rate", dual("Sound card rate", "Vzorkování zvukové karty")),
            ("fft", dual("Transform and guard", "Transformace a ochranný interval")),
            ("spacing", dual("Carrier spacing and symbol",
                             "Rozestup nosných a symbol")),
            ("carriers", dual("Active carriers", "Aktivní nosné")),
            ("mcs", dual("Modulation and code", "Modulace a kód")),
            ("bits", dual("Bits per symbol", "Bity na symbol")),
            ("phy_rate", dual("PHY rate", "Rychlost PHY")),
            ("header", dual("Header cost", "Náklad hlavičky")),
            ("airtime", dual("One full block", "Jeden plný blok")),
        )
        for key, caption in rows:
            value = QLabel()
            value.setWordWrap(True)
            value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self.facts_fields[key] = value
            form.addRow(QLabel(caption), value)
        layout.addLayout(form)

        self.facts_note = QLabel()
        self.facts_note.setWordWrap(True)
        layout.addWidget(self.facts_note)
        return host

    def selected_profile(self):
        """The `OfdmProfile` the pickers currently name."""
        return profile_or_default(str(self.profile_picker.currentData()))

    def selected_mcs(self) -> int:
        return int(self.mcs_picker.currentData())

    def _render_facts(self) -> None:
        """Restate the whole waveform whenever either picker moves.

        This is the panel that answers "what does this profile actually mean",
        so it is derived from `bench.describe` and never from a stored copy.
        """
        entry = self.selected_profile()
        facts = bench.describe(entry, self.selected_mcs())
        self.facts = facts
        fields = self.facts_fields
        fields["band"].setText(dual(
            f"{facts.band} — {facts.occupied:.0f} Hz wide",
            f"{facts.band} — šířka {facts.occupied:.0f} Hz",
        ))
        # Named apart from the occupied bandwidth on purpose: a sample rate read
        # as a bandwidth is the single most common misreading of an OFDM profile.
        fields["sample_rate"].setText(dual(
            f"{facts.sample_rate} Hz — this is not the bandwidth",
            f"{facts.sample_rate} Hz — to není šířka pásma",
        ))
        fields["fft"].setText(dual(
            f"{facts.fft_size} points · guard {facts.cp_length} samples "
            f"({facts.cp_ms:.2f} ms)",
            f"{facts.fft_size} bodů · ochranný interval {facts.cp_length} vzorků "
            f"({facts.cp_ms:.2f} ms)",
        ))
        fields["spacing"].setText(dual(
            f"{facts.subcarrier_spacing:.2f} Hz · symbol {facts.symbol_ms:.1f} ms",
            f"{facts.subcarrier_spacing:.2f} Hz · symbol {facts.symbol_ms:.1f} ms",
        ))
        fields["carriers"].setText(dual(
            f"{facts.carriers} ({facts.data_carriers} data + "
            f"{facts.pilots} pilot)",
            f"{facts.carriers} ({facts.data_carriers} datových + "
            f"{facts.pilots} pilotních)",
        ))
        fields["mcs"].setText(f"MCS{facts.mcs_index} — {facts.mcs_label}")
        fields["bits"].setText(dual(
            f"{facts.coded_bits_per_symbol} coded → "
            f"{facts.information_bits_per_symbol} information",
            f"{facts.coded_bits_per_symbol} kódovaných → "
            f"{facts.information_bits_per_symbol} informačních",
        ))
        fields["phy_rate"].setText(dual(
            f"{facts.phy_rate:.0f} bit/s before framing and ARQ",
            f"{facts.phy_rate:.0f} bit/s před rámcováním a ARQ",
        ))
        fields["header"].setText(dual(
            f"{facts.header_symbols} symbols of every burst",
            f"{facts.header_symbols} symbolů každého vysílání",
        ))
        fields["airtime"].setText(dual(
            f"{facts.full_block_seconds:.2f} s of air for {facts.block_size} B",
            f"{facts.full_block_seconds:.2f} s vysílání na {facts.block_size} B",
        ))
        if facts.sample_rate != 48000:
            self.facts_note.setText(tr("modem.needs_fast_card",
                                       rate=facts.sample_rate))
            self.facts_note.setProperty("statusRole", "warning")
        else:
            self.facts_note.setText(tr("modem.untried"))
            self.facts_note.setProperty("statusRole", "info")
        repolish(self.facts_note)

    # -- one burst -----------------------------------------------------------

    def _burst_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 8, 6, 6)
        hint = QLabel(tr("modem.burst_hint"))
        hint.setObjectName("Metadata")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        controls = QHBoxLayout()
        self.burst_snr = self._snr_spin(15.0)
        self.burst_payload = self._byte_spin(16, 4_096, 512)
        self.burst_seed = self._seed_spin()
        controls.addWidget(QLabel(tr("modem.snr")))
        controls.addWidget(self.burst_snr)
        controls.addWidget(QLabel(tr("modem.payload")))
        controls.addWidget(self.burst_payload)
        controls.addWidget(QLabel(tr("modem.seed")))
        controls.addWidget(self.burst_seed)
        self.burst_button = QPushButton(tr("modem.run_burst"))
        self.burst_button.setObjectName("primaryAction")
        self.burst_button.clicked.connect(self.start_burst)
        controls.addWidget(self.burst_button)
        controls.addStretch()
        layout.addLayout(controls)

        self.burst_status = QLabel(tr("modem.idle"))
        self.burst_status.setWordWrap(True)
        layout.addWidget(self.burst_status)

        self.burst_fields = self._report_form(layout, (
            ("channel", dual("Channel applied", "Použitý kanál")),
            ("payload", dual("Payload in the block", "Data v bloku")),
            ("applied", dual("SNR asked for", "Požadovaný odstup")),
            ("measured", dual("SNR measured", "Měřený odstup")),
            ("evm", dual("EVM", "Chyba vektoru (EVM)")),
            ("cfo", dual("Frequency offset", "Kmitočtová odchylka")),
            ("sync", dual("Sync confidence", "Spolehlivost synchronizace")),
            ("spread", dual("Channel spread", "Rozptyl kanálu")),
            ("crest", dual("Crest factor", "Činitel výkyvu")),
            ("ber", dual("Uncoded reference BER", "Referenční BER bez kódu")),
            ("airtime", dual("Airtime", "Doba vysílání")),
            ("frame", dual("Frame", "Rámec")),
        ))
        layout.addStretch(1)
        return page

    def start_burst(self) -> None:
        """One block through the full channel simulator, off the UI thread."""
        entry = self.selected_profile()
        index = self.selected_mcs()
        snr = float(self.burst_snr.value())
        payload = int(self.burst_payload.value())
        seed = int(self.burst_seed.value())
        self._submit(
            BURST_TASK,
            lambda: bench.run_burst(entry, index, payload_bytes=payload,
                                    snr_db=snr, seed=seed),
            self.burst_status,
            self._render_burst,
        )

    def _render_burst(self, result: bench.BurstResult) -> None:
        """Render a finished burst. Separated so it can be driven directly."""
        metrics = result.metrics
        fields = self.burst_fields
        fields["channel"].setText(result.channel)
        fields["payload"].setText(dual(
            f"{result.payload_bytes} B", f"{result.payload_bytes} B",
        ))
        fields["applied"].setText(dual(
            f"{result.applied_snr_db:.1f} dB in band · "
            f"{result.applied_snr_db + result.wideband_offset_db:.1f} dB across "
            "the whole audio band",
            f"{result.applied_snr_db:.1f} dB v pásmu · "
            f"{result.applied_snr_db + result.wideband_offset_db:.1f} dB v celém "
            "zvukovém pásmu",
        ))
        fields["measured"].setText(measurement(metrics.snr_db, "{value:.1f} dB"))
        fields["evm"].setText(measurement(
            metrics.evm_rms * 100.0 if metrics.evm_rms is not None else None,
            "{value:.1f} %",
        ))
        fields["cfo"].setText(measurement(metrics.cfo_hz, "{value:+.1f} Hz"))
        fields["sync"].setText(measurement(metrics.sync_confidence, "{value:.2f}"))
        fields["spread"].setText(
            measurement(result.channel_spread_db, "{value:.1f} dB")
        )
        fields["crest"].setText(dual(
            f"transmitted {measurement(result.tx_crest_db, '{value:.1f} dB')} · "
            f"received {measurement(metrics.crest_factor_db, '{value:.1f} dB')}",
            f"vysláno {measurement(result.tx_crest_db, '{value:.1f} dB')} · "
            f"přijato {measurement(metrics.crest_factor_db, '{value:.1f} dB')}",
        ))
        fields["ber"].setText(f"{result.uncoded_ber:.2e}")
        fields["airtime"].setText(dual(
            f"{result.seconds:.2f} s · {result.samples} samples",
            f"{result.seconds:.2f} s · {result.samples} vzorků",
        ))
        fields["frame"].setText(metrics.summary())
        self._verdict(self.burst_status, result.passed,
                      tr("modem.burst_ok"), tr("modem.burst_bad"))

    # -- a whole transfer ----------------------------------------------------

    def _transfer_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 8, 6, 6)
        hint = QLabel(tr("modem.transfer_hint"))
        hint.setObjectName("Metadata")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        controls = QHBoxLayout()
        self.transfer_snr = self._snr_spin(15.0)
        self.transfer_payload = self._byte_spin(64, 65_536, 2_048)
        controls.addWidget(QLabel(tr("modem.snr")))
        controls.addWidget(self.transfer_snr)
        controls.addWidget(QLabel(tr("modem.payload")))
        controls.addWidget(self.transfer_payload)
        self.transfer_button = QPushButton(tr("modem.run_transfer"))
        self.transfer_button.setObjectName("primaryAction")
        self.transfer_button.clicked.connect(self.start_transfer)
        controls.addWidget(self.transfer_button)
        controls.addStretch()
        layout.addLayout(controls)

        self.transfer_status = QLabel(tr("modem.idle"))
        self.transfer_status.setWordWrap(True)
        layout.addWidget(self.transfer_status)

        self.transfer_fields = self._report_form(layout, (
            ("blocks", dual("Blocks", "Bloky")),
            ("acked", dual("Blocks acknowledged", "Potvrzené bloky")),
            ("retries", dual("Retransmissions", "Opakovaná vysílání")),
            ("per", dual("Packet error rate", "Chybovost paketů")),
            ("measured", dual("SNR measured", "Měřený odstup")),
            ("evm", dual("EVM", "Chyba vektoru (EVM)")),
            ("airtime", dual("Channel occupied", "Obsazení kanálu")),
            ("throughput", dual("Throughput measured", "Měřená propustnost")),
            ("turnaround", dual("Keying turnaround", "Průtah klíčování")),
        ))

        log_heading = QLabel(dual("Link log", "Log spoje"))
        log_heading.setObjectName("SectionLabel")
        layout.addWidget(log_heading)
        self.transfer_log = QPlainTextEdit()
        self.transfer_log.setReadOnly(True)
        self.transfer_log.setMaximumBlockCount(4_000)
        layout.addWidget(self.transfer_log, 1)
        return page

    def start_transfer(self) -> None:
        """A whole message with ARQ, its log streaming in as it happens."""
        entry = self.selected_profile()
        index = self.selected_mcs()
        snr = float(self.transfer_snr.value())
        payload = int(self.transfer_payload.value())
        # The station's own keying lead is what the link will really wait for, so
        # the measurement uses it rather than a figure invented here.
        turnaround = max(0.05, self.runtime.config.ofdm_tx_lead_ms / 1000.0)
        pending = self._pending

        def work():
            return bench.run_transfer(
                entry, index, payload_bytes=payload, snr_db=snr,
                ptt_turnaround=turnaround, timeout=TRANSFER_TIMEOUT_SECONDS,
                on_log=lambda line: pending.put(("log", line)),
            )

        if self._submit(TRANSFER_TASK, work, self.transfer_status,
                        self._render_transfer):
            self.transfer_log.clear()

    def _render_transfer(self, result: bench.TransferResult) -> None:
        fields = self.transfer_fields
        fields["blocks"].setText(dual(
            f"{result.blocks} × {result.facts.block_size} B for "
            f"{result.payload_bytes} B of message",
            f"{result.blocks} × {result.facts.block_size} B na "
            f"{result.payload_bytes} B zprávy",
        ))
        fields["acked"].setText(f"{result.blocks_acked} / {result.blocks}")
        fields["retries"].setText(str(result.retries))
        fields["per"].setText(measurement(
            result.packet_error_rate * 100.0
            if result.packet_error_rate is not None else None,
            "{value:.1f} %",
        ))
        fields["measured"].setText(
            measurement(result.measured_snr_db, "{value:.1f} dB")
        )
        fields["evm"].setText(measurement(
            result.measured_evm * 100.0
            if result.measured_evm is not None else None,
            "{value:.1f} %",
        ))
        fields["airtime"].setText(f"{result.channel_seconds:.2f} s")
        fields["throughput"].setText(
            measurement(result.throughput_bps, "{value:.0f} bit/s")
        )
        fields["turnaround"].setText(dual(
            f"{self.runtime.config.ofdm_tx_lead_ms} ms, from Station settings",
            f"{self.runtime.config.ofdm_tx_lead_ms} ms, z nastavení stanice",
        ))
        # The log is streamed while the transfer runs, but a report driven
        # directly still has to show it, and a stream that was interrupted has
        # to end up complete.
        text = "\n".join(result.log)
        if text and text != self.transfer_log.toPlainText():
            self.transfer_log.setPlainText(text)
        self._verdict(self.transfer_status, result.passed,
                      tr("modem.transfer_ok"), tr("modem.transfer_bad"))

    # -- decode rate against SNR --------------------------------------------

    def _sweep_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 8, 6, 6)
        hint = QLabel(tr("modem.sweep_hint"))
        hint.setObjectName("Metadata")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        controls = QHBoxLayout()
        self.sweep_detail = QComboBox()
        self.sweep_detail.addItem(
            tr("modem.sweep_full", count=len(bench.SWEEP_POINTS)),
            bench.SWEEP_POINTS,
        )
        self.sweep_detail.addItem(
            tr("modem.sweep_coarse", count=len(COARSE_SWEEP_POINTS)),
            COARSE_SWEEP_POINTS,
        )
        self.sweep_detail.setCurrentIndex(1)
        self.sweep_runs = QSpinBox()
        self.sweep_runs.setRange(1, 50)
        self.sweep_runs.setValue(5)
        controls.addWidget(QLabel(tr("modem.sweep_detail")))
        controls.addWidget(self.sweep_detail)
        controls.addWidget(QLabel(tr("modem.sweep_runs")))
        controls.addWidget(self.sweep_runs)
        self.sweep_button = QPushButton(tr("modem.run_sweep"))
        self.sweep_button.setObjectName("primaryAction")
        self.sweep_button.clicked.connect(self.start_sweep)
        controls.addWidget(self.sweep_button)
        self.sweep_cancel = QPushButton(tr("modem.cancel"))
        self.sweep_cancel.setEnabled(False)
        self.sweep_cancel.clicked.connect(self.cancel_sweep)
        controls.addWidget(self.sweep_cancel)
        controls.addStretch()
        layout.addLayout(controls)

        self.sweep_status = QLabel(tr("modem.idle"))
        self.sweep_status.setWordWrap(True)
        layout.addWidget(self.sweep_status)

        self.sweep_alarm = QLabel()
        self.sweep_alarm.setWordWrap(True)
        self.sweep_alarm.setVisible(False)
        layout.addWidget(self.sweep_alarm)

        self.sweep_table = RowTable(0, 7)
        self.sweep_table.setHorizontalHeaderLabels([
            tr("modem.col_snr"),
            tr("modem.col_decoded"),
            tr("modem.col_wrong"),
            tr("modem.col_measured"),
            tr("modem.col_evm"),
            tr("modem.col_ber"),
            tr("modem.col_verdict"),
        ])
        self.sweep_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.sweep_table, 1)

        self.sweep_summary = QLabel()
        self.sweep_summary.setWordWrap(True)
        layout.addWidget(self.sweep_summary)
        return page

    def start_sweep(self, points: tuple[float, ...] | None = None) -> None:
        """Walk the SNR ladder, filling the table point by point.

        `points` exists so the caller can ask for a shorter ladder than either
        offered preset -- the button passes nothing and reads the picker.
        """
        entry = self.selected_profile()
        index = self.selected_mcs()
        runs = int(self.sweep_runs.value())
        ladder = tuple(points) if points else tuple(self.sweep_detail.currentData())
        pending = self._pending
        self._sweep_cancel.clear()
        cancelled = self._sweep_cancel.is_set

        def work():
            return bench.run_sweep(
                entry, index, runs=runs, points=ladder,
                on_point=lambda point: pending.put(("point", point)),
                cancelled=cancelled,
            )

        if self._submit(SWEEP_TASK, work, self.sweep_status, self._render_sweep):
            self._sweep_expected = len(ladder)
            self.sweep_table.setRowCount(0)
            self.sweep_alarm.setVisible(False)
            self.sweep_summary.clear()
            self.sweep_status.setText(
                tr("modem.sweep_running", done=0, total=len(ladder))
            )

    def cancel_sweep(self) -> None:
        """Ask the sweep to stop; it is polled between points, so this is prompt."""
        if self._running != SWEEP_TASK:
            return
        self._sweep_cancel.set()
        self.sweep_cancel.setEnabled(False)
        self.sweep_status.setText(tr("modem.sweep_cancelling"))
        self.sweep_status.setProperty("statusRole", "info")
        repolish(self.sweep_status)

    def _add_sweep_row(self, point: bench.SweepPoint) -> None:
        row = self.sweep_table.rowCount()
        self.sweep_table.insertRow(row)
        # A wrong-byte delivery is marked in the cell as well as in the alarm
        # above the table: the alarm says how many, the mark says which point.
        wrong = (f"⚠ {point.wrong_bytes}" if point.wrong_bytes
                 else str(point.wrong_bytes))
        values = (
            f"{point.applied_snr_db:.1f}",
            f"{point.decoded} / {point.runs}",
            wrong,
            measurement(point.measured_snr_db, "{value:.1f}"),
            measurement(
                point.measured_evm * 100.0
                if point.measured_evm is not None else None,
                "{value:.1f}",
            ),
            f"{point.uncoded_ber:.1e}",
            tr("modem.reliable") if point.reliable else tr("modem.unreliable"),
        )
        for column, text in enumerate(values):
            self.sweep_table.setItem(row, column, QTableWidgetItem(text))
        if self._running == SWEEP_TASK:
            self.sweep_status.setText(tr(
                "modem.sweep_running", done=row + 1,
                total=max(self._sweep_expected, row + 1),
            ))
            self.sweep_status.setProperty("statusRole", "info")
            repolish(self.sweep_status)

    def _render_sweep(self, result: bench.SweepResult) -> None:
        """Rebuild the whole table from a finished sweep.

        Idempotent on purpose: the rows already arrived through `on_point` while
        it ran, and a report handed here directly must produce the same table.
        """
        self.sweep_table.setRowCount(0)
        for point in result.points:
            self._add_sweep_row(point)

        wrong = result.wrong_byte_deliveries
        if wrong:
            # The single most serious result the modem can produce. Below the
            # cliff a block has to be rejected, never handed back corrupted, so
            # this cannot be a number in a cell somebody might scroll past.
            self.sweep_alarm.setText(tr("modem.wrong_bytes_alarm", count=wrong))
            self.sweep_alarm.setProperty("statusRole", "danger")
        else:
            self.sweep_alarm.setText(tr("modem.wrong_bytes_none"))
            self.sweep_alarm.setProperty("statusRole", "success")
        self.sweep_alarm.setVisible(True)
        repolish(self.sweep_alarm)

        self.sweep_summary.setText(dual(
            f"Weakest signal every run decoded at: "
            f"{measurement(result.lowest_reliable_snr_db, '{value:.1f} dB')} · "
            f"strongest signal nothing decoded at: "
            f"{measurement(result.cliff_snr_db, '{value:.1f} dB')} · "
            f"{result.runs} run(s) per point",
            f"Nejslabší signál, kde dekódovalo každé opakování: "
            f"{measurement(result.lowest_reliable_snr_db, '{value:.1f} dB')} · "
            f"nejsilnější signál, kde nedekódovalo nic: "
            f"{measurement(result.cliff_snr_db, '{value:.1f} dB')} · "
            f"opakování na bod: {result.runs}",
        ))
        if wrong:
            self.sweep_status.setText(tr("modem.wrong_bytes_alarm", count=wrong))
            self.sweep_status.setProperty("statusRole", "danger")
            # Raise the page holding the alarm. The operator who started the
            # sweep is usually already looking at it, but one who wandered off
            # to another tab must not have to come back to find this out.
            self.tabs.setCurrentWidget(self.sweep_page)
        elif result.cancelled:
            self.sweep_status.setText(tr("modem.sweep_cancelled",
                                         count=len(result.points)))
            self.sweep_status.setProperty("statusRole", "warning")
        else:
            self.sweep_status.setText(tr("modem.sweep_done",
                                         count=len(result.points)))
            self.sweep_status.setProperty("statusRole", "success")
        repolish(self.sweep_status)

    # -- files: one to transmit, and one to read back ------------------------

    def _files_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 8, 6, 6)

        make_heading = QLabel(dual("A burst to transmit", "Dávka k vysílání"))
        make_heading.setObjectName("SectionLabel")
        layout.addWidget(make_heading)
        make_hint = QLabel(tr("modem.make_hint"))
        make_hint.setObjectName("Metadata")
        make_hint.setWordWrap(True)
        layout.addWidget(make_hint)
        transmit_hint = QLabel(tr("modem.transmit_hint"))
        transmit_hint.setObjectName("Metadata")
        transmit_hint.setWordWrap(True)
        layout.addWidget(transmit_hint)

        make_row = QHBoxLayout()
        self.file_repeats = QSpinBox()
        self.file_repeats.setRange(1, 20)
        self.file_repeats.setValue(3)
        self.file_payload = self._byte_spin(16, 4_096, 512)
        self.file_gap = QDoubleSpinBox()
        self.file_gap.setRange(0.2, 10.0)
        self.file_gap.setSingleStep(0.5)
        self.file_gap.setValue(1.0)
        self.file_gap.setSuffix(" s")
        make_row.addWidget(QLabel(tr("modem.repeats")))
        make_row.addWidget(self.file_repeats)
        make_row.addWidget(QLabel(tr("modem.payload")))
        make_row.addWidget(self.file_payload)
        make_row.addWidget(QLabel(tr("modem.gap")))
        make_row.addWidget(self.file_gap)
        self.save_button = QPushButton(tr("modem.save_tx"))
        self.save_button.setObjectName("primaryAction")
        self.save_button.clicked.connect(self.save_test_file)
        make_row.addWidget(self.save_button)
        # Beside the file, not instead of it: a file is still what goes to
        # another tool or into the archive. This is the same waveform, keyed and
        # played by Guardian, which is the step that used to be manual.
        self.transmit_button = QPushButton(tr("modem.transmit"))
        self.transmit_button.setObjectName("primaryAction")
        self.transmit_button.clicked.connect(self.start_transmit)
        make_row.addWidget(self.transmit_button)
        make_row.addStretch()
        layout.addLayout(make_row)

        self.file_status = QLabel(tr("modem.idle"))
        self.file_status.setWordWrap(True)
        self.file_status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.file_status)

        # A line of its own: a finished save and a live transmission are two
        # different facts, and neither may overwrite the other.
        self.transmit_status = QLabel(tr("modem.idle"))
        self.transmit_status.setWordWrap(True)
        layout.addWidget(self.transmit_status)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(divider)

        open_heading = QLabel(dual("A file to decode", "Soubor k dekódování"))
        open_heading.setObjectName("SectionLabel")
        layout.addWidget(open_heading)
        open_hint = QLabel(tr("modem.open_hint"))
        open_hint.setObjectName("Metadata")
        open_hint.setWordWrap(True)
        layout.addWidget(open_hint)

        open_row = QHBoxLayout()
        self.open_button = QPushButton(tr("modem.open_wav"))
        self.open_button.clicked.connect(self.open_capture)
        open_row.addWidget(self.open_button)
        open_row.addStretch()
        layout.addLayout(open_row)

        self.capture_status = QLabel(tr("modem.idle"))
        self.capture_status.setWordWrap(True)
        layout.addWidget(self.capture_status)

        self.capture_fields = self._report_form(layout, (
            ("path", dual("File", "Soubor")),
            ("rate", dual("Sample rate", "Vzorkovací kmitočet")),
            ("length", dual("Length", "Délka")),
            ("levels", dual("Levels", "Úrovně")),
            ("sync", dual("Sync confidence", "Spolehlivost synchronizace")),
            ("snr", dual("SNR measured", "Měřený odstup")),
            ("evm", dual("EVM", "Chyba vektoru (EVM)")),
            ("cfo", dual("Frequency offset", "Kmitočtová odchylka")),
            ("frame", dual("Frame", "Rámec")),
        ))
        layout.addStretch(1)
        return page

    def _captures_dir(self) -> Path:
        """Where Guardian keeps its own recordings -- the obvious place for these."""
        return config_dir() / "captures"

    def save_test_file(self) -> None:
        """Write a clean burst, with no channel applied, for a radio to transmit."""
        entry = self.selected_profile()
        index = self.selected_mcs()
        folder = self._captures_dir()
        folder.mkdir(parents=True, exist_ok=True)
        suggested = folder / f"txtest-{entry.name}-mcs{index}.wav"
        chosen, _ = QFileDialog.getSaveFileName(
            self, tr("modem.save_tx"), str(suggested), tr("modem.wav_filter")
        )
        if not chosen:
            return
        repeats = int(self.file_repeats.value())
        payload = int(self.file_payload.value())
        gap = float(self.file_gap.value())
        target = Path(chosen)
        self._submit(
            FILE_TASK,
            lambda: bench.make_test_burst(
                entry, index, payload_bytes=payload, repeats=repeats,
                gap_seconds=gap, wav_path=target,
            ),
            self.file_status,
            self._render_saved_file,
        )

    def _render_saved_file(self, made) -> None:
        samples, path = made
        facts = self.facts
        rate = facts.sample_rate if facts is not None else 0
        seconds = len(samples) / rate if rate else 0.0
        self.file_status.setText(dual(
            f"Wrote {path} — {seconds:.1f} s at {rate} Hz, "
            f"{self.file_repeats.value()} burst(s). Guardian can put this on the "
            "air itself with Transmit into the radio; the file is for another "
            "tool or for the archive.",
            f"Zapsáno {path} — {seconds:.1f} s při {rate} Hz, "
            f"počet dávek: {self.file_repeats.value()}. Guardian to umí vyslat "
            "sám tlačítkem Vyslat do rádia; soubor je pro jiný nástroj nebo do "
            "archivu.",
        ))
        self.file_status.setProperty("statusRole", "success")
        repolish(self.file_status)

    # -- putting it on the air ----------------------------------------------- #

    def transmit_seconds(self, payload_bytes: int, repeats: int) -> float:
        """About how long the transmitter will be keyed for.

        An estimate, and it only has to be good enough to tell an operator
        whether this is three seconds or fifteen. The engine's own figure is
        `full_block_seconds`, which is one *full* block, so a smaller payload
        keeps the fixed header cost and scales only the data part. On top of
        that come the lead-in and one gap after each burst, both of which
        `Operations.transmit_test_burst` leaves at their default.

        The seconds actually aired come back from the transmission itself; this
        never stands in for that.
        """
        facts = self.facts
        if facts is None:
            facts = bench.describe(self.selected_profile(), self.selected_mcs())
        repeats = max(1, int(repeats))
        share = min(1.0, max(1, int(payload_bytes)) / max(1, facts.block_size))
        fixed = facts.header_symbols * facts.symbol_ms / 1000.0
        data = max(0.0, facts.full_block_seconds - fixed)
        burst = fixed + data * share
        return TRANSMIT_GAP_SECONDS * (repeats + 1) + burst * repeats

    def _transmit_frequency(self) -> str:
        """Where the radio is, if Guardian has any way of knowing."""
        frequency = self.runtime.operations.current_frequency()
        if not frequency:
            return tr("modem.transmit_frequency_unknown")
        return f"{int(frequency) / 1_000_000:.4f} MHz"

    def confirm_transmit(self, seconds: float, repeats: int,
                         payload_bytes: int) -> bool:
        """Ask before keying. Nothing else in this workspace touches the PTT.

        Ok/Cancel with Cancel as the default button, the same shape as the
        shell's manual-QSY confirmation: an operator who presses Enter without
        reading transmits nothing.
        """
        answer = QMessageBox.question(
            self,
            tr("modem.transmit_title"),
            tr(
                "modem.transmit_confirm",
                seconds=f"{seconds:.0f}",
                profile=self.selected_profile().name,
                mcs=self.selected_mcs(),
                repeats=repeats,
                payload=payload_bytes,
                frequency=self._transmit_frequency(),
                device=(self.runtime.config.audio_output
                        or tr("modem.transmit_device_unset")),
            ),
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Ok

    def start_transmit(self) -> None:
        """Key the radio and play the test burst through it, off the UI thread.

        `Operations.transmit_test_burst` blocks for the whole transmission --
        seconds on a wide profile, a quarter of a minute on a narrow one -- so
        it goes on the worker pool like every other slow thing here.
        """
        # Refused before the confirmation, not after: asking an operator to
        # authorise a transmission that cannot start would be a lie.
        if self._running is not None:
            self._refuse(self.transmit_status)
            return
        entry = self.selected_profile()
        index = self.selected_mcs()
        repeats = int(self.file_repeats.value())
        payload = int(self.file_payload.value())
        expected = self.transmit_seconds(payload, repeats)
        if not self.confirm_transmit(expected, repeats, payload):
            return
        operations = self.runtime.operations
        # The pickers, not the saved config: an operator measuring a rung has
        # selected it here and has no reason to have saved it as the station's.
        if self._submit(
            TRANSMIT_TASK,
            lambda: operations.transmit_test_burst(
                profile_name=entry.name, mcs_index=index,
                payload_bytes=payload, repeats=repeats,
            ),
            self.transmit_status,
            self._render_transmitted,
        ):
            self._transmit_started = time.monotonic()
            self._transmit_expected = expected
            self._show_transmit_progress()

    def _show_transmit_progress(self) -> None:
        """Say on the page that the radio is live, and for how long so far."""
        if self._transmit_started is None:
            return
        elapsed = max(0.0, time.monotonic() - self._transmit_started)
        self.transmit_status.setText(tr(
            "modem.transmit_live",
            elapsed=f"{elapsed:.1f}",
            expected=f"{self._transmit_expected:.0f}",
        ))
        self.transmit_status.setProperty("statusRole", "danger")
        repolish(self.transmit_status)

    def _render_transmitted(self, aired: float | None) -> None:
        """Report the seconds aired, or that nothing went out.

        `None` means the transmission was refused or failed, and the backend has
        already logged the real reason. Guessing at one here would be worse than
        pointing at the log.
        """
        self._transmit_started = None
        if aired is None:
            self.transmit_status.setText(tr("modem.transmit_none"))
            self.transmit_status.setProperty("statusRole", "warning")
        else:
            self.transmit_status.setText(
                tr("modem.transmit_done", seconds=f"{aired:.1f}")
            )
            self.transmit_status.setProperty("statusRole", "success")
        repolish(self.transmit_status)

    def open_capture(self) -> None:
        """Decode any WAV file, whatever wrote it."""
        folder = self._captures_dir()
        folder.mkdir(parents=True, exist_ok=True)
        chosen, _ = QFileDialog.getOpenFileName(
            self, tr("modem.open_wav"), str(folder), tr("modem.wav_filter")
        )
        if not chosen:
            return
        entry = self.selected_profile()
        path = Path(chosen)
        self._submit(
            DECODE_TASK,
            lambda: bench.decode_capture(entry, path),
            self.capture_status,
            self._render_capture,
        )

    def _render_capture(self, result: bench.CaptureResult) -> None:
        metrics = result.metrics
        fields = self.capture_fields
        fields["path"].setText(str(result.path))
        fields["rate"].setText(dual(
            f"{result.sample_rate} Hz in the file · "
            f"{result.expected_rate} Hz expected by this profile",
            f"{result.sample_rate} Hz v souboru · "
            f"{result.expected_rate} Hz očekává tento profil",
        ))
        fields["length"].setText(dual(
            f"{result.seconds:.2f} s · {result.samples} samples",
            f"{result.seconds:.2f} s · {result.samples} vzorků",
        ))
        fields["levels"].setText(dual(
            f"RMS {measurement(result.audio_rms or None, '{value:.4f}')} · "
            f"peak {measurement(result.audio_peak or None, '{value:.4f}')}",
            f"RMS {measurement(result.audio_rms or None, '{value:.4f}')} · "
            f"špička {measurement(result.audio_peak or None, '{value:.4f}')}",
        ))
        fields["sync"].setText(measurement(metrics.sync_confidence, "{value:.2f}"))
        fields["snr"].setText(measurement(metrics.snr_db, "{value:.1f} dB"))
        fields["evm"].setText(measurement(
            metrics.evm_rms * 100.0 if metrics.evm_rms is not None else None,
            "{value:.1f} %",
        ))
        fields["cfo"].setText(measurement(metrics.cfo_hz, "{value:+.1f} Hz"))
        if result.header is not None:
            fields["frame"].setText(result.header.summary())
        else:
            # The decoder's own English reason, unwrapped: a translated sentence
            # around it would only bury the useful part.
            fields["frame"].setText(measurement(result.error or None))
        if result.passed:
            self.capture_status.setText(
                tr("modem.capture_ok", profile=self.selected_profile().name)
            )
            self.capture_status.setProperty("statusRole", "success")
        elif not result.rate_matches:
            self.capture_status.setText(tr("modem.capture_rate",
                                           rate=result.sample_rate,
                                           expected=result.expected_rate))
            self.capture_status.setProperty("statusRole", "warning")
        else:
            self.capture_status.setText(tr("modem.capture_bad"))
            self.capture_status.setProperty("statusRole", "warning")
        repolish(self.capture_status)

    # -- running things off the UI thread ------------------------------------

    def _submit(self, task: str, work, status: QLabel, render) -> bool:
        """Start one measurement, or explain why it cannot start.

        One at a time by design: two overlapping sweeps would compete for the
        same worker pool the station itself uses, and the buttons say so by
        going insensitive rather than by silently dropping the second click.
        """
        if self._running is not None:
            return self._refuse(status)
        # The pool can refuse independently -- a task of this name still
        # finishing, or a pool closed because Guardian is shutting down.
        if not self.runtime.operations.workers.submit(
            task, work, lambda result: self._finish(result, status, render)
        ):
            return self._refuse(status)
        self._set_running(task)
        status.setText(tr("modem.running"))
        status.setProperty("statusRole", "info")
        repolish(status)
        return True

    def _refuse(self, status: QLabel) -> bool:
        """Say on the page why nothing started, and report that nothing did."""
        status.setText(tr("modem.busy"))
        status.setProperty("statusRole", "warning")
        repolish(status)
        return False

    def _finish(self, result: TaskResult, status: QLabel, render) -> None:
        try:
            self._drain_pending()
            self._set_running(None)
            if result.error is not None:
                status.setText(tr("modem.task_failed", error=result.error))
                status.setProperty("statusRole", "warning")
                repolish(status)
                return
            render(result.value)
        except RuntimeError:
            # Changing the interface language rebuilds the whole shell, so a
            # measurement submitted before that lands on widgets Qt has already
            # deleted. Losing the report is the correct outcome; raising out of
            # a worker-pool completion would take the drain loop with it.
            return

    def _set_running(self, task: str | None) -> None:
        self._running = task
        idle = task is None
        for button in (self.burst_button, self.transfer_button,
                       self.sweep_button, self.save_button,
                       self.transmit_button, self.open_button):
            button.setEnabled(idle)
        self.sweep_cancel.setEnabled(task == SWEEP_TASK)
        if task != TRANSMIT_TASK:
            # Nothing is on the air, so nothing may still be counting -- even if
            # the transmission ended by failing rather than by finishing.
            self._transmit_started = None

    def _drain_pending(self) -> None:
        """Move whatever the worker thread produced into the widgets."""
        while True:
            try:
                kind, value = self._pending.get_nowait()
            except queue.Empty:
                return
            if kind == "log":
                self.transfer_log.appendPlainText(value)
            else:
                self._add_sweep_row(value)

    def refresh(self) -> None:
        """Show what the running measurement has produced so far.

        Called by the shell's poll, which has already drained the worker pool.
        The pool's own completions therefore arrive whether or not this workspace
        is the visible one -- and `_finish` drains this queue before it renders,
        so a measurement watched from another workspace still ends up complete.
        Only the filling-in as it happens depends on being looked at.
        """
        self._drain_pending()
        if self._running == TRANSMIT_TASK:
            self._show_transmit_progress()

    # -- small shared widget shapes -----------------------------------------

    def _snr_spin(self, value: float) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(-10.0, 40.0)
        widget.setSingleStep(1.0)
        widget.setDecimals(1)
        widget.setValue(value)
        widget.setSuffix(" dB")
        widget.setToolTip(tr("modem.snr_hint"))
        return widget

    def _byte_spin(self, low: int, high: int, value: int) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(low, high)
        widget.setSingleStep(64)
        widget.setValue(value)
        widget.setSuffix(" B")
        return widget

    def _seed_spin(self) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(0, 65_535)
        widget.setValue(0xA5)
        widget.setToolTip(tr("modem.seed_hint"))
        return widget

    def _report_form(self, layout: QVBoxLayout, rows) -> dict[str, QLabel]:
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        fields: dict[str, QLabel] = {}
        for key, caption in rows:
            value = QLabel()
            value.setWordWrap(True)
            value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            fields[key] = value
            form.addRow(QLabel(caption), value)
        layout.addLayout(form)
        return fields

    def _verdict(self, status: QLabel, passed: bool, good: str, bad: str) -> None:
        status.setText(good if passed else bad)
        status.setProperty("statusRole", "success" if passed else "warning")
        repolish(status)
