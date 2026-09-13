import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt
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
    assert dialog._tracked_widgets == []
    assert dialog._tracked_parent is None
    assert dialog._tracked_screen is None
    assert not dialog._recenter_timer.isActive()
    assert app.activeModalWidget() is None
    parent.close()


def test_startup_show_again_preserves_animation_clock_and_tracks_parent():
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent.setGeometry(200, 120, 400, 300)
    parent.show()
    dialog = StartupAnimation(parent)
    dialog.show()
    QTest.qWait(40)
    before = dialog._clock.elapsed()
    dialog.hide()
    parent.move(210, 130)
    QTest.qWait(40)
    dialog.show()
    app.processEvents()
    assert dialog._clock.elapsed() >= before + 30
    assert dialog.frameGeometry().center() == parent.frameGeometry().center()
    dialog._finished = True
    dialog._timer.stop()
    dialog.close()
    assert dialog._tracked_widgets == []
    parent.close()


def test_startup_retracks_parent_geometry_without_restarting_animation():
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent.setGeometry(200, 120, 400, 300)
    parent.show()
    dialog = StartupAnimation(parent)
    dialog.show()
    app.processEvents()

    assert dialog.frameGeometry().center() == parent.frameGeometry().center()
    QTest.qWait(35)
    elapsed_before = dialog._clock.elapsed()

    parent.resize(450, 320)
    parent.move(220, 140)
    app.processEvents()
    QTest.qWait(35)

    assert dialog.frameGeometry().center() == parent.frameGeometry().center()
    assert dialog._clock.elapsed() > elapsed_before

    dialog._finished = True
    dialog._timer.stop()
    dialog.close()
    parent.close()


def test_startup_is_bounded_by_a_small_available_screen(monkeypatch):
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent.setGeometry(600, 500, 300, 200)
    parent.show()
    dialog = StartupAnimation(parent)
    dialog.show()
    app.processEvents()

    available = QRect(100, 200, 320, 240)
    screen = SimpleNamespace(availableGeometry=lambda: available)
    monkeypatch.setattr(dialog, "_target_screen", lambda: screen)
    dialog._recenter()

    frame = dialog.frameGeometry()
    assert frame.left() >= available.left()
    assert frame.top() >= available.top()
    assert frame.right() <= available.right()
    assert frame.bottom() <= available.bottom()
    assert frame.width() <= available.width()
    assert frame.height() <= available.height()

    dialog._finished = True
    dialog._timer.stop()
    dialog.close()
    parent.close()
