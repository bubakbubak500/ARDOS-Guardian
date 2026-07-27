"""PySide6 application bootstrap."""

from __future__ import annotations

import sys

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from .. import __app_name__, __version__
from ..i18n import set_language
from .runtime import ShellRuntime
from .shell import GuardianMainWindow
from .performance import start_probe_from_environment

def main() -> None:
    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName(__app_name__)
    application.setApplicationVersion(__version__)
    application.setOrganizationName("ARDOS")
    application.setOrganizationDomain("ardos.radio")
    application.setFont(QFont("Segoe UI", 9))
    settings = QSettings()
    set_language(str(settings.value("ui/language", "en")))
    runtime = ShellRuntime()

    window = GuardianMainWindow(runtime, settings)
    window.ui_performance_probe = start_probe_from_environment(window)
    application.aboutToQuit.connect(runtime.close)
    window.show()
    QTimer.singleShot(0, window.show_spectrum_if_applicable)
    QTimer.singleShot(0, window.show_readiness_if_needed)
    application.exec()
