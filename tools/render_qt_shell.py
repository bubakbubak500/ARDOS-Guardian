"""Render deterministic Qt shell screenshots for visual review."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="guardian-qt-render-")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from guardian.qt.runtime import ShellRuntime
from guardian.qt.shell import GuardianMainWindow
from guardian.qt.theme import ThemePreference
from guardian.services import (
    DependencySnapshot,
    MailboxSnapshot,
    NetworkSnapshot,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, default=1366)
    parser.add_argument("--height", type=int, default=768)
    parser.add_argument("--suffix", default="")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    application = QApplication([])
    application.setFont(QFont("Segoe UI", 9))
    for preference in (ThemePreference.LIGHT, ThemePreference.DARK):
        settings = QSettings(
            str(args.output / f"{preference.value}.ini"),
            QSettings.Format.IniFormat,
        )
        settings.setValue("ui/theme", preference.value)
        runtime = ShellRuntime()
        runtime.config.callsign = "OK7PS"
        runtime.config.radio_backend = "hamlib"
        runtime.config.radio = "IC-7300"
        runtime.config.vara_mode = "HF"
        runtime.config.payload_backend = "vara_p2p"
        runtime.snapshots.update(
            mailbox=MailboxSnapshot(inbox=12, unread=3, outbox=2, transit=1),
            network=NetworkSnapshot(active_sessions=1, heard_stations=7),
            dependencies=DependencySnapshot(
                hamlib_available=True,
                hamlib_path=r"C:\ARDOS\hamlib\rigctld.exe",
            ),
        )
        runtime.events.publish("Station profile loaded.", source="config")
        runtime.events.publish("Hamlib dependency is ready.", source="dependency")
        runtime.events.publish("Waiting for radio connection.", source="radio")
        window = GuardianMainWindow(runtime, settings)
        window.resize(args.width, args.height)
        window.show()
        application.processEvents()
        suffix = f"-{args.suffix}" if args.suffix else ""
        window.grab().save(
            str(
                args.output
                / f"{args.width}x{args.height}-{preference.value}{suffix}.png"
            )
        )
        window.close()
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
