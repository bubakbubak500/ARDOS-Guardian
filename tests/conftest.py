"""Keep import-time Guardian state out of the developer's real APPDATA.

The current application resolves and creates its data directory while modules
are imported. Stage 0 characterises that behavior instead of changing it, so
the test process receives an isolated APPDATA before test modules are loaded.
"""

import os
from pathlib import Path
import shutil
import tempfile

import pytest


_TEST_APPDATA = Path(tempfile.mkdtemp(prefix="guardian-tests-"))
os.environ["APPDATA"] = str(_TEST_APPDATA)


@pytest.fixture(scope="session", autouse=True)
def _isolate_shell_background_io():
    """Keep UI tests independent from real filesystem and network scans."""
    from guardian.qt.runtime import ShellRuntime

    dependency_refresh = ShellRuntime.request_dependency_refresh
    update_check = ShellRuntime.request_update_check
    ShellRuntime.request_dependency_refresh = lambda self: False
    ShellRuntime.request_update_check = lambda self, on_complete=None: False
    try:
        yield
    finally:
        ShellRuntime.request_dependency_refresh = dependency_refresh
        ShellRuntime.request_update_check = update_check


def pytest_sessionfinish(session, exitstatus) -> None:
    shutil.rmtree(_TEST_APPDATA, ignore_errors=True)
