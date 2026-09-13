"""Small, shared helpers for windows that must fit on every desktop.

Qt reports top-level geometry in logical pixels.  That means the same code can
be used at 100%, 125%, and 150% Windows display scaling: the platform plugin
has already converted the monitor's available work area before it reaches us.

The helpers deliberately do not change a layout or force a widget to a
particular minimum.  Callers provide a preferred and a *readable* minimum,
while long content remains scrollable in the dialog that owns it.  Keeping
that policy here makes all top-level windows use the same screen selection,
clamping, and centering rules.
"""

from __future__ import annotations

from collections.abc import Iterable
import weakref

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, QTimer, Slot
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid


DEFAULT_SCREEN_MARGIN = 24


class _ScreenFit(QObject):
    """Refit visible windows without retaining native window/screen wrappers."""

    def __init__(self, window, minimum, margin):
        super().__init__(window)
        # parent() can recreate a Python wrapper during native destruction.
        # Event filters also run while a window is being torn down; never
        # resurrect that parent from a WinIdChange notification.
        self._window = weakref.ref(window)
        self.minimum = QSize(minimum)
        self.margin = margin
        self._connections = []
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.apply)
        window.installEventFilter(self)

    @Slot()
    def schedule(self, *_args):
        window = self._window()
        if window is not None and isValid(window) and window.isVisible():
            self.timer.start(0)

    def disconnect_screen(self):
        for connection in self._connections:
            QObject.disconnect(connection)
        self._connections.clear()

    def bind_screen(self):
        self.disconnect_screen()
        window = self._window()
        if window is None or not isValid(window):
            return
        handle = window.windowHandle()
        if handle is not None:
            self._connections.append(handle.screenChanged.connect(self.schedule))
        screen = window.screen()
        if screen is not None:
            self._connections.append(screen.availableGeometryChanged.connect(self.schedule))

    @Slot()
    def apply(self):
        window = self._window()
        if window is not None and isValid(window) and window.isVisible():
            self.bind_screen()
            fit_window_to_screen(
                window, window.size(), self.minimum,
                margin=self.margin, center=False, _track=False,
            )

    def eventFilter(self, watched, event):
        if event.type() in {
            QEvent.Type.Show, QEvent.Type.WinIdChange,
            QEvent.Type.ScreenChangeInternal, QEvent.Type.WindowStateChange,
        }:
            self.schedule()
        elif event.type() == QEvent.Type.Hide:
            self.timer.stop()
            self.disconnect_screen()
        return False


def available_screen_geometry(widget: QWidget | None = None) -> QRect:
    """Return the available work area for ``widget`` in logical pixels.

    A widget's window handle is preferred because it remains reliable while a
    top-level window is being moved between monitors. Hidden dialogs use their
    parent's monitor, falling back to the primary screen. Tests can patch this
    function with a fixed ``QRect`` to exercise display-scale matrices without
    needing a real monitor.
    """

    screen = None
    # A new dialog belongs on its parent's monitor. Avoid creating a native
    # handle just to determine the screen while the dialog is being built.
    anchor = widget
    if widget is not None and not widget.isVisible():
        anchor = widget.parentWidget()
    if anchor is not None:
        handle = anchor.window().windowHandle()
        if handle is not None:
            screen = handle.screen()

    application = QApplication.instance()
    if screen is None and application is not None and widget is not None and widget.isVisible():
        screen = application.screenAt(widget.frameGeometry().center())
    if screen is None and application is not None:
        screen = application.primaryScreen()
    if screen is not None:
        # QScreen is owned by Qt, not by a dialog. Keep its Python wrapper
        # alive across transient windows instead of repeatedly releasing and
        # reconstructing it while other widget wrappers are being collected.
        if application is not None:
            retained = [item for item in getattr(application, "_guardian_screen_refs", ())
                        if isValid(item)]
            if screen not in retained:
                retained.append(screen)
            application._guardian_screen_refs = retained
        return QRect(screen.availableGeometry())

    # A QApplication normally exists before any top-level window can be
    # created.  The fallback keeps the helper harmless in import-time and
    # lightweight unit-test use, though.
    return QRect(0, 0, 1024, 768)


def _size(value: QSize | Iterable[int] | None, fallback: QSize) -> QSize:
    if value is None:
        return QSize(fallback)
    if isinstance(value, QSize):
        return QSize(value)
    try:
        width, height = value
    except (TypeError, ValueError):
        return QSize(fallback)
    return QSize(max(1, int(width)), max(1, int(height)))


