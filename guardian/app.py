"""Application entry point."""

from __future__ import annotations

# App User Model ID — set BEFORE any window is created so Windows treats
# Guardian as its own app in the taskbar (and uses our icon, not pythonw's).
APP_ID = "Guardian.ARDOS.Control.1"


def _set_app_user_model_id() -> None:
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def main() -> None:
    _set_app_user_model_id()
    from .qt.app import main as qt_main

    qt_main()


if __name__ == "__main__":
    main()
