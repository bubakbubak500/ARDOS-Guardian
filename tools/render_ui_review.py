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
from guardian.install.dependencies import DependencyKind, DependencyStatus
from guardian.qt.help_dialog import HelpDialog
from guardian.qt.mail_workspace import ComposeDialog
from guardian.qt.readiness_dialog import ReadinessDialog
from guardian.qt.runtime import ShellRuntime
from guardian.qt.shell import GuardianMainWindow
from guardian.qt.theme import ThemeController, ThemePreference
from guardian.routing import Route


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

    heard_window = GuardianMainWindow(runtime, settings)
    heard_window.resize(1366, 768)
    heard_window._show_workspace("network")
    heard_window.workspace_names["network"].tabs.setCurrentIndex(1)
    _save(heard_window, args.output / "dark-network-heard-cs.png", application)

    runtime.config.discovery_mode = "assisted"
    runtime.config.discovery_auto_use = True
    runtime.config.link_advert_enabled = True
    runtime.operations.apply_network_settings()
    discovery_window = GuardianMainWindow(runtime, settings)
    discovery_window.resize(1366, 768)
    discovery_window._show_workspace("network")
    discovery_window.workspace_names["network"].tabs.setCurrentIndex(3)
    _save(
        discovery_window,
        args.output / "dark-network-discovery-cs.png",
        application,
    )

    live_window = GuardianMainWindow(runtime, settings)
    live_window.resize(1366, 768)
    live_window._show_workspace("network")
    live_window.workspace_names["network"].tabs.setCurrentIndex(4)
    _save(
        live_window,
        args.output / "dark-network-live-topology-cs.png",
        application,
    )

    runtime.config.separate_working_channels = True
    runtime.routes.add(
        Route("OK2IPW", "", "", 145_500_000, "FM", 145_550_000, "FM")
    )
    working_window = GuardianMainWindow(runtime, settings)
    working_window.resize(1366, 768)
    working_window._show_workspace("network")
    _save(
        working_window,
        args.output / "dark-network-working-cs.png",
        application,
    )

    theme = ThemeController(settings)
    compose = ComposeDialog(runtime)
    compose.template.setCurrentIndex(compose.template.findData("ICS-213"))
    compose.resize(820, 760)
    _save(compose, args.output / "compose-ics213-cs.png", application)

    help_dialog = HelpDialog()
    help_dialog.resize(1050, 720)
    _save(help_dialog, args.output / "help-cs.png", application)

    runtime.dependency_statuses = (
        DependencyStatus(
            DependencyKind.HAMLIB,
            "Hamlib / rigctld",
            True,
            r"C:\Guardian\hamlib\rigctld.exe",
            r"C:\Guardian\hamlib\rigctld.exe",
        ),
        DependencyStatus(
            DependencyKind.VARA_FM,
            "VARA FM",
            False,
            None,
            "missing",
            "https://downloads.winlink.org/VARA%20Products/",
            True,
        ),
        DependencyStatus(
            DependencyKind.VARA_HF,
            "VARA HF",
            False,
            None,
            "missing",
            "https://downloads.winlink.org/VARA%20Products/",
            True,
        ),
    )
    readiness = ReadinessDialog(runtime, settings)
    readiness._scan_pending = False
    readiness._render()
    readiness.resize(1040, 520)
    _save(readiness, args.output / "readiness-vara-cs.png", application)

    theme.deleteLater()
    runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
