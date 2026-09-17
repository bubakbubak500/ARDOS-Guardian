"""Build the pinned in-process modem with a C11 compiler (GCC/Clang/Zig)."""
from pathlib import Path
import argparse
import os
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    bundled_zig = ROOT / '.build-temp/toolchain/zig-x86_64-windows-0.14.1/zig.exe'
    default_cc = '"' + bundled_zig.as_posix() + '" cc' if bundled_zig.is_file() else 'cc'
    parser.add_argument('--cc', default=os.environ.get('CC', default_cc))
    parser.add_argument('--target', default='')
    args = parser.parse_args()
    source = ROOT / 'native/ardop'
    vendor = source / 'vendor'
    windows = 'windows' in args.target if args.target else sys.platform == 'win32'
    output = source / 'bin' / ('guardian_ardop.dll' if windows else 'libguardian_ardop.so')
    output.parent.mkdir(parents=True, exist_ok=True)
    files = sorted((vendor / 'core').rglob('*.c'))
    files += [vendor / 'shell' / name for name in ('runtime.c', 'resample.c', 'telemetry.c', 'capture.c', 'sys.c')]
    command = shlex.split(args.cc)
    command += ['-std=c11', '-O2', '-shared', '-I' + str(vendor), '-I' + str(vendor / 'core')]
    if args.target:
        command += ['-target', args.target]
    if not windows:
        command += ['-fPIC', '-D_POSIX_C_SOURCE=200809L', '-pthread']
    command += [str(source / 'guardian_ardop.c'), *map(str, files), '-o', str(output), '-lm']
    cache = ROOT / '.build-temp' / 'zig-cache'
    env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(cache / 'global'), ZIG_LOCAL_CACHE_DIR=str(cache / 'local'))
    subprocess.run(command, check=True, cwd=ROOT, env=env)
    print(output)

if __name__ == '__main__':
    main()
