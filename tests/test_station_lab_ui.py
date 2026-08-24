from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QMessageBox

from guardian.config import StationConfig
from guardian.operations import StationLabStatus
from guardian.qt.station_lab_workspace import StationLabWorkspace
from guardian.station_lab import CalibrationState


def _app():
    return QApplication.instance() or QApplication([])


class _Operations:
    def __init__(self):
        self.station_lab = StationLabStatus()
        self.started = None
        self.accepted = False

    def start_station_calibration(self, peer, mode):
        self.started = (peer, mode)
        self.station_lab = StationLabStatus(
            state=CalibrationState.OFFERING.value, peer=peer,
            session_id=10, mode=mode, message="calling",
        )
        return True

    def accept_station_calibration(self):
        self.accepted = True
        return True

    def reject_station_calibration(self):
        return True

    def cancel_station_calibration(self):
        self.station_lab.state = CalibrationState.CANCELLED.value
        return True


def test_workspace_has_single_start_flow_and_incoming_consent(tmp_path, monkeypatch):
    _app()
    config = StationConfig()
    config.save = lambda path=None: tmp_path / "config.json"
    operations = _Operations()
    workspace = StationLabWorkspace(SimpleNamespace(config=config, operations=operations))
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
    )
    try:
        assert len(workspace.segments) == 10
        assert not hasattr(workspace, "windows_gain")
        assert not hasattr(workspace, "mode")
        workspace.peer.setText("ok2ipw")
        workspace.start.click()
        assert operations.started == ("OK2IPW", "quick")
        workspace.refresh()
        assert workspace.cancel.isVisible() is False  # parent is not shown
        assert not workspace.start.isEnabled()

        operations.station_lab = StationLabStatus(
            state=CalibrationState.WAITING_APPROVAL.value,
            peer="OK1AAA", session_id=22, pending_offer=True,
            message="incoming test",
        )
        workspace.refresh()
        assert not workspace.offer.isHidden()
        workspace.accept.click()
        assert operations.accepted
    finally:
        workspace.close()
