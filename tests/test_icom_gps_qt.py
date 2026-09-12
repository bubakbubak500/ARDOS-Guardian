"""Qt/map integration checks for the one-shot IC-705 GPS source."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, Signal
from PySide6.QtWidgets import QApplication

from guardian.location import LocationFailure, LocationFix, LocationSource
from guardian.qt.runtime import ShellRuntime
from guardian.services.snapshots import RadioSnapshot


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _connected_ic705(runtime) -> None:
    runtime.config.radio = "Icom IC-705"
    runtime.config.rig_model = 3085
    runtime.config.radio_backend = "hamlib"
    runtime.snapshots.update(
        radio=RadioSnapshot(connected=True, name="hamlib")
    )


def _ic705_ports():
    return [
        "COM7 — IC-705 Serial Port A (CI-V)",
        "COM8 — IC-705 Serial Port B",
    ]


class _FakeGpsRequest(QObject):
    fix_ready = Signal(object)
    failed = Signal(object, str)
    state_changed = Signal(str)

    def __init__(self, port: str, parent, outcome, **kwargs) -> None:
        super().__init__(parent)
        self.port = port
        self.cat_port = kwargs.get("cat_port", "")
        self.outcome = outcome
        self.cancelled = False

    @property
    def active(self) -> bool:
        return False

    def start(self) -> None:
        self.state_changed.emit("locating")
        if isinstance(self.outcome, LocationFailure):
            self.failed.emit(self.outcome, "test")
        else:
            self.fix_ready.emit(self.outcome)

    def cancel(self) -> None:
        self.cancelled = True


def test_ic705_gps_uses_preview_and_applies_only_the_locator(
    tmp_path, monkeypatch
) -> None:
    _application()
    import guardian.qt.map_window as map_module
    from guardian.qt.map_window import MapWindow
    from guardian.routing import MAX_LOCATOR_CHARS, to_locator

    monkeypatch.setattr(map_module, "list_serial_ports", _ic705_ports)
    runtime = ShellRuntime()
    runtime.mailstore.root = tmp_path / "mail"
    runtime.config.map_background = False
    runtime.config.station_grid = "JN89HE"
    runtime.config.beacon_position = False
    runtime.config.cat_port = "COM7"
    # A stale remembered port must not override the fresh, explicit USB(B)
    # identification.
    runtime.config.gps_port = "COM99"
    _connected_ic705(runtime)
    fix = LocationFix(
        50.0755,
        14.4378,
        None,
        LocationSource.GPS,
        datetime.now(timezone.utc),
    )
    created: list[tuple[str, str]] = []

    def factory(port, parent, **kwargs):
        created.append((port, kwargs.get("cat_port", "")))
        return _FakeGpsRequest(port, parent, fix, **kwargs)

    window = MapWindow(runtime, gps_request_factory=factory)
    try:
        window.show()
        _application().processEvents()
        assert not window.gps_button.isHidden()
        assert not hasattr(window, "gps_port")
        assert not hasattr(window, "gps_refresh")
        window._detect_icom_gps()

        assert created == [("COM8", "COM7")]
        assert runtime.config.station_grid == "JN89HE"
        assert window._detected_grid == to_locator(
            fix.latitude, fix.longitude, MAX_LOCATOR_CHARS
        )
        assert "unknown" in window.detected_text.text().lower()
        assert window.detected_panel.isVisibleTo(window)

        expected = to_locator(fix.latitude, fix.longitude, MAX_LOCATOR_CHARS)
        window._use_detected()

        assert runtime.config.station_grid == expected
        assert runtime.config.beacon_position is False
        stored = runtime.config.save().read_text(encoding="utf-8")
        assert "50.0755" not in stored
        assert "14.4378" not in stored
    finally:
        window.close()
        runtime.close()


def test_ic705_gps_without_safe_port_never_guesses_or_opens_a_serial_device(
    monkeypatch,
) -> None:
    _application()
    import guardian.qt.map_window as map_module
    from guardian.qt.map_window import MapWindow

    monkeypatch.setattr(
        map_module,
        "list_serial_ports",
        lambda: [
            "COM7 — IC-705 Serial Port A (CI-V)",
            "COM8 — USB Serial Port",
        ],
    )
    runtime = ShellRuntime()
    runtime.config.map_background = False
    runtime.config.cat_port = "COM7"
    runtime.config.gps_port = "COM8"
    _connected_ic705(runtime)
    started: list[bool] = []

    def forbidden_factory(*_args, **_kwargs):
        started.append(True)
        raise AssertionError("CAT/PTT port must never be used for GPS")

    window = MapWindow(runtime, gps_request_factory=forbidden_factory)
    try:
        window.show()
        _application().processEvents()
        assert not window.gps_button.isHidden()
        window._detect_icom_gps()
        assert started == []
        assert "unrelated" in window.location_status.text().lower()
    finally:
        window.close()
        runtime.close()


def test_ic705_gps_refuses_ambiguous_usb_b_ports(monkeypatch) -> None:
    _application()
    import guardian.qt.map_window as map_module
    from guardian.qt.map_window import MapWindow

    monkeypatch.setattr(
        map_module,
        "list_serial_ports",
        lambda: [
            "COM7 — IC-705 Serial Port A (CI-V)",
            "COM8 — IC-705 Serial Port B",
            "COM9 — IC-705 Serial Port B",
        ],
    )
    runtime = ShellRuntime()
    runtime.config.map_background = False
    runtime.config.cat_port = "COM7"
    _connected_ic705(runtime)
    started: list[bool] = []

    def forbidden_factory(*_args, **_kwargs):
        started.append(True)
        raise AssertionError("ambiguous GPS port must not be opened")

    window = MapWindow(runtime, gps_request_factory=forbidden_factory)
    try:
        window._detect_icom_gps()
        assert started == []
        assert "guess" in window.location_status.text().lower()
    finally:
        window.close()
        runtime.close()


def test_ic705_gps_button_requires_selected_connected_radio() -> None:
    _application()
    from guardian.qt.map_window import MapWindow

    runtime = ShellRuntime()
    runtime.config.map_background = False
    window = MapWindow(runtime)
    try:
        window.show()
        _application().processEvents()
        assert window.gps_button.isHidden()

        _connected_ic705(runtime)
        window.refresh()
        assert not window.gps_button.isHidden()

        runtime.snapshots.update(
            radio=RadioSnapshot(connected=False, name="hamlib")
        )
        window.refresh()
        assert window.gps_button.isHidden()

        runtime.config.radio = "Icom IC-7300"
        runtime.config.rig_model = 0
        runtime.snapshots.update(
            radio=RadioSnapshot(connected=True, name="hamlib")
        )
        window.refresh()
        assert window.gps_button.isHidden()
    finally:
        window.close()
        runtime.close()


def test_ic705_gps_visibility_refresh_does_not_scan_serial_ports(monkeypatch) -> None:
    _application()
    import guardian.qt.map_window as map_module
    from guardian.qt.map_window import MapWindow

    calls: list[bool] = []

    def ports():
        calls.append(True)
        return _ic705_ports()

    monkeypatch.setattr(map_module, "list_serial_ports", ports)
    runtime = ShellRuntime()
    runtime.config.map_background = False
    _connected_ic705(runtime)
    window = MapWindow(runtime)
    try:
        window.refresh()
        window.refresh()
        assert calls == []
        window._resolve_ic705_gps_port()
        assert calls == [True]
    finally:
        window.close()
        runtime.close()


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    app = _application()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(predicate())


def test_ic705_request_cancel_ignores_late_worker_result(monkeypatch) -> None:
    _application()
    import guardian.qt.location as location_module
    from guardian.qt.location import IcomGpsRequest

    started = threading.Event()
    calls = 0
    fix = LocationFix(50.0, 14.0, None, LocationSource.GPS)

    def read(_port, *, stop_event, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            while not stop_event.is_set():
                time.sleep(0.005)
            return fix
        return fix

    monkeypatch.setattr(location_module, "read_icom_gps", read)
    request = IcomGpsRequest("COM8", timeout_ms=1_000)
    fixes: list[LocationFix] = []
    failures: list[LocationFailure] = []
    request.fix_ready.connect(fixes.append)
    request.failed.connect(lambda failure, _detail: failures.append(failure))
    try:
        request.start()
        assert started.wait(1.0)
        request.cancel()
        assert failures == [LocationFailure.CANCELLED]

        # A cancelled worker may finish after the next one has started. Its
        # result must not overwrite the newer request.
        request.start()
        assert _wait_for(lambda: len(fixes) == 1)
        assert calls >= 2
        assert fixes == [fix]
    finally:
        request.cancel()
        request.deleteLater()
        _wait_for(lambda: not location_module._LIVE_ICOM_GPS_THREADS, timeout=2.0)


def test_closing_map_cancels_a_blocking_ic705_read(monkeypatch) -> None:
    _application()
    import guardian.qt.location as location_module
    import guardian.qt.map_window as map_module
    from guardian.qt.location import IcomGpsRequest
    from guardian.qt.map_window import MapWindow

    started = threading.Event()
    stopped = threading.Event()

    def read(_port, *, stop_event, **_kwargs):
        started.set()
        while not stop_event.is_set():
            time.sleep(0.005)
        stopped.set()
        return None

    monkeypatch.setattr(location_module, "read_icom_gps", read)
    monkeypatch.setattr(map_module, "list_serial_ports", _ic705_ports)
    runtime = ShellRuntime()
    runtime.config.map_background = False
    runtime.config.cat_port = "COM7"
    _connected_ic705(runtime)
    window = MapWindow(
        runtime,
        gps_request_factory=lambda port, parent, **kwargs: IcomGpsRequest(
            port, parent, timeout_ms=2_000, **kwargs
        ),
    )
    try:
        window._detect_icom_gps()
        assert started.wait(1.0)
        window.close()
        assert stopped.wait(1.0)
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert _wait_for(
            lambda: not location_module._LIVE_ICOM_GPS_THREADS,
            timeout=2.0,
        )
    finally:
        window.close()
        runtime.close()


def test_destroying_ic705_request_parent_stops_worker_and_releases_thread(
    monkeypatch,
) -> None:
    _application()
    import guardian.qt.location as location_module
    from guardian.qt.location import IcomGpsRequest

    started = threading.Event()
    stopped = threading.Event()

    def read(_port, *, stop_event, **_kwargs):
        started.set()
        while not stop_event.is_set():
            time.sleep(0.005)
        stopped.set()
        return None

    monkeypatch.setattr(location_module, "read_icom_gps", read)
    parent = QObject()
    request = IcomGpsRequest("COM8", parent, timeout_ms=2_000)
    request.start()
    assert started.wait(1.0)

    # Parent deletion bypasses MapWindow.closeEvent, so this exercises the
    # request's destroyed -> stop_event cleanup directly.
    parent.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert stopped.wait(1.0)
    assert _wait_for(
        lambda: not location_module._LIVE_ICOM_GPS_THREADS,
        timeout=2.0,
    )


def test_ic705_request_has_an_overall_timeout(monkeypatch) -> None:
    _application()
    import guardian.qt.location as location_module
    from guardian.qt.location import IcomGpsRequest

    started = threading.Event()
    stopped = threading.Event()

    def read(_port, *, stop_event, **_kwargs):
        started.set()
        while not stop_event.is_set():
            time.sleep(0.005)
        stopped.set()
        return None

    monkeypatch.setattr(location_module, "read_icom_gps", read)
    request = IcomGpsRequest("COM8", timeout_ms=30)
    failures: list[LocationFailure] = []
    request.failed.connect(lambda failure, _detail: failures.append(failure))
    try:
        request.start()
        assert started.wait(1.0)
        assert _wait_for(lambda: failures == [LocationFailure.TIMEOUT])
        assert stopped.wait(1.0)
    finally:
        request.cancel()
        request.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        _wait_for(lambda: not location_module._LIVE_ICOM_GPS_THREADS, timeout=2.0)
