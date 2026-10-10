#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
python tools/build_ardop.py
python tools/write_build_identity.py
python -m PyInstaller --noconfirm --clean Guardian-linux.spec
mkdir -p dist/Guardian/maps
cp installer/MAPS_README.txt dist/Guardian/maps/README.txt
cp docs/LINUX.md dist/Guardian/README-Linux.md
python -c 'from guardian.assets.icon import build_image; build_image(256).save("dist/Guardian/guardian.png")'
python tools/package_linux.py
