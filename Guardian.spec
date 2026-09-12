# -*- mode: python ; coding: utf-8 -*-
"""Portable PyInstaller definition for the Guardian Windows application.

The optional compression and Core Audio paths are packaged explicitly.  This
keeps the G1 application identity and data directory while avoiding the G2
Companion/hotspot dependency set.
"""

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules


root = Path(SPECPATH).resolve()
datas = []
binaries = []
hiddenimports = []

for package in ("sounddevice", "pycaw", "comtypes", "zopfli"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

# G1's consent-first map still loads only the two existing geolocation
# projections dynamically.  Do not collect the broad WinRT namespace: that
# would pull in unrelated G2 Companion/WiFi APIs and their dependencies.
for package in (
    "winrt.windows.devices.geolocation",
    "winrt.windows.foundation",
):
    try:
        hiddenimports += collect_submodules(package)
    except ImportError:
        # Source builds on a non-Windows host do not install PyWinRT.  The
        # runtime already reports the location API as unavailable there.
        hiddenimports.append(package)

jpegxl_root = root / "codecs" / "vendor" / "jpegxl"
for tool in ("cjxl.exe", "djxl.exe"):
    path = jpegxl_root / "bin" / tool
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing {path}; run tools\\fetch_jpegxl.ps1 before PyInstaller."
        )
    binaries.append((str(path), "jpegxl"))
licenses = jpegxl_root / "licenses"
if licenses.is_dir():
    datas.append((str(licenses), "jpegxl/licenses"))

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

# Qt6Core resolves ICU through the Windows installation when the PySide6
# wheel is used.  PyInstaller's dependency scan can instead pick up the
# Codex-runtime ICU 78 pair; its ``icuuc.dll`` does not export the unversioned
# symbols requested by Qt6Core on this supported Windows image.  Keep the
# source and frozen behavior aligned by leaving the system ICU resolution in
# place and dropping only these incompatible auto-collected files.
if sys.platform == "win32":
    analysis.binaries[:] = [
        _entry
        for _entry in analysis.binaries
        if Path(_entry[0]).name.lower() != "icuuc.dll"
        and not Path(_entry[0]).name.lower().startswith("icudt")
    ]

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Guardian",
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
    name="Guardian",
)
