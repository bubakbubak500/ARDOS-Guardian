"""Read-only diagnostics with explicit export."""

from __future__ import annotations

import json
import platform
import sys
from dataclasses import asdict
from datetime import datetime, timezone

from PySide6.QtWidgets import (
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
from .runtime import ShellRuntime


class DiagnosticsDialog(QDialog):
    def __init__(self, runtime: ShellRuntime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.setWindowTitle("Guardian diagnostics")
        self.setMinimumSize(760, 520)
        outer = QVBoxLayout(self)
        heading = QLabel("Diagnostics")
        heading.setObjectName("PanelHeader")
        detail = QLabel(
            "This report contains station configuration and local paths, but no "
            "message bodies or attachments. Export only when you choose."
        )
        detail.setObjectName("Metadata")
        detail.setWordWrap(True)
        outer.addWidget(heading)
        outer.addWidget(detail)
        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        outer.addWidget(self.viewer, 1)
        export = QPushButton("Export diagnostic report…")
        export.clicked.connect(self._export)
        outer.addWidget(export)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.viewer.setPlainText(
            json.dumps(self.report(), indent=2, ensure_ascii=False)
        )

    def report(self) -> dict:
        snapshot = self.runtime.snapshots.read()
        return {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "guardian_version": __version__,
            "platform": platform.platform(),
            "python": sys.version,
            "frozen": bool(getattr(sys, "frozen", False)),
            "data_directory": str(config_dir()),
            "config_path": str(DEFAULT_CONFIG_PATH),
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
            "Export Guardian diagnostics",
            "guardian-diagnostics.json",
            "JSON files (*.json)",
        )
        if path:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(self.report(), handle, indent=2, ensure_ascii=False)
