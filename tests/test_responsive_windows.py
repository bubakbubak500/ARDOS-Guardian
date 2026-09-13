"""Controls remain usable when logical desktop space is reduced by scaling."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect, QSettings
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QScrollArea, QPushButton

from guardian.i18n import language, set_language
from guardian.qt import window_geometry
from guardian.qt.runtime import ShellRuntime
from guardian.qt.settings_dialog import SettingsDialog
from guardian.qt.shell import GuardianMainWindow
from guardian.qt.theme import ThemePreference
from guardian.qt.map_window import MapWindow


@pytest.mark.parametrize("work_area", [(1366, 728), (1093, 574), (911, 472), (1280, 680)])
def test_settings_and_network_remain_usable_in_small_work_areas(tmp_path, monkeypatch, work_area):
    app = QApplication.instance() or QApplication([])
    for font in ("segoeui.ttf", "segoeuib.ttf"):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + font)
    previous_language = language()
    set_language("cs")
    area = QRect(0, 0, *work_area)
    monkeypatch.setattr(window_geometry, "available_screen_geometry", lambda widget=None: QRect(area))
    monkeypatch.setattr(ShellRuntime, "request_dependency_refresh", lambda self: False)
    runtime = ShellRuntime()
    settings = QSettings(str(tmp_path / "layout.ini"), QSettings.Format.IniFormat)
    window = GuardianMainWindow(runtime, settings)
    dialog = SettingsDialog(runtime.config, ThemePreference.SYSTEM, window, settings=settings)
    try:
        window.show()
        window._show_workspace("network")
        network = window.workspace_names["network"]
        network.tabs.setCurrentIndex(3)
        for _ in range(5):
            app.processEvents()
        assert area.contains(window.frameGeometry())
        for button in window.operational_header.findChildren(QPushButton):
            if button.isVisibleTo(window):
                assert button.height() >= button.minimumSizeHint().height()
        assert network.discovery_routes.viewport().height() >= 3 * 24
        assert network.discovery_pending.viewport().height() >= 2 * 24
        page = network.tabs.currentWidget()
        assert isinstance(page, QScrollArea)
        actions = page.widget().findChildren(QPushButton)
        assert actions
        page.ensureWidgetVisible(actions[-1])
        app.processEvents()
        last_rect = QRect(actions[-1].mapTo(page.viewport(), QPoint()), actions[-1].size())
        assert page.viewport().rect().contains(last_rect)
        dialog.show()
        for index in range(dialog.tabs.count()):
            dialog.tabs.setCurrentIndex(index)
            for _ in range(3):
                app.processEvents()
            assert area.contains(dialog.frameGeometry())
            for button in dialog.buttons.buttons():
                assert area.contains(QRect(button.mapToGlobal(QPoint()), button.size()))
            assert isinstance(dialog.tabs.currentWidget(), QScrollArea)
        dialog.tabs.setCurrentIndex(1)
        app.processEvents()
        page = dialog.tabs.currentWidget()
        page.ensureWidgetVisible(dialog.radio_profile_picker)
        app.processEvents()
        picker_rect = QRect(dialog.radio_profile_picker.mapTo(page.viewport(), QPoint()), dialog.radio_profile_picker.size())
        assert page.viewport().rect().contains(picker_rect)
    finally:
        dialog.close()
        window.close()
        runtime.close()
        set_language(previous_language)


def test_map_tools_scroll_without_compressing_controls(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    area = QRect(0, 0, 911, 472)
    monkeypatch.setattr(window_geometry, "available_screen_geometry", lambda widget=None: QRect(area))
    monkeypatch.setattr(ShellRuntime, "request_dependency_refresh", lambda self: False)
    runtime = ShellRuntime()
    runtime.config.map_background = False
    dialog = MapWindow(runtime, maps_directory=tmp_path)
    try:
        dialog.show()
        for _ in range(5):
            app.processEvents()
        assert area.contains(dialog.frameGeometry())
        assert dialog.content_scroll.verticalScrollBar().maximum() > 0
        for button in dialog.findChildren(QPushButton):
            if button.isVisibleTo(dialog):
                assert button.height() >= button.minimumSizeHint().height()
        dialog.content_scroll.ensureWidgetVisible(dialog.measure_button)
        app.processEvents()
        viewport = dialog.content_scroll.viewport()
        assert viewport.rect().contains(QRect(dialog.measure_button.mapTo(viewport, QPoint()), dialog.measure_button.size()))
        for box in dialog.findChildren(QDialogButtonBox):
            for button in box.buttons():
                assert area.contains(QRect(button.mapToGlobal(QPoint()), button.size()))
    finally:
        dialog.close()
        runtime.close()
