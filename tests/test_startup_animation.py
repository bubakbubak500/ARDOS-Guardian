import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest, QSignalSpy
from PySide6.QtWidgets import QApplication, QWidget

from guardian.qt.startup_animation import StartupAnimation


def test_production_bootstrap_waits_for_animation_before_readiness(monkeypatch):
    from guardian.qt import app as bootstrap
    from guardian.qt import startup_animation

    events = []
    class Signal:
        def connect(self, callback):
            self.callback = callback

    class FakeApplication:
        aboutToQuit = Signal()
        def __getattr__(self, name):
            return lambda *args: None
        def exec(self):
            assert events == ["window", "animation"]
            window.startup_animation.finished.callback()

    class FakeAnimation:
        def __init__(self, parent):
            assert parent is window
            self.finished = Signal()
        def show(self):
            events.append("animation")

    window = SimpleNamespace(
        show=lambda: events.append("window"),
        show_spectrum_if_applicable=lambda: events.append("spectrum"),
        show_readiness_if_needed=lambda: events.append("readiness"),
    )
    monkeypatch.delenv("GUARDIAN_STARTUP_PREVIEW", raising=False)
    monkeypatch.setattr(bootstrap, "QApplication", SimpleNamespace(instance=lambda: FakeApplication()))
    monkeypatch.setattr(bootstrap, "QSettings", lambda: SimpleNamespace(value=lambda *args: "en"))
    monkeypatch.setattr(bootstrap, "ShellRuntime", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(bootstrap, "GuardianMainWindow", lambda *args: window)
    monkeypatch.setattr(bootstrap, "start_probe_from_environment", lambda *args: None)
    monkeypatch.setattr(bootstrap, "QTimer", SimpleNamespace(singleShot=lambda delay, callback: callback()))
    monkeypatch.setattr(startup_animation, "StartupAnimation", FakeAnimation)
    bootstrap.main()
    assert events == ["window", "animation", "spectrum", "readiness"]


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
