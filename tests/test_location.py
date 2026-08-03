import os
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from guardian.location import LocationFailure, LocationFix, LocationSource
from guardian.qt import location as qt_location


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_location_fix_rejects_invalid_or_impossible_values() -> None:
    fix = LocationFix(50.0755, 14.4378, 42.0, LocationSource.WIFI)
    assert not fix.is_approximate
    assert LocationFix(50.0, 14.0, 1_001).is_approximate
    with pytest.raises(ValueError):
        LocationFix(91, 14, 10)
    with pytest.raises(ValueError):
        LocationFix(50, 181, 10)
    with pytest.raises(ValueError):
        LocationFix(50, 14, -1)


def test_winrt_position_is_reduced_to_the_ephemeral_fix() -> None:
    timestamp = datetime.now(timezone.utc)
    coordinate = SimpleNamespace(
        latitude=50.0755,
        longitude=14.4378,
        accuracy=37.5,
        position_source=SimpleNamespace(name="WI_FI"),
        timestamp=timestamp,
    )
    fix = qt_location._fix_from_position(SimpleNamespace(coordinate=coordinate))
    assert fix == LocationFix(
        50.0755, 14.4378, 37.5, LocationSource.WIFI, timestamp
    )


class _Operation:
    def __init__(self, result=None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.completed = None
        self.cancelled = False

    def get_results(self):
        if self.error is not None:
            raise self.error
        return self.result

    def complete(self) -> None:
        assert self.completed is not None
        self.completed(self, None)

    def cancel(self) -> None:
        self.cancelled = True


def _api(position=None, error: BaseException | None = None):
    position_operation = _Operation(position, error)

    class Geolocator:
        desired_accuracy_in_meters = None
        location_status = SimpleNamespace(name="READY")

        def allow_fallback_to_consentless_positions(self):
            pass

        def get_geoposition_async_with_age_and_timeout(self, _age, _timeout):
            return position_operation

    return SimpleNamespace(
        Geolocator=Geolocator,
        position=position_operation,
    )


def test_windows_request_marshals_one_fix_after_ui_consent(monkeypatch) -> None:
    _application()
    coordinate = SimpleNamespace(
        latitude=50.0755,
        longitude=14.4378,
        accuracy=42.0,
        position_source=SimpleNamespace(name="SATELLITE"),
        timestamp=datetime.now(timezone.utc),
    )
    api = _api(position=SimpleNamespace(coordinate=coordinate))
    monkeypatch.setattr(qt_location, "_load_api", lambda: api)
    request = qt_location.WindowsLocationRequest(timeout_ms=5_000)
    states: list[str] = []
    fixes: list[LocationFix] = []
    failures: list[tuple[LocationFailure, str]] = []
    request.state_changed.connect(states.append)
    request.fix_ready.connect(fixes.append)
    request.failed.connect(lambda failure, detail: failures.append((failure, detail)))

    request.start()
    api.position.complete()

    assert states == ["locating"]
    assert failures == []
    assert fixes[0].source == LocationSource.SATELLITE
    assert fixes[0].accuracy_m == 42
    assert not request.active


def test_windows_request_reports_denial_and_timeout(monkeypatch) -> None:
    _application()
    denied_api = _api(error=PermissionError("access denied"))
    monkeypatch.setattr(qt_location, "_load_api", lambda: denied_api)
    denied = qt_location.WindowsLocationRequest(timeout_ms=5_000)
    denied_failures = []
    denied.failed.connect(lambda failure, _detail: denied_failures.append(failure))
    denied.start()
    denied_api.position.complete()
    assert denied_failures == [LocationFailure.DENIED]

    waiting_api = _api()
    monkeypatch.setattr(qt_location, "_load_api", lambda: waiting_api)
    waiting = qt_location.WindowsLocationRequest(timeout_ms=5_000)
    timeout_failures = []
    waiting.failed.connect(lambda failure, _detail: timeout_failures.append(failure))
    waiting.start()
    waiting._timed_out()
    assert waiting_api.position.cancelled
    assert timeout_failures == [LocationFailure.TIMEOUT]
