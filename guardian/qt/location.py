"""Consent-first Windows Location Service bridge for the Qt map.

WinRT completions can arrive on arbitrary threads.  Private Qt signals marshal
them back to the object's UI thread before results or UI state are touched.
"""

from __future__ import annotations

from datetime import timedelta
import importlib
import sys
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from ..location import LocationFailure, LocationFix, LocationSource


SOURCE_NAMES = {
    "CELLULAR": LocationSource.CELLULAR,
    "SATELLITE": LocationSource.SATELLITE,
    "WI_FI": LocationSource.WIFI,
    "IP_ADDRESS": LocationSource.IP,
    "DEFAULT": LocationSource.DEFAULT,
    "OBFUSCATED": LocationSource.OBFUSCATED,
    "UNKNOWN": LocationSource.UNKNOWN,
}


def _load_api():
    # PyWinRT ships namespace packages without importable parent __init__
    # modules.  Import the generated projection by its complete name.
    return importlib.import_module("winrt.windows.devices.geolocation")


def _source(value: Any) -> LocationSource:
    return SOURCE_NAMES.get(getattr(value, "name", ""), LocationSource.UNKNOWN)


def _fix_from_position(position: Any) -> LocationFix:
    coordinate = position.coordinate
    return LocationFix(
        latitude=float(coordinate.latitude),
        longitude=float(coordinate.longitude),
        accuracy_m=float(coordinate.accuracy),
        source=_source(coordinate.position_source),
        timestamp=coordinate.timestamp,
    )


class WindowsLocationRequest(QObject):
    """A single position request, started after the map's explicit consent UI."""

    fix_ready = Signal(object)              # LocationFix
    failed = Signal(object, str)            # LocationFailure, safe diagnostic
    state_changed = Signal(str)             # locating
    _position_completed = Signal()

    def __init__(self, parent=None, *, timeout_ms: int = 60_000) -> None:
        super().__init__(parent)
        self._api = None
        self._position_operation = None
        self._geolocator = None
        self._active = False
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(timeout_ms)
        self._timeout.timeout.connect(self._timed_out)
        self._position_completed.connect(self._position_done)

    @property
    def active(self) -> bool:
        return self._active

    def start(self) -> None:
        if self._active:
            return
        if sys.platform != "win32":
            self.failed.emit(LocationFailure.UNAVAILABLE, "Windows only")
            return
        try:
            self._api = _load_api()
            self._active = True
            self.state_changed.emit("locating")
            geolocator = self._api.Geolocator()
            # If Windows cannot provide the requested precise fix, it may use
            # its consentless coarse/default source.  The map still labels the
            # reported accuracy and never saves without a second confirmation.
            geolocator.allow_fallback_to_consentless_positions()
            geolocator.desired_accuracy_in_meters = 50
            self._geolocator = geolocator
            operation = geolocator.get_geoposition_async_with_age_and_timeout(
                timedelta(minutes=2), timedelta(seconds=30)
            )
            self._position_operation = operation
            operation.completed = lambda _operation, _status: self._position_completed.emit()
            self._timeout.start()
        except (ImportError, ModuleNotFoundError) as exc:
            self._finish_failure(LocationFailure.UNAVAILABLE, self._safe_error(exc))
        except BaseException as exc:
            self._finish_failure(self._classify_error(exc), self._safe_error(exc))

    def cancel(self) -> None:
        if not self._active:
            return
        if self._position_operation is not None:
            try:
                self._position_operation.cancel()
            except BaseException:
                pass
        self._finish_failure(LocationFailure.CANCELLED, "cancelled by operator")

    def _position_done(self) -> None:
        if not self._active or self._position_operation is None:
            return
        try:
            fix = _fix_from_position(self._position_operation.get_results())
        except BaseException as exc:
            self._finish_failure(self._classify_error(exc), self._safe_error(exc))
            return
        self._active = False
        self._timeout.stop()
        self._clear_operations()
        self.fix_ready.emit(fix)

    def _timed_out(self) -> None:
        if self._position_operation is not None:
            try:
                self._position_operation.cancel()
            except BaseException:
                pass
        self._finish_failure(LocationFailure.TIMEOUT, "60 second request timeout")

    def _finish_failure(self, failure: LocationFailure, detail: str) -> None:
        was_active = self._active
        self._active = False
        self._timeout.stop()
        self._clear_operations()
        # Import/platform failures happen before _active is set and still need
        # to reach the map.  Late callbacks after cancellation do not.
        if was_active or failure in (LocationFailure.UNAVAILABLE, LocationFailure.ERROR):
            self.failed.emit(failure, detail)

    def _clear_operations(self) -> None:
        self._position_operation = None
        self._geolocator = None
        self._api = None

    def _classify_error(self, exc: BaseException) -> LocationFailure:
        if isinstance(exc, PermissionError):
            return LocationFailure.DENIED
        status = getattr(self._geolocator, "location_status", None)
        name = getattr(status, "name", "")
        if name == "DISABLED":
            return LocationFailure.DISABLED
        if name in ("NO_DATA", "NOT_INITIALIZED"):
            return LocationFailure.NO_DATA
        if name == "NOT_AVAILABLE":
            return LocationFailure.UNAVAILABLE
        text = str(exc).lower()
        if "access" in text and "denied" in text:
            return LocationFailure.DENIED
        if "timeout" in text or "timed out" in text:
            return LocationFailure.TIMEOUT
        return LocationFailure.ERROR

    @staticmethod
    def _safe_error(exc: BaseException) -> str:
        # No coordinate object is interpolated here.  The text is useful for
        # diagnostics without leaking a successful position.
        return f"{type(exc).__name__}: {str(exc)[:240]}"
