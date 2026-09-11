"""Consent-first Windows Location Service bridge for the Qt map.

WinRT completions can arrive on arbitrary threads.  Private Qt signals marshal
them back to the object's UI thread before results or UI state are touched.
"""

from __future__ import annotations

from datetime import timedelta
import importlib
import sys
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from ..location import LocationFailure, LocationFix, LocationSource
from ..radio.icom_gps import (
    IcomGpsPortConflict,
    read_icom_gps,
    same_serial_port,
)


# A map window can be closed while the final bounded serial read is unwinding.
# Keep the native QThread object alive until Qt emits ``finished`` even if its
# request (and therefore the request's private set of threads) is deleted.
_LIVE_ICOM_GPS_THREADS: set[Any] = set()


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


class _IcomGpsThread(QThread):
    """Run the bounded pyserial read away from the Qt UI thread."""

    fix_ready = Signal(int, object)
    failed = Signal(int, object, str)
    finished_info = Signal(int)

    def __init__(
        self,
        token: int,
        port: str,
        stop_event: Event,
        *,
        timeout_seconds: float,
        max_age_seconds: float,
        serial_factory,
        now,
        forbidden_port: str,
    ) -> None:
        # Do not parent this thread to the request.  Cancellation is
        # cooperative and bounded; a request/window may be destroyed while a
        # final 200 ms serial read is unwinding.
        super().__init__()
        self.token = token
        self.port = port
        self.stop_event = stop_event
        self.timeout_seconds = timeout_seconds
        self.max_age_seconds = max_age_seconds
        self.serial_factory = serial_factory
        self.now = now
        self.forbidden_port = forbidden_port

    def run(self) -> None:
        try:
            try:
                fix = read_icom_gps(
                    self.port,
                    stop_event=self.stop_event,
                    timeout_seconds=self.timeout_seconds,
                    max_age_seconds=self.max_age_seconds,
                    serial_factory=self.serial_factory,
                    now=self.now,
                    forbidden_port=self.forbidden_port,
                )
            except BaseException as exc:
                self.failed.emit(
                    self.token,
                    _classify_gps_error(exc),
                    _safe_gps_error(exc),
                )
                return
            if self.stop_event.is_set():
                self.failed.emit(
                    self.token,
                    LocationFailure.CANCELLED,
                    "cancelled by operator",
                )
            elif fix is None:
                self.failed.emit(
                    self.token,
                    LocationFailure.NO_DATA,
                    "no recent valid GPS fix",
                )
            else:
                self.fix_ready.emit(self.token, fix)
        finally:
            # The bound receiver runs in the request's UI thread.  It performs
            # bookkeeping before QThread.finished invokes the independent
            # keepalive finalizer below.
            self.finished_info.emit(self.token)


def _classify_gps_error(exc: BaseException) -> LocationFailure:
    if isinstance(exc, PermissionError):
        return LocationFailure.DENIED
    if isinstance(exc, (FileNotFoundError, IcomGpsPortConflict)):
        return LocationFailure.UNAVAILABLE
    text = str(exc).lower()
    if "access denied" in text or "permission" in text:
        return LocationFailure.DENIED
    if "not found" in text or "no such file" in text or "port" in text:
        return LocationFailure.UNAVAILABLE
    return LocationFailure.ERROR


def _safe_gps_error(exc: BaseException) -> str:
    # Do not copy arbitrary serial-driver text into a diagnostic record.  A
    # driver normally reports only an OS error, but a custom backend could
    # include received NMEA data in its exception text.
    return f"{type(exc).__name__}"


def _release_icom_gps_thread(thread: Any) -> None:
    _LIVE_ICOM_GPS_THREADS.discard(thread)
    try:
        thread.deleteLater()
    except RuntimeError:
        # Qt already destroyed the wrapper after emitting ``finished``.
        pass


