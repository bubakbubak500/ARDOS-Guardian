"""Consent-first update dialog."""

from __future__ import annotations

from PySide6.QtCore import QProcess, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from .. import __version__
from ..i18n import dual, tr
from ..services import TaskResult
from ..updates import UpdateInfo
from .runtime import ShellRuntime


class UpdateDialog(QDialog):
    download_progress = Signal(int, int)

    def __init__(
        self,
        runtime: ShellRuntime,
        info: UpdateInfo,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.info = info
        self._downloading = False
        self.download_progress.connect(self._show_progress)
        self.worker_timer = QTimer(self)
        self.worker_timer.setInterval(100)
        self.worker_timer.timeout.connect(self.runtime.drain_workers)
        self.setWindowTitle(dual("Guardian update", "Aktualizace Guardianu"))
        self.setMinimumWidth(520)
        outer = QVBoxLayout(self)
        title = QLabel(dual(
            f"Guardian {info.version} is available",
            f"Je dostupný Guardian {info.version}",
        ))
        title.setObjectName("PanelHeader")
        detail = QLabel(dual(
            f"Installed: {__version__}\n"
            "Guardian downloads only after confirmation and verifies the "
            "installer SHA-256 checksum before it can be launched.",
            f"Nainstalováno: {__version__}\n"
            "Guardian začne stahovat až po potvrzení a před spuštěním ověří "
            "kontrolní součet SHA-256 instalátoru.",
        ))
        detail.setWordWrap(True)
        outer.addWidget(title)
        outer.addWidget(detail)
        if info.notes_url:
            notes = QPushButton(dual("Open release notes", "Otevřít poznámky k verzi"))
            notes.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl(info.notes_url))
            )
            outer.addWidget(notes)
        self.download = QPushButton(dual(
            "Download verified installer", "Stáhnout ověřený instalátor"
        ))
        self.download.setObjectName("primaryAction")
        self.download.clicked.connect(self._download)
        outer.addWidget(self.download)
        self.status = QLabel()
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.hide()
        outer.addWidget(self.progress)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.close_button = buttons.button(
            QDialogButtonBox.StandardButton.Close
        )
        self.close_button.setText(
            tr("common.close")
        )
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _download(self) -> None:
        answer = QMessageBox.question(
            self,
            dual("Download update", "Stáhnout aktualizaci"),
            dual(
                f"Download Guardian {self.info.version} from GitHub?",
                f"Stáhnout Guardian {self.info.version} z GitHubu?",
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._downloading = True
        self.download.setEnabled(False)
        self.close_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status.setText(dual(
            "Downloading and verifying installer…",
            "Stahuji a ověřuji instalátor…",
        ))

        def progress(received: int, total: int | None) -> None:
            self.download_progress.emit(received, total or 0)

        def completed(result: TaskResult) -> None:
            self._downloading = False
            self.worker_timer.stop()
            self.download.setEnabled(True)
            self.close_button.setEnabled(True)
            if result.error is not None:
                self.progress.hide()
                self.status.setText(str(result.error))
                return
            path = result.value
            self.progress.setRange(0, 100)
            self.progress.setValue(100)
            self.status.setText(dual(
                f"Verified installer: {path}",
                f"Ověřený instalátor: {path}",
            ))
            launch = QMessageBox.question(
                self,
                dual("Install update", "Nainstalovat aktualizaci"),
                dual(
                    "Close Guardian and launch the verified installer now?",
                    "Zavřít Guardian a nyní spustit ověřený instalátor?",
                ),
            )
            if launch == QMessageBox.StandardButton.Yes:
                started = QProcess.startDetached(str(path), [])
                succeeded = started[0] if isinstance(started, tuple) else started
                if succeeded:
                    self.accept()
                    QTimer.singleShot(0, QApplication.quit)
                else:
                    self.status.setText(dual(
                        "The installer could not be launched.",
                        "Instalátor se nepodařilo spustit.",
                    ))

        submitted = self.runtime.download_update(
            self.info,
            completed,
            progress,
        )
        if submitted:
            self.worker_timer.start()
        else:
            self._downloading = False
            self.download.setEnabled(True)
            self.close_button.setEnabled(True)
            self.progress.hide()
            self.status.setText(dual(
                "An update download is already running.",
                "Stahování aktualizace již probíhá.",
            ))

    def _show_progress(self, received: int, total: int) -> None:
        if total <= 0:
            self.progress.setRange(0, 0)
            return
        percent = min(100, round(received * 100 / total))
        self.progress.setRange(0, 100)
        self.progress.setValue(percent)
        self.progress.setFormat(
            f"{percent}%  ·  {received / 1_048_576:.1f} / "
            f"{total / 1_048_576:.1f} MB"
        )

    def reject(self) -> None:
        if self._downloading:
            return
        super().reject()
