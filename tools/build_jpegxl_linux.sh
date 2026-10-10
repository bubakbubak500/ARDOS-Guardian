#!/usr/bin/env bash
# Build the same pinned libjxl release as Windows, against Ubuntu 22.04's ABI.
# Required apt packages: build-essential cmake ninja-build pkg-config
#                       libpng-dev libjpeg-dev
# Upstream build/options: https://github.com/libjxl/libjxl/blob/v0.12.0/BUILDING.md
#                        https://github.com/libjxl/libjxl/blob/v0.12.0/CMakeLists.txt
set -euo pipefail

if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
    printf '%s\n' 'Build these release tools on Linux x86_64 (Ubuntu 22.04).' >&2
    exit 1
fi
for command in git cmake ninja pkg-config c++ cc; do
    command -v "$command" >/dev/null || {
        printf 'Missing build dependency: %s\n' "$command" >&2
        exit 1
    }
done
pkg-config --exists libpng libjpeg || {
    printf '%s\n' 'Install libpng-dev and libjpeg-dev before building JPEG XL.' >&2
    exit 1
}

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
version=0.12.0
# Exact commit linked by the official v0.12.0 release; do not trust a moved tag.
commit=a7a9c787341cf703dede03c2009fa460cae5e5df
work="$root/.build-temp/jpegxl-linux-$version"
source="$work/source"
build="$work/build"
destination="$root/codecs/vendor/jpegxl"
bin="$destination/bin"
licenses="$destination/licenses"
mkdir -p -- "$work" "$bin" "$licenses"

if [[ ! -d "$source/.git" ]]; then
    git init "$source"
    git -C "$source" remote add origin https://github.com/libjxl/libjxl.git
    git -C "$source" fetch --depth 1 origin "$commit"
    git -C "$source" checkout --detach FETCH_HEAD
fi
if [[ "$(git -C "$source" rev-parse HEAD)" != "$commit" ]]; then
    printf '%s\n' "Cached libjxl checkout is not the pinned commit: $commit" >&2
    exit 1
fi
# Explicit paths omit the large testdata repository and unused codec/test deps.
git -C "$source" submodule update --init --recursive --depth 1 \
    --recommend-shallow -- third_party/brotli third_party/highway third_party/skcms

cmake -S "$source" -B "$build" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_FLAGS='-march=x86-64 -mtune=generic' \
    -DCMAKE_CXX_FLAGS='-march=x86-64 -mtune=generic' \
    -DCMAKE_LIBRARY_OUTPUT_DIRECTORY="$build/runtime" \
    -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON \
    '-DCMAKE_INSTALL_RPATH=$ORIGIN' \
    -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=OFF \
    -DCMAKE_DISABLE_FIND_PACKAGE_GIF=ON \
    -DBUILD_SHARED_LIBS=ON \
    -DBUILD_TESTING=OFF \
    -DJPEGXL_VERSION="$version" \
    -DJPEGXL_DEP_LICENSE_DIR=/usr/share/doc \
    -DJPEGXL_ENABLE_TOOLS=ON \
    -DJPEGXL_ENABLE_DEVTOOLS=OFF \
    -DJPEGXL_ENABLE_BENCHMARK=OFF \
    -DJPEGXL_ENABLE_EXAMPLES=OFF \
    -DJPEGXL_ENABLE_JNI=OFF \
    -DJPEGXL_ENABLE_DOXYGEN=OFF \
    -DJPEGXL_ENABLE_MANPAGES=OFF \
    -DJPEGXL_ENABLE_FUZZERS=OFF \
    -DJPEGXL_ENABLE_VIEWERS=OFF \
    -DJPEGXL_ENABLE_PLUGINS=OFF \
    -DJPEGXL_ENABLE_TCMALLOC=OFF \
    -DJPEGXL_ENABLE_OPENEXR=OFF \
    -DJPEGXL_ENABLE_SJPEG=OFF \
    -DJPEGXL_ENABLE_SKCMS=ON \
    -DJPEGXL_ENABLE_LTO=OFF \
    -DJPEGXL_TEST_TOOLS=OFF \
    -DJPEGXL_BUNDLE_LIBPNG=OFF \
    -DJPEGXL_ENABLE_HWY_AVX3=OFF \
    -DJPEGXL_ENABLE_HWY_AVX3_DL=OFF \
    -DJPEGXL_ENABLE_HWY_AVX3_SPR=OFF \
    -DJPEGXL_ENABLE_HWY_AVX3_ZEN4=OFF
# jpegli was removed upstream in v0.12.0; no jpegli flag/checkout is needed.
# Building named targets avoids compiling/installing all upstream CLI tools.
cmake --build "$build" --target cjxl djxl --parallel 2
install -m 755 -- "$build/tools/cjxl" "$build/tools/djxl" "$bin/"

shopt -s nullglob
libraries=("$build/runtime/"*.so*)
if (( ${#libraries[@]} == 0 )); then
    printf '%s\n' 'No JPEG XL shared libraries were built.' >&2
    exit 1
fi
# Preserve the relative SONAME symlinks; $ORIGIN resolves beside the tools.
cp -a -- "${libraries[@]}" "$bin/"
license_files=("$build/"LICENSE.*)
cp -- "${license_files[@]}" "$licenses/"
cp -- "$source/PATENTS" "$licenses/PATENTS.jpeg-xl"
printf 'libjxl v%s\nhttps://github.com/libjxl/libjxl/commit/%s\n' \
    "$version" "$commit" > "$licenses/UPSTREAM.txt"

# Check the staged RPATH and PNG codec before PyInstaller inspects the tools.
"$bin/cjxl" --version
"$bin/djxl" --version
printf 'P6\n2 1\n255\n\377\000\000\000\377\000' > "$work/smoke.ppm"
"$bin/cjxl" "$work/smoke.ppm" "$work/smoke.jxl" --distance=0 --effort=3
"$bin/djxl" "$work/smoke.jxl" "$work/smoke.png"
test -s "$work/smoke.png"
printf 'JPEG XL %s tools and libraries staged in %s\n' "$version" "$bin"
printf 'Add to PATH before building: export PATH="%s:$PATH"\n' "$bin"
