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
        self.setWindowTitle("Guardian update")
        self.setMinimumWidth(520)
        outer = QVBoxLayout(self)
        title = QLabel(f"Guardian {info.version} is available")
        title.setObjectName("PanelHeader")
        detail = QLabel(
            f"Installed: {__version__}\n"
            "Guardian downloads only after confirmation and verifies the "
            "installer SHA-256 checksum before it can be launched."
        )
        detail.setWordWrap(True)
        outer.addWidget(title)
        outer.addWidget(detail)
        if info.notes_url:
            notes = QPushButton("Open release notes")
            notes.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl(info.notes_url))
            )
            outer.addWidget(notes)
        self.download = QPushButton("Download verified installer")
        self.download.setObjectName("primaryAction")
        self.download.clicked.connect(self._download)
        outer.addWidget(self.download)
        self.status = QLabel()
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _download(self) -> None:
        answer = QMessageBox.question(
            self,
            "Download update",
            f"Download Guardian {self.info.version} from GitHub?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.download.setEnabled(False)
        self.status.setText("Downloading and verifying installer…")

        def completed(result: TaskResult) -> None:
            self.download.setEnabled(True)
            if result.error is not None:
                self.status.setText(str(result.error))
                return
            path = result.value
            self.status.setText(f"Verified installer: {path}")
            launch = QMessageBox.question(
                self,
                "Install update",
                "Close Guardian and launch the verified installer now?",
            )
            if launch == QMessageBox.StandardButton.Yes:
                QProcess.startDetached(str(path), [])

        self.runtime.download_update(self.info, completed)
