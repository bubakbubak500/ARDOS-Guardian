"""Render the localized UI states covered by the current visual review."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

os.environ["APPDATA"] = tempfile.mkdtemp(prefix="guardian-ui-review-")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from guardian.i18n import Language, set_language
from guardian.qt.help_dialog import HelpDialog
from guardian.qt.mail_workspace import ComposeDialog
from guardian.qt.runtime import ShellRuntime
from guardian.qt.shell import GuardianMainWindow
from guardian.qt.theme import ThemeController, ThemePreference


def _save(widget, path: Path, application: QApplication) -> None:
    widget.show()
    application.processEvents()
    if not widget.grab().save(str(path)):
        raise RuntimeError(f"Could not save {path}")
    widget.close()
    application.processEvents()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    application = QApplication([])
    application.setFont(QFont("Segoe UI", 9))
    settings = QSettings(
        str(args.output / "review.ini"),
        QSettings.Format.IniFormat,
    )
    settings.setValue("ui/theme", ThemePreference.DARK.value)
    settings.setValue("ui/language", Language.CZECH.value)
    set_language(Language.CZECH)

    runtime = ShellRuntime()
    runtime.config.callsign = "OK7PS"
    runtime.config.radio_backend = "hamlib"
    runtime.config.radio = "IC-7300"
    runtime.config.vara_mode = "HF"

    window = GuardianMainWindow(runtime, settings)
    window.resize(1366, 768)
    window._show_workspace("network")
    _save(window, args.output / "dark-network-cs.png", application)

    theme = ThemeController(settings)
    compose = ComposeDialog(runtime)
    compose.template.setCurrentIndex(compose.template.findData("ICS-213"))
    compose.resize(820, 760)
    _save(compose, args.output / "compose-ics213-cs.png", application)

    help_dialog = HelpDialog()
    help_dialog.resize(1050, 720)
    _save(help_dialog, args.output / "help-cs.png", application)

    theme.deleteLater()
    runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