def _usable_geometry(geometry: QRect, margin: int) -> QRect:
    """Inset a work area while keeping a valid one-pixel rectangle."""

    area = QRect(geometry)
    inset = max(0, int(margin))
    # A very small test screen should still produce a usable target rather
    # than a negative QRect.  The normal desktop path never reaches this.
    inset_x = min(inset, max(0, (area.width() - 1) // 2))
    inset_y = min(inset, max(0, (area.height() - 1) // 2))
    return area.adjusted(inset_x, inset_y, -inset_x, -inset_y)


def _clamp_top_left(window: QWidget, area: QRect) -> QPoint:
    frame = window.frameGeometry()
    max_left = max(area.left(), area.right() - frame.width() + 1)
    max_top = max(area.top(), area.bottom() - frame.height() + 1)
    x = min(max(frame.left(), area.left()), max_left)
    y = min(max(frame.top(), area.top()), max_top)
    return QPoint(x, y)


def _client_area_size(window: QWidget, area: QRect) -> QSize:
    """Return the client size that leaves room for the native frame."""

    frame = window.frameGeometry()
    client = window.size()
    frame_width = max(0, frame.width() - client.width())
    frame_height = max(0, frame.height() - client.height())
    return QSize(
        max(1, area.width() - frame_width),
        max(1, area.height() - frame_height),
    )


def center_on_available_screen(
    window: QWidget,
    *,
    margin: int = DEFAULT_SCREEN_MARGIN,
) -> None:
    """Center ``window`` in its current monitor's available work area."""

    area = _usable_geometry(available_screen_geometry(window), margin)
    frame = window.frameGeometry()
    frame.moveCenter(area.center())
    window.move(frame.topLeft())
    # A decoration can be wider than a tiny synthetic test area.  Clamp after
    # centering so the same operation is safe for both real and fake screens.
    window.move(_clamp_top_left(window, area))


def fit_window_to_screen(
    window: QWidget,
    preferred_size: QSize | Iterable[int] | None = None,
    minimum_size: QSize | Iterable[int] | None = None,
    *,
    margin: int = DEFAULT_SCREEN_MARGIN,
    center: bool = True,
    limit_maximum: bool = False,
    _track: bool = True,
) -> QSize:
    """Fit and optionally center a top-level window in the available area.

    ``preferred_size`` is the normal comfortable size.  ``minimum_size`` is
    the smallest size at which the caller considers its controls readable;
    when that size is larger than the monitor, it is capped to the monitor so
    the window never makes its action buttons unreachable.  Large content
    should be placed in a ``QScrollArea`` by the caller.

    The returned :class:`QSize` is the actual requested client size, useful in
    tests and for callers that want to record geometry.  ``limit_maximum`` is
    opt-in because a maximum captured on one monitor would otherwise survive
    a move to a larger monitor.  Callers that opt in should call this helper
    again from ``showEvent`` or their screen-change handler.
    """

    if not isinstance(window, QWidget):
        raise TypeError("window must be a QWidget")

    minimum = _size(minimum_size, window.minimumSizeHint())
    if _track:
        watcher = getattr(window, "_screen_fit", None)
        if watcher is None:
            window._screen_fit = _ScreenFit(window, minimum, margin)
        else:
            watcher.minimum = QSize(minimum)
            watcher.margin = margin
    if window.isMaximized() or window.isFullScreen():
        return QSize(window.size())

    area = _usable_geometry(available_screen_geometry(window), margin)
    available = _client_area_size(window, area)
    preferred = _size(preferred_size, window.sizeHint())

    minimum.setWidth(min(minimum.width(), available.width()))
    minimum.setHeight(min(minimum.height(), available.height()))
    target = QSize(
        min(max(preferred.width(), minimum.width()), available.width()),
        min(max(preferred.height(), minimum.height()), available.height()),
    )

    # Clear a bound left by an earlier call before fitting on another monitor.
    # This is also useful when a caller switches from a capped to a free
    # window after it has been shown.
    window.setMaximumSize(QSize(16_777_215, 16_777_215))
    if limit_maximum:
        window.setMaximumSize(available)
    window.setMinimumSize(minimum)
    window.resize(target)

    frame = window.frameGeometry()
    if center:
        frame.moveCenter(area.center())
        window.move(frame.topLeft())
    window.move(_clamp_top_left(window, area))
    return QSize(target)


def fit_dialog_to_screen(
    dialog: QWidget,
    preferred_size: QSize | Iterable[int] | None = None,
    minimum_size: QSize | Iterable[int] | None = None,
    *,
    margin: int = DEFAULT_SCREEN_MARGIN,
    center: bool = True,
    limit_maximum: bool = False,
) -> QSize:
    """Dialog-named alias for :func:`fit_window_to_screen`.

    Keeping this name makes call sites self-documenting while allowing the
    same implementation to be used by a persistent ``QMainWindow`` such as
    the spectrum monitor.
    """

    return fit_window_to_screen(
        dialog,
        preferred_size,
        minimum_size,
        margin=margin,
        center=center,
        limit_maximum=limit_maximum,
    )