class IcomGpsRequest(QObject):
    """Read one current IC-705 GPS Out fix without touching CAT or PTT.

    ``port`` is the USB(B) GPS Out serial port.  ``cat_port`` is supplied by
    the map as a second guard: a GPS read must never open the configured CAT or
    RTS/DTR PTT device, even if a stale port label was left in the profile.
    """

    fix_ready = Signal(object)              # LocationFix
    failed = Signal(object, str)            # LocationFailure, safe diagnostic
    state_changed = Signal(str)             # locating

    def __init__(
        self,
        port: str,
        parent=None,
        *,
        cat_port: str = "",
        timeout_ms: int = 15_000,
        max_age_seconds: float = 120.0,
        serial_factory=None,
        now=None,
    ) -> None:
        super().__init__(parent)
        self.port = str(port or "").strip()
        self.cat_port = str(cat_port or "").strip()
        self.timeout_ms = max(1, int(timeout_ms))
        self.max_age_seconds = max_age_seconds
        self.serial_factory = serial_factory
        self.now = now
        self._active = False
        self._generation = 0
        self._stop_event: Event | None = None
        self._thread: _IcomGpsThread | None = None
        self._threads: set[_IcomGpsThread] = set()
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(self.timeout_ms)
        self._timeout.timeout.connect(self._timed_out)

    @property
    def active(self) -> bool:
        return self._active

    def start(self) -> None:
        if self._active:
            return
        if not self.port:
            self.failed.emit(LocationFailure.UNAVAILABLE, "GPS serial port is empty")
            return
        if same_serial_port(self.port, self.cat_port):
            self.failed.emit(
                LocationFailure.UNAVAILABLE,
                "GPS port is also the configured CAT/PTT port",
            )
            return

        self._generation += 1
        token = self._generation
        stop_event = Event()
        self._stop_event = stop_event
        # If a window destroys this request directly, the worker still gets a
        # cancellation signal without retaining a bound method on the dying
        # QObject.
        self.destroyed.connect(
            lambda _object=None, stop_event=stop_event: stop_event.set()
        )
        thread = _IcomGpsThread(
            token,
            self.port,
            stop_event,
            timeout_seconds=self.timeout_ms / 1_000.0,
            max_age_seconds=self.max_age_seconds,
            serial_factory=self.serial_factory,
            now=self.now,
            forbidden_port=self.cat_port,
        )
        self._thread = thread
        self._threads.add(thread)
        _LIVE_ICOM_GPS_THREADS.add(thread)
        thread.fix_ready.connect(self._thread_fix_ready)
        thread.failed.connect(self._thread_failed)
        thread.finished_info.connect(self._thread_finished)
        # This callback captures only the thread.  It remains a safe finalizer
        # when the request/window is destroyed before the worker finishes.
        thread.finished.connect(
            lambda thread=thread: _release_icom_gps_thread(thread)
        )
        self._active = True
        self.state_changed.emit("locating")
        self._timeout.start()
        thread.start()

    def cancel(self) -> None:
        if not self._active:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        self._finish_failure(LocationFailure.CANCELLED, "cancelled by operator")

    def _timed_out(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        self._finish_failure(LocationFailure.TIMEOUT, "GPS read timeout")

    def _thread_fix_ready(self, token: int, fix: LocationFix) -> None:
        if token != self._generation or not self._active:
            return
        self._active = False
        self._timeout.stop()
        self._stop_event = None
        self.fix_ready.emit(fix)

    def _thread_failed(
        self, token: int, failure: LocationFailure, detail: str
    ) -> None:
        if token != self._generation or not self._active:
            return
        self._finish_failure(failure, detail)

    def _finish_failure(self, failure: LocationFailure, detail: str) -> None:
        if not self._active:
            return
        self._active = False
        self._timeout.stop()
        self._stop_event = None
        self.failed.emit(failure, detail)

    def _thread_finished(self, token: int) -> None:
        finished = next(
            (thread for thread in self._threads if thread.token == token),
            None,
        )
        if finished is not None:
            self._threads.discard(finished)
            if self._thread is finished and token == self._generation:
                self._thread = None
