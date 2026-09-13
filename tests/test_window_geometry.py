import os
import gc
import weakref

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QRect
from PySide6.QtWidgets import QApplication, QDialog
from shiboken6 import isValid

import guardian.qt.window_geometry as window_geometry


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_fit_window_caps_client_size_and_centers_in_injected_work_area(monkeypatch):
    _application()
    area = QRect(100, 50, 800, 600)
    monkeypatch.setattr(
        window_geometry,
        "available_screen_geometry",
        lambda widget=None: QRect(area),
    )
    dialog = QDialog()
    dialog.resize(1200, 900)
    try:
        target = window_geometry.fit_dialog_to_screen(
            dialog,
            preferred_size=(1200, 900),
            minimum_size=(900, 700),
            margin=20,
        )
        frame = dialog.frameGeometry()
        usable = QRect(120, 70, 760, 560)
        assert target.width() <= usable.width()
        assert target.height() <= usable.height()
        assert dialog.minimumSize().width() <= target.width()
        assert dialog.minimumSize().height() <= target.height()
        assert usable.contains(frame.topLeft())
        assert usable.contains(frame.bottomRight())
        assert frame.center() == usable.center()
    finally:
        dialog.close()


def test_fit_window_caps_a_readable_floor_when_screen_is_smaller(monkeypatch):
    _application()
    area = QRect(0, 0, 480, 320)
    monkeypatch.setattr(
        window_geometry,
        "available_screen_geometry",
        lambda widget=None: QRect(area),
    )
    dialog = QDialog()
    try:
        target = window_geometry.fit_window_to_screen(
            dialog,
            preferred_size=(900, 700),
            minimum_size=(700, 500),
            margin=16,
        )
        assert target.width() <= 448
        assert target.height() <= 288
        assert dialog.minimumSize().width() <= target.width()
        assert dialog.minimumSize().height() <= target.height()
    finally:
        dialog.close()


def test_delayed_show_refits_to_new_work_area_and_keeps_user_size(monkeypatch):
    app = _application()
    area = QRect(0, 0, 1600, 1000)
    monkeypatch.setattr(window_geometry, "available_screen_geometry", lambda widget=None: QRect(area))
    dialog = QDialog()
    window_geometry.fit_dialog_to_screen(dialog, (1000, 800), (500, 300))
    area = QRect(0, 0, 911, 472)
    try:
        dialog.show()
        for _ in range(3):
            app.processEvents()
        assert area.contains(dialog.frameGeometry())
        dialog.resize(600, 350)
        dialog.move(80, 60)
        before = dialog.frameGeometry()
        area = QRect(0, 0, 1920, 1080)
        dialog._screen_fit.schedule()
        app.processEvents()
        assert dialog.frameGeometry() == before
        assert dialog.maximumWidth() > area.width()
    finally:
        dialog.close()


def test_screen_tracking_stops_when_closed_and_restarts_when_shown(monkeypatch):
    app = _application()
    area = QRect(0, 0, 1600, 1000)
    monkeypatch.setattr(window_geometry, "available_screen_geometry", lambda widget=None: QRect(area))
    dialog = QDialog()
    window_geometry.fit_dialog_to_screen(dialog, (1000, 800), (500, 300))
    watcher = dialog._screen_fit
    try:
        dialog.show()
        app.processEvents()
        assert watcher._connections
        watcher.schedule()
        dialog.close()
        assert not watcher.timer.isActive()
        assert not watcher._connections
        area = QRect(0, 0, 800, 500)
        dialog.show()
        app.processEvents()
        assert area.contains(dialog.frameGeometry())
        assert watcher._connections
    finally:
        dialog.close()
        dialog.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(watcher)


def test_screen_tracking_does_not_resurrect_garbage_collected_windows():
    app = _application()
    for _ in range(25):
        dialog = QDialog()
        window_geometry.fit_dialog_to_screen(dialog, (500, 300), (300, 200))
        dialog.show()
        app.processEvents()
        dialog.close()
        # Real dialogs can have callbacks/attributes forming Python cycles.
        dialog._cycle = dialog
        reference = weakref.ref(dialog)
        del dialog
        gc.collect()
        app.processEvents()
        assert reference() is None
