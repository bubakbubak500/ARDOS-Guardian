"""Read-only diagnostics with explicit export."""

from __future__ import annotations

import json
import platform
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .. import __version__
from ..config import DEFAULT_CONFIG_PATH, config_dir
from ..i18n import dual, tr
from .runtime import ShellRuntime


class DiagnosticsDialog(QDialog):
    def __init__(self, runtime: ShellRuntime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.setWindowTitle(dual("Guardian diagnostics", "Diagnostika Guardianu"))
        self.setMinimumSize(760, 520)
        outer = QVBoxLayout(self)
        heading = QLabel(dual("Diagnostics", "Diagnostika"))
        heading.setObjectName("PanelHeader")
        detail = QLabel(dual(
            "This report contains station configuration and local paths, but no "
            "message bodies or attachments. Export only when you choose.",
            "Tato zpráva obsahuje nastavení stanice a místní cesty, nikoli však "
            "obsah zpráv ani přílohy. Exportuje se pouze na váš pokyn.",
        ))
        detail.setObjectName("Metadata")
        detail.setWordWrap(True)
        outer.addWidget(heading)
        outer.addWidget(detail)
        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        outer.addWidget(self.viewer, 1)
        probe = QPushButton(dual(
            "Test VARA data path", "Otestovat datovou cestu VARA"
        ))
        probe.clicked.connect(self._probe_vara)
        outer.addWidget(probe)
        export = QPushButton(dual(
            "Export diagnostic report…", "Exportovat diagnostickou zprávu…"
        ))
        export.clicked.connect(self._export)
        outer.addWidget(export)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(
            tr("common.close")
        )
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.viewer.setPlainText(
            json.dumps(self.report(), indent=2, ensure_ascii=False)
        )

    def _probe_vara(self) -> None:
        """Check the 8301 data path locally -- no radio, no second station.

        Answers the one question two-station RF tests keep leaving open: does
        VARA hold its end of the data socket, and does it acknowledge what
        Guardian writes with a BUFFER report?
        """
        operations = getattr(self.runtime, "operations", None)
        vara = getattr(operations, "vara", None)
        lines = [dual("VARA data path", "Datová cesta VARA"), "=" * 44]
        if vara is None or not getattr(vara, "connected", False):
            lines.append(dual(
                "VARA is not connected -- connect it first.",
                "VARA není připojena — nejprve ji připojte.",
            ))
            self.viewer.setPlainText("\n".join(lines))
            return

        state = vara.state
        alive = vara.data_socket_alive()
        before = state.buffer_reports
        linked = state.link_state == "CONNECTED"
        lines += [
            f"command port : {state.cmd_connected}",
            f"data port    : {state.data_connected}"
            f" -> {state.data_peer_endpoint}",
            f"data socket  : {'alive' if alive else 'CLOSED BY VARA'}",
            f"generation   : {state.data_socket_generation}"
            f" ({state.data_socket_reopens} reopens)",
            f"link state   : {state.link_state}",
            f"BUFFER seen  : {before}",
            f"rejected cmds: {state.rejected_commands}",
            f"bitrate      : {state.tx_bitrate_bps}",
            "",
        ]

        if not linked:
            # Port 8301 is a bridge that only carries traffic during a link.
            # Writing with no link would either be discarded or, worse, sit in
            # VARA and corrupt the next real transfer -- so do not write.
            lines += [
                dual(
                    "No VARA link is up, so nothing was written: port 8301 only "
                    "carries data during a connection, and a stray write could "
                    "corrupt the next transfer.",
                    "Žádné spojení VARA neběží, proto se nic nezapisovalo: port "
                    "8301 přenáší data jen během spojení a zápis mimo ně by "
                    "mohl poškodit příští přenos.",
                ),
                "",
                dual(
                    "The socket line above is still the useful result. For the "
                    "decisive check, run this again during a transfer, or watch "
                    "VARA's own window: its DATA indicator and bps graph show "
                    "whether Guardian's bytes ever reach the modem.",
                    "Užitečný výsledek je i tak řádek o socketu. Rozhodující "
                    "test: spusťte tohle znovu během přenosu, nebo sledujte "
                    "okno samotné VARA — indikátor DATA a graf bps ukážou, "
                    "jestli se Guardianovy bajty do modemu vůbec dostanou.",
                ),
            ]
            self.viewer.setPlainText("\n".join(lines))
            return

        try:
            vara.write_data(b"\0" * 256)
        except Exception as exc:  # noqa: BLE001
            lines.append(f"write failed : {exc}")
            self.viewer.setPlainText("\n".join(lines))
            return

        QApplication.processEvents()
        deadline = time.monotonic() + 5.0
        while state.buffer_reports == before and time.monotonic() < deadline:
            time.sleep(0.05)
            QApplication.processEvents()

        gained = state.buffer_reports - before
        lines += [
            "wrote 256 bytes into the live link",
            f"BUFFER reports in 5 s : {gained}",
            f"last BUFFER value     : {state.tx_buffer_bytes}",
            f"data socket afterwards: "
            f"{'alive' if vara.data_socket_alive() else 'CLOSED BY VARA'}",
            "",
        ]
        if gained:
            lines.append(dual(
                "VARA queued the write. The data path works; a failing "
                "transfer is an RF or session problem, not this socket.",
                "VARA zápis zařadila do fronty. Datová cesta funguje; selhání "
                "přenosu je pak problém RF nebo relace, nikoli tohoto socketu.",
            ))
        else:
            lines.append(dual(
                "VARA reported no BUFFER during a live link. Per the native "
                "command reference BUFFER is sent whenever VARA adds data to "
                "the queue, so the bytes are not reaching the modem.",
                "VARA nehlásila žádný BUFFER, přestože spojení běželo. Podle "
                "specifikace se BUFFER posílá vždy, když VARA přidá data do "
                "fronty — bajty se tedy do modemu nedostávají.",
            ))
        self.viewer.setPlainText("\n".join(lines))

    def report(self) -> dict:
        snapshot = self.runtime.snapshots.read()
        diagnostic_audio = config_dir() / "last-bad-control.wav"
        transport = self.runtime.operations.audio_transport
        return {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "guardian_version": __version__,
            "platform": platform.platform(),
            "python": sys.version,
            "frozen": bool(getattr(sys, "frozen", False)),
            "data_directory": str(config_dir()),
            "config_path": str(DEFAULT_CONFIG_PATH),
            "last_bad_control_audio": (
                str(diagnostic_audio) if diagnostic_audio.exists() else None
            ),
            "control_audio_levels": (
                transport.levels() if transport is not None else None
            ),
            "configuration": asdict(self.runtime.config),
            "snapshot": asdict(snapshot),
            "dependencies": [
                asdict(status) for status in self.runtime.dependency_statuses
            ],
            "events": [
                {
                    "time": event.timestamp.isoformat(),
                    "level": event.level.value,
                    "source": event.source,
                    "message": event.message,
                }
                for event in self.runtime.events.history()
            ],
        }

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            dual("Export Guardian diagnostics", "Export diagnostiky Guardianu"),
            "guardian-diagnostics.json",
            dual("JSON files (*.json)", "Soubory JSON (*.json)"),
        )
        if path:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(self.report(), handle, indent=2, ensure_ascii=False)
