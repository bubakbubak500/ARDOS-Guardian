"""PyInstaller entry point for the G1 Guardian application.

The launcher stays free of GUI/native imports until after
``multiprocessing.freeze_support``.  This is required by the bounded
aggressive-compression worker in a frozen Windows build.
"""

from __future__ import annotations

import sys
import traceback
from multiprocessing import freeze_support
from pathlib import Path


def _native_module_diagnostics() -> list[str]:
    """Return loaded native module paths for frozen self-test failures."""

    lines = [f"MEIPASS={getattr(sys, '_MEIPASS', '<source>')}"]
    if sys.platform != "win32":
        return lines
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_module_handle = kernel32.GetModuleHandleW
        get_module_handle.argtypes = [ctypes.c_wchar_p]
        get_module_handle.restype = ctypes.c_void_p
        get_module_filename = kernel32.GetModuleFileNameW
        get_module_filename.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
        get_module_filename.restype = ctypes.c_uint32
        for name in (
            "MSVCP140.dll",
            "MSVCP140_1.dll",
            "MSVCP140_2.dll",
            "VCRUNTIME140.dll",
            "VCRUNTIME140_1.dll",
            "Qt6Core.dll",
            "shiboken6.abi3.dll",
        ):
            handle = get_module_handle(name)
            if not handle:
                lines.append(f"LOADED {name}=<no>")
                continue
            buffer = ctypes.create_unicode_buffer(1024)
            length = get_module_filename(handle, buffer, len(buffer))
            lines.append(f"LOADED {name}={buffer.value[:length]}")
    except BaseException as exc:
        lines.append(f"native diagnostics unavailable: {exc!r}")
    return lines


def _self_test_report_path(arguments: list[str], option: str) -> Path | None:
    try:
        index = arguments.index(option)
    except ValueError:
        return None
    if index + 1 >= len(arguments):
        raise ValueError(f"{option} requires a path")
    return Path(arguments[index + 1])


def _write_report(path: Path | None, report: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")


def _run_compression_self_test() -> None:
    from guardian.message.compression_selftest import run

    report_path = _self_test_report_path(
        sys.argv, "--compression-self-test-report"
    )
    try:
        report = run()
    except BaseException:
        _write_report(report_path, traceback.format_exc())
        raise
    _write_report(report_path, report)


def _run_qt_self_test() -> None:
    report_path = _self_test_report_path(sys.argv, "--qt-self-test-report")
    try:
        import PySide6
        from PySide6 import QtCore, QtGui, QtWidgets
        import importlib

        # Keep this an import-only smoke test.  It verifies that the two
        # consent-gated WinRT projections are present in the frozen bundle
        # without touching location services or requesting user consent.
        geolocation = importlib.import_module(
            "winrt.windows.devices.geolocation"
        )
        foundation = importlib.import_module("winrt.windows.foundation")

        # Touch one native symbol from every Qt library the main shell imports.
        report = (
            "PASS\n"
            f"PySide6 {PySide6.__version__}\n"
            f"Qt {QtCore.qVersion()}\n"
            f"QtGui QColor valid={QtGui.QColor('black').isValid()}\n"
            f"QtWidgets module={QtWidgets.__name__}\n"
            f"WinRT geolocation={geolocation.__name__}\n"
            f"WinRT foundation={foundation.__name__}\n"
        )
    except BaseException:
        report = "\n".join(_native_module_diagnostics()) + "\n" + traceback.format_exc()
        _write_report(report_path, report)
        raise
    _write_report(report_path, report)


if __name__ == "__main__":
    # Must happen before importing guardian.app/Qt.  Spawned frozen workers
    # re-enter this module and PyInstaller uses this hook to consume its
    # multiprocessing command-line arguments safely.
    freeze_support()
    if "--compression-self-test" in sys.argv:
        _run_compression_self_test()
    elif "--qt-self-test" in sys.argv:
        _run_qt_self_test()
    else:
        from guardian.app import main

        main()
