"""First-run readiness and dependency assistance."""

from __future__ import annotations

from PySide6.QtCore import QSettings, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import dual, tr
from ..install import DependencyKind
from ..services import TaskResult
from .runtime import ShellRuntime


class ReadinessDialog(QDialog):
    def __init__(
        self,
        runtime: ShellRuntime,
        settings: QSettings,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.settings = settings
        self.setWindowTitle(tr("readiness.title"))
        self.setMinimumSize(780, 450)

        outer = QVBoxLayout(self)
        heading = QLabel(dual("Prepare this station", "Připravte tuto stanici"))
        heading.setObjectName("PanelHeader")
        description = QLabel(
            dual(
                "Guardian checks external tools without transmitting or opening "
                "a radio. Nothing is downloaded or launched without your action.",
                "Guardian kontroluje externí nástroje bez vysílání a bez otevření "
                "rádia. Bez vašeho pokynu se nic nestáhne ani nespustí.",
            )
        )
        description.setObjectName("Metadata")
        description.setWordWrap(True)
        outer.addWidget(heading)
        outer.addWidget(description)

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 8, 0, 8)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(10)
        outer.addWidget(self.grid_host, 1)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        outer.addWidget(self.summary)
        actions = QHBoxLayout()
        self.rescan = QPushButton(dual("Scan again", "Zkontrolovat znovu"))
        self.rescan.clicked.connect(self._rescan)
        actions.addWidget(self.rescan)
        actions.addStretch()
        outer.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(
            tr("common.close")
        )
        buttons.rejected.connect(self._finish)
        outer.addWidget(buttons)

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._rescan()

    def _rescan(self) -> None:
        self.rescan.setEnabled(False)
        self.summary.setProperty("statusRole", "info")
        self.summary.setText(
            "◐ "
            + dual(
                "Scanning local dependencies…",
                "Kontroluji místní závislosti…",
            )
        )
        self.runtime.request_dependency_refresh()

    def _tick(self) -> None:
        self.runtime.drain_workers()
        if self.runtime.workers.is_active("dependency-scan"):
            return
        self.rescan.setEnabled(True)
        self._render()

    def _clear_grid(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _detail(self, status) -> str:
        if status.available:
            return status.detail
        if status.kind == DependencyKind.HAMLIB:
            return dual(
                "Not found. Guardian can install a verified portable build.",
                "Nenalezeno. Guardian může nainstalovat ověřenou přenosnou verzi.",
            )
        return dual(
            "Not found. Download it from the VARA author's website.",
            "Nenalezeno. Stáhněte VARA z oficiálního webu autora.",
        )

    def _render(self) -> None:
        statuses = self.runtime.dependency_statuses
        if not statuses:
            return
        self._clear_grid()
        headers = (
            tr("readiness.component"),
            tr("readiness.state"),
            dual("Detected path / guidance", "Nalezená cesta / doporučení"),
            dual("Action", "Akce"),
        )
        for column, text in enumerate(headers):
            label = QLabel(text)
            label.setObjectName("SectionLabel")
            self.grid.addWidget(label, 0, column)
        for row, status in enumerate(statuses, start=1):
            self.grid.addWidget(QLabel(status.label), row, 0)
            state = QLabel(
                ("● " + tr("common.ready"))
                if status.available
                else ("◆ " + tr("common.missing"))
            )
            state.setProperty(
                "statusRole", "success" if status.available else "warning"
            )
            self.grid.addWidget(state, row, 1)
            detail = QLabel(self._detail(status))
            detail.setObjectName("Metadata")
            detail.setWordWrap(True)
            self.grid.addWidget(detail, row, 2)
            action = QPushButton()
            if status.kind == DependencyKind.HAMLIB and not status.available:
                action.setText(
                    dual(
                        "Install verified Hamlib",
                        "Nainstalovat ověřený Hamlib",
                    )
                )
                action.clicked.connect(self._install_hamlib)
            elif status.kind in (DependencyKind.VARA_FM, DependencyKind.VARA_HF):
                action.setText(
                    dual("Open official source", "Otevřít oficiální zdroj")
                    if not status.available
                    else dual("Locate another…", "Vybrat jiný…")
                )
                if status.available:
                    action.clicked.connect(
                        lambda _checked=False, kind=status.kind: self._locate(kind)
                    )
                else:
                    action.clicked.connect(
                        lambda _checked=False, url=status.official_url:
                        QDesktopServices.openUrl(QUrl(url))
                    )
            else:
                action.setText(dual("Locate…", "Vybrat…"))
                action.clicked.connect(
                    lambda _checked=False, kind=status.kind: self._locate(kind)
                )
            self.grid.addWidget(action, row, 3)
        self.grid.setColumnStretch(2, 1)

        cfg = self.runtime.config
        selected = (
            DependencyKind.VARA_HF
            if cfg.vara_mode.upper() == "HF"
            else DependencyKind.VARA_FM
        )
        by_kind = {item.kind: item for item in statuses}
        station_ready = (
            cfg.callsign != "NOCALL"
            and (
                cfg.radio_backend != "hamlib"
                or by_kind[DependencyKind.HAMLIB].available
            )
            and by_kind[selected].available
        )
        self.summary.setProperty(
            "statusRole", "success" if station_ready else "warning"
        )
        self.summary.setText(
            "● "
            + dual(
                "Station is ready for the selected workflow.",
                "Stanice je připravena pro zvolený způsob práce.",
            )
            if station_ready
            else "◆ "
            + dual(
                "Complete the station identity and selected VARA dependency "
                "before normal operation.",
                "Před běžným provozem doplňte identitu stanice a zvolenou "
                "závislost VARA.",
            )
        )

    def _install_hamlib(self) -> None:
        answer = QMessageBox.question(
            self,
            dual("Install Hamlib", "Instalace Hamlib"),
            dual(
                "Download the official portable Hamlib build, verify its SHA-256 "
                "checksum when published, and install it into Guardian's user data?",
                "Stáhnout oficiální přenosnou verzi Hamlib, ověřit její SHA-256 "
                "a nainstalovat ji do uživatelských dat Guardianu?",
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.rescan.setEnabled(False)

        def completed(result: TaskResult) -> None:
            if result.error is not None:
                QMessageBox.critical(
                    self,
                    dual("Hamlib installation", "Instalace Hamlib"),
                    str(result.error),
                )
            self._render()

        self.runtime.install_hamlib(completed)

    def _locate(self, kind: DependencyKind) -> None:
        names = {
            DependencyKind.HAMLIB: ("rigctld.exe", "rigctld.exe"),
            DependencyKind.VARA_FM: ("VARAFM.exe", "VARAFM.exe"),
            DependencyKind.VARA_HF: ("VARA.exe", "VARA.exe"),
        }
        label, pattern = names[kind]
        path, _ = QFileDialog.getOpenFileName(
            self,
            dual(f"Locate {label}", f"Vyberte {label}"),
            "",
            dual(
                f"{label} ({pattern});;Executables (*.exe)",
                f"{label} ({pattern});;Spustitelné soubory (*.exe)",
            ),
        )
        if not path:
            return
        if kind == DependencyKind.HAMLIB:
            self.runtime.config.rigctld_path = path
        elif kind == DependencyKind.VARA_FM:
            self.runtime.config.vara_fm_path = path
        else:
            self.runtime.config.vara_hf_path = path
        self.runtime.config.save()
        self._rescan()

    def _finish(self) -> None:
        self.settings.setValue("onboarding/completed", True)
        self.settings.sync()
        self.accept()
