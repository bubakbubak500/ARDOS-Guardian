import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest, QSignalSpy
from PySide6.QtWidgets import QApplication, QWidget

from guardian.qt.startup_animation import StartupAnimation


def test_startup_blocks_dismissal_then_releases_parent():
    app = QApplication.instance() or QApplication([])
    # Notification tests keep the singleton emergency window alive until Qt's
    # deferred-delete pass. Clear that prior test state before asserting that
    # StartupAnimation itself owns application modality; the dismissal checks
    # below remain unchanged.
    for widget in app.topLevelWidgets():
        if widget.objectName() == "EmergencyDialog":
            shutdown = getattr(widget, "shutdown", None)
            if callable(shutdown):
                shutdown()
            widget.deleteLater()
    app.processEvents()
    parent = QWidget()
    parent.show()
    dialog = StartupAnimation(parent)
    finished = QSignalSpy(dialog.finished)
    dialog.show()
    app.processEvents()
    assert app.activeModalWidget() is dialog
    assert dialog._renderer.isValid()
    assert len(dialog._cells) > 1000
    dialog._time = 2.5
    image = dialog.grab().toImage()
    background = image.pixelColor(0, 0)
    assert any(image.pixelColor(x, y) != background
               for y in range(60, 400, 3) for x in range(100, 460, 3))
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    QTest.keyClick(dialog, Qt.Key.Key_F4, Qt.KeyboardModifier.AltModifier)
    dialog.close()
    dialog.reject()
    dialog.accept()
    dialog.done(0)
    app.processEvents()
    assert dialog.isVisible()
    assert finished.count() == 0
    assert finished.wait(6500)
    assert dialog._clock.elapsed() >= 5000
    assert not dialog.isVisible()
    assert not dialog._timer.isActive()
    assert app.activeModalWidget() is None
    parent.close()
