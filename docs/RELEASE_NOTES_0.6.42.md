# Guardian 0.6.42

**Audio works on Windows on ARM.** The 0.6.41 diagnostics answered the
missing-audio-devices report in one line, and the cause was neither privacy
settings nor drivers:

```
"error": "OSError: cannot load library '...\\_sounddevice_data\\
          portaudio-binaries\\libportaudioarm64.dll': error 0x7e"
```

## What was happening

That PC is an ARM64 machine running Guardian's **x64** build under Windows'
emulation. Windows tells such a process two different things:

| variable | value | meaning |
| --- | --- | --- |
| `PROCESSOR_ARCHITECTURE` | `AMD64` | what **this process** runs as |
| `PROCESSOR_ARCHITEW6432` | `ARM64` | what the **machine** is |

Python's `platform.machine()` prefers the second, so it answers `ARM64`. The
audio backend picks its bundled PortAudio DLL from exactly that value, went
looking for `libportaudioarm64.dll`, and the import failed with error 0x7e
(module not found) — taking every audio device on the PC with it, while VARA,
which never touches this backend, listed them normally.

**Shipping the ARM64 DLL would not have fixed it.** An emulated x64 process
cannot load an ARM64 library; the x64 one it already has is the correct file.
The question being asked was simply the wrong one — the *machine's*
architecture rather than the *process's*.

Guardian now answers the right question while the backend chooses its
library, and puts `platform.machine()` back immediately afterwards. Native
x64 PCs and non-Windows hosts are untouched: the override only engages when
the process and machine architectures actually differ.

## Diagnostics

The `audio_backend` section now reports both architectures side by side, so
an emulated process is identifiable at a glance:

```json
"process_architecture": "AMD64",
"machine_architecture": "ARM64"
```

**For OK2IPW's station:** update and open Settings → Audio. The devices
should be there. If anything is still missing, the diagnostics export will
now show the real device list rather than a load error.

## Tests

The DLL choice following the process rather than the machine (the emulated
case), an ordinary machine left completely alone, `platform.machine()`
restored after the import, and both architectures present in diagnostics.
All three deliberate mutations — no override, overriding with the machine
value, and leaking the patch — were caught.
