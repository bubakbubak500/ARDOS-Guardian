# -*- mode: python ; coding: utf-8 -*-
"""Portable PyInstaller definition for the Guardian Windows application.

The G2 line builds as Guardian-G2.exe into dist\\Guardian-G2 so it can be
installed and run next to a public G1 Guardian on the same machine.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs, collect_submodules


root = Path(SPECPATH).resolve()
datas = []
binaries = []
hiddenimports = []

for package in ("sounddevice",):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

# Imported lazily so non-Windows source installs retain a clean fallback.  A
# frozen Windows build still needs the projection module and its native DLLs.
hiddenimports += collect_submodules("winrt")
binaries += collect_dynamic_libs("winrt")

# Exact upstream archives include the helper binaries, source and licence
# notices for Guardian's optional ZPAQ/PAQ8PX/LPAQ8 compression candidates.
datas.append((str(root / "guardian" / "codecs" / "vendor"),
              "guardian/codecs/vendor"))

analysis = Analysis(
    [str(root / "guardian_launch.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Guardian-G2",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(root / "guardian" / "assets" / "guardian.ico"),
    version=str(root / "build" / "version_info.txt"),
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Guardian-G2",
)
