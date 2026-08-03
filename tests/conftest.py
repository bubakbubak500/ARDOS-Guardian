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
def _isolate_shell_dependency_scans():
    """Keep UI tests independent from real, concurrent filesystem scans."""
    from guardian.qt.runtime import ShellRuntime

    original = ShellRuntime.request_dependency_refresh
    ShellRuntime.request_dependency_refresh = lambda self: False
    try:
        yield
    finally:
        ShellRuntime.request_dependency_refresh = original


def pytest_sessionfinish(session, exitstatus) -> None:
    shutil.rmtree(_TEST_APPDATA, ignore_errors=True)
