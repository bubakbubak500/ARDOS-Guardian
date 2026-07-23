import re

from guardian import __version__


def test_application_version_uses_release_format() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
