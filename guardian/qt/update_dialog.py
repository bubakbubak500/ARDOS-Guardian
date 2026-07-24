"""Consent-first update dialog."""

from __future__ import annotations

from PySide6.QtCore import QProcess, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .. import __version__
from ..i18n import dual, tr
from ..services import TaskResult
from ..updates import UpdateInfo
from .runtime import ShellRuntime


class UpdateDialog(QDialog):
    def __init__(
        self,
        runtime: ShellRuntime,
        info: UpdateInfo,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.info = info
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
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(
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
        self.download.setEnabled(False)
        self.status.setText(dual(
            "Downloading and verifying installer…",
            "Stahuji a ověřuji instalátor…",
        ))

        def completed(result: TaskResult) -> None:
            self.download.setEnabled(True)
            if result.error is not None:
                self.status.setText(str(result.error))
                return
            path = result.value
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
                QProcess.startDetached(str(path), [])

        self.runtime.download_update(self.info, completed)
