"""Keep the shipped ARDOP DLL compatible with baseline Windows x64 CPUs."""
import sys

from tools import build_ardop


def test_windows_zig_build_targets_baseline_x64(monkeypatch):
    captured = {}

    def record(command, **kwargs):
        captured['command'] = command
        captured['env'] = kwargs['env']

    monkeypatch.setattr(sys, 'argv', ['build_ardop.py', '--cc', 'zig cc'])
    monkeypatch.setattr(build_ardop.sys, 'platform', 'win32')
    monkeypatch.setattr(build_ardop.subprocess, 'run', record)
    monkeypatch.setenv('ZIG_GLOBAL_CACHE_DIR', 'custom-global-cache')
    monkeypatch.setenv('ZIG_LOCAL_CACHE_DIR', 'custom-local-cache')

    build_ardop.main()

    assert captured['command'].count('-target') == 1
    assert captured['command'].count('x86_64-windows-gnu') == 1
    assert '-mcpu=x86_64' in captured['command']
    assert captured['env']['ZIG_GLOBAL_CACHE_DIR'] == 'custom-global-cache'
    assert captured['env']['ZIG_LOCAL_CACHE_DIR'] == 'custom-local-cache'
