# -*- mode: python ; coding: utf-8 -*-
"""Separate Linux freeze definition; the Windows spec is unchanged."""
from pathlib import Path
import shutil
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

if not sys.platform.startswith("linux"):
    raise RuntimeError("Build Guardian-linux.spec on Linux")
root = Path(SPECPATH).resolve()
datas = [(str(root / "guardian/lab/dashboard.html"), "guardian/lab"),
         (str(root / "build/guardian-build.json"), "."),
         (str(root / "native/ardop/vendor/LICENSE"), "ardop"),
         (str(root / "native/ardop/UPSTREAM.md"), "ardop")]
ardop = root / "native/ardop/bin/libguardian_ardop.so"
if not ardop.is_file():
    raise FileNotFoundError("Run tools/build_ardop.py on Linux first")
binaries = [(str(ardop), "ardop")]
hiddenimports = collect_submodules("guardian.lab")
for package in ("sounddevice", "zopfli", "bleak", "keyring", "secretstorage", "jeepney"):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports
for tool in ("cjxl", "djxl"):
    path = shutil.which(tool)
    if path is None:
        raise FileNotFoundError("Install libjxl-tools before freezing")
    binaries.append((path, "jpegxl"))
portaudio = Path("/usr/lib/x86_64-linux-gnu/libportaudio.so.2")
if not portaudio.is_file():
    raise FileNotFoundError("Install libportaudio2 before freezing")
binaries.append((str(portaudio), "."))
for package in ("libjxl-tools", "libjxl0.7", "libportaudio2"):
    license_path = Path("/usr/share/doc") / package / "copyright"
    if license_path.is_file():
        datas.append((str(license_path), "licenses/" + package))

analysis = Analysis([str(root / "guardian_launch.py")], pathex=[str(root)],
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["pycaw", "comtypes", "winrt"], noarchive=False, optimize=0)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name="Guardian",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
collection = COLLECT(exe, analysis.binaries, analysis.datas,
    strip=False, upx=False, name="Guardian")
