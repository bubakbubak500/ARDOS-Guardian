"""Keep import-time Guardian state out of the developer's real APPDATA.

The current application resolves and creates its data directory while modules
are imported. Stage 0 characterises that behavior instead of changing it, so
the test process receives an isolated APPDATA before test modules are loaded.
"""

import os
from pathlib import Path
import shutil
import tempfile


_TEST_APPDATA = Path(tempfile.mkdtemp(prefix="guardian-tests-"))
os.environ["APPDATA"] = str(_TEST_APPDATA)


def pytest_sessionfinish(session, exitstatus) -> None:
    shutil.rmtree(_TEST_APPDATA, ignore_errors=True)
