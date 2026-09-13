import os
import pytest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QFont, QFontDatabase

from guardian.qt.runtime import ShellRuntime
from guardian.qt.shell import GuardianMainWindow
from guardian.qt.warships_window import WarshipsWindow
from guardian.qt.network_workspace import NetworkWorkspace
from guardian.routing import Link, Topology, Route
from guardian.protocol.warships import Op


def test_hidden_access_editors_single_window_and_board_render(tmp_path):
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    app.setFont(QFont("Segoe UI", 10))
    runtime = ShellRuntime()
    shell = GuardianMainWindow(runtime, QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat))
    try:
        shell.show()
        shell.activateWindow()
        shell.setFocus()
        app.processEvents()
        QTest.keyClicks(shell, "IDKFA")
        app.processEvents()
        window = shell.warships_access.window
        assert isinstance(window, WarshipsWindow)
        window.close()
        assert not window.isVisible()
        shell.activateWindow()
        shell.setFocus()
        app.processEvents()
        QTest.keyClicks(shell, "idkfa")
        assert shell.warships_access.window is window
        game = runtime.warships.game
        game.state = "PLACING"
        game.board.randomize()
        window.refresh()
        assert window.ready_button.isEnabled()
        game.state = "MY_TURN"
        game.enemy[99] = Op.MISS
        window.target = 98
        window.refresh()
        assert window.fire_button.isEnabled()
        window.enemy.pulse(99, Op.MISS)
        QTest.qWait(50)
        app.processEvents()
        assert window.grab().save(str(tmp_path / "warships.png"))
        # Real editor focus must never unlock the code.
        window.close()
        shell.activateWindow()
        from PySide6.QtWidgets import QLineEdit
        editor = QLineEdit(shell)
        editor.show()
        editor.setFocus()
        app.processEvents()
        QTest.keyClicks(editor, "idkfa")
        assert editor.text() == "idkfa" and not window.isVisible()
    finally:
        shell.close()
        runtime.close()


def test_remove_topology_keeps_manual_routes_and_persists_empty(monkeypatch):
    app = QApplication.instance() or QApplication([])
    runtime = ShellRuntime()
    runtime.config.callsign = "A"
    runtime.topology = Topology([Link("A", "B")])
    runtime.routes.replace_topology(runtime.topology.derive_routes("A"))
    runtime.routes.add(Route(destination="C", preferred="D"))
    workspace = NetworkWorkspace(runtime)
    try:
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
        workspace._remove_topology()
        assert runtime.topology.links
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        workspace._remove_topology()
        assert not runtime.topology.links and not Topology.load().links
        assert runtime.routes.lookup("B") is None
        assert runtime.routes.lookup("C").preferred == "D"
        assert workspace.topology_table.rowCount() == 0
    finally:
        workspace.close()
        runtime.close()


@pytest.mark.parametrize("target_name", ["readiness", "activity", "button"])
def test_code_from_normal_focused_widgets_without_typing_deadline(tmp_path, target_name):
    from PySide6.QtWidgets import QPushButton
    app = QApplication.instance() or QApplication([])
    runtime = ShellRuntime()
    shell = GuardianMainWindow(runtime, QSettings(str(tmp_path / "keys.ini"), QSettings.Format.IniFormat))
    try:
        shell.show()
        shell.activateWindow()
        if target_name == "activity":
            shell._toggle_activity_panel(True)
        app.processEvents()
        target = (next(button for button in shell.findChildren(QPushButton) if button.isVisible() and button.isEnabled())
                  if target_name == "button" else getattr(shell, target_name))
        target.setFocus()
        app.processEvents()
        assert QApplication.focusWidget() is target
        QTest.keyClicks(target, "i")
        assert shell.warships_access.code == "i"  # no bubbling duplicates
        QTest.qWait(2200)  # deliberately slower than the old limit
        QTest.keyClicks(target, "dkfa")
        app.processEvents()
        assert shell.warships_access.window is not None
        assert shell.warships_access.window.isVisible()
    finally:
        shell.close()
        runtime.close()
