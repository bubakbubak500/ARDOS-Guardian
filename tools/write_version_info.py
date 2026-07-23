"""Generate PyInstaller Windows version metadata from Guardian's one version."""

from __future__ import annotations

import argparse
from pathlib import Path
import re

from guardian import __version__


TEMPLATE = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version_tuple},
    prodvers={version_tuple},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'OK7PS'),
          StringStruct('FileDescription', 'Guardian - ARDOS control and routing layer'),
          StringStruct('FileVersion', '{version}'),
          StringStruct('InternalName', 'Guardian'),
          StringStruct('LegalCopyright', 'Copyright (c) OK7PS'),
          StringStruct('OriginalFilename', 'Guardian.exe'),
          StringStruct('ProductName', 'Guardian'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def version_tuple(value: str) -> tuple[int, int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        raise ValueError("Guardian version must use major.minor.patch format.")
    return (*map(int, match.groups()), 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        TEMPLATE.format(
            version=__version__,
            version_tuple=version_tuple(__version__),
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
