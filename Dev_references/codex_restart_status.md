# Codex restart status — G2 adaptive OFDM 2.1.1 release

Updated: 2026-08-10 (Europe/Prague)

## Outcome

The requested G2 OFDM speed/resilience work is complete and ready for the first
two-radio IC-705 test. Guardian G2 was advanced to version 2.1.1, all 900 tests
pass, the frozen application launches successfully, and a verified Windows
installer is available.

- Workspace: `C:\Users\ok7ps\Documents\Guardian`
- Branch: `g2`
- Unchanged HEAD: `73ff4d4` (changes are intentionally uncommitted)
- Permissions: unrestricted filesystem/network access; approval policy `never`
- No commit, push, release upload, or silent installation was performed
- User-owned `.vscode/`, `Dev_references/`, and `output/` content was preserved

## Definitive artifact

- Installer: `release\Guardian-G2-2.1.1-setup-win-x64.exe`
- Size: 41,585,966 bytes
- SHA-256: `0d3d9cff65d583a1a7a332626cc1f14468da5ce593b490831afd5b08f5a9934f`
- Authenticode: `NotSigned` (expected; Windows may show a warning)
- Manifest version/hash/download URL: verified and matching
- Frozen app: `dist\Guardian-G2\Guardian-G2.exe`
- Frozen app SHA-256: `f16648d1554d1034820c5d87b34b5d10eb75184bcbbe96c7d9e535f9fc4650f5`
- Isolated launch smoke: alive and responding after five seconds

The installer was not run because a registered G2 2.0.4 installation already
exists. This preserves the user's current install until they explicitly test
2.1.1. The prior 2.1.0 installer also remains in `release\`.

## 2.1.1 workspace cleanup

The **Whole transfer** and **Decode rate vs SNR** pages were removed from the
operator-facing Modem test tab bar. Both only simulated the two endpoints and
channel in memory; neither transmitted through nor measured the configured
radio. Their engine and automated tests remain available through
`tools/ofdm_bench.py`. The practical **One burst** and **Files** pages remain.

## Implemented design

The existing OFDM waveform, roughly 2.7 kHz occupied channel, K=7 octal
171/133 convolutional code, robust header, audio/PTT pipes, and MCS selection
were retained. No 64-QAM promotion or bandwidth widening was added.

- Five explicit payload FEC profiles: 1/2, 2/3, 3/4, 5/6, and 7/8, implemented
  by puncturing the existing rate-1/2 mother code. Exact encoded-bit and
  effective-rate calculations use the actual puncturing and termination.
- Frame format 2 keeps the robust 16-byte BPSK/rate-1/2 header, explicitly
  carries FEC/MCS/version metadata, adds a robust sequence/length manifest, and
  protects each ARQ subblock independently with FEC and CRC.
- Keyed burst targets: 256, 512, 1024, 2048, 4096, 8192, and 16384 bytes.
  Independently selectable ARQ blocks: 256, 512, or 1024 bytes.
- One variable-length ACK/NACK bitmap acknowledges an aggregate burst. The
  receiver retains good blocks and the sender selectively retries only missing
  blocks, using progressively stronger FEC. Duplicate/late traffic cannot
  duplicate application bytes; the lost-final-ACK linger remains.
- One hysteretic controller jointly manages FEC and burst length. AUTO starts
  at 1/2 and 2048 bytes, upgrades one axis after three clean bursts, and moves
  toward robustness immediately after failure. Both axes also support FIXED.
- Timeouts derive from actual waveform duration, turnaround, processing margin,
  and a configurable multiplier. Retries are bounded.
- Local and remote SNR/EVM are kept distinct. Status and logs expose unique
  payload, retransmissions, protocol overhead, data/ACK airtime, turnaround,
  first-pass bitmap results, modeled channel rate, and real wall-clock goodput.
- Version-1 decoding and fixed 512-byte stop-and-wait legacy mode remain for
  controlled comparison. Both stations should use the same 2.1.0 mode because
  format-subversion negotiation was not added.

## Configuration and UI

New persisted settings cover adaptive/fixed FEC, fixed/min/max adaptive burst,
ARQ block size, retry limit, timeout multiplier, and legacy mode. The Settings
dialog and Modem test workspace expose the choices. The live transfer panel
shows active FEC, burst/ARQ sizes, first-pass success, retry bytes, and measured
wall-clock goodput.

## Validation

- Release-final 2.1.1 full suite: `900 passed` in 227.3 seconds
- Focused final logging/link/UI regression: 36 passed
- Python package/tools compilation: passed
- `git diff --check`: passed (only existing CRLF conversion warnings)
- All five FEC clean round trips and explicit header serialization: covered
- Deterministic selective repeat: exactly one of four blocks erased; three were
  retained, only 512 bytes were retried with stronger FEC, final bytes matched
- Lost final ACK, duplicate suppression, 256/512/1024 ARQ blocks, partial final
  blocks, corrupt manifest/data rejection, fixed/AUTO/legacy operation,
  hysteresis, and duration-aware timeout behavior: covered
- PyInstaller application and Inno Setup installer builds: passed
- Final manifest/hash/version/frozen-launch checks: passed

## Deterministic benchmark

BENCH/MCS1, 2471 bytes, 20 dB simulated in-band SNR, 0.4 s turnaround:

| Setting | Channel time | Goodput |
|---|---:|---:|
| Legacy v1, 512 B, FEC 1/2 | 17.536 s | 1127 bit/s |
| Format 2, 4096 B, FEC 1/2 | 12.512 s | 1580 bit/s |
| Format 2, 4096 B, FEC 3/4 | 8.912 s | 2218 bit/s |
| Format 2, 8192 B, FEC 7/8 | 7.880 s | 2509 bit/s |

These are deterministic channel-occupancy results, not over-air claims. Full
methodology is in `docs/OFDM_G2_BENCHMARK_2026-08-10.md`.

## Exact implementation files

Tracked files modified:

- `README.md`
- `config.example.json`
- `docs/ofdm-vhf.md`
- `guardian/_version.py`
- `guardian/config.py`
- `guardian/ofdm/__init__.py`
- `guardian/ofdm/bench.py`
- `guardian/ofdm/framing.py`
- `guardian/ofdm/link.py`
- `guardian/ofdm/metrics.py`
- `guardian/operations.py`
- `guardian/payload/__init__.py`
- `guardian/payload/ofdm_vhf.py`
- `guardian/qt/modem_workspace.py`
- `guardian/qt/settings_dialog.py`
- `guardian/qt/shell.py`
- `guardian/qt/transfer_progress.py`
- `installer/Guardian.iss`
- `pyproject.toml`
- `release/release-manifest.json`
- `tests/test_bench.py`
- `tests/test_modem_workspace.py`
- `tests/test_ofdm_channel.py`
- `tests/test_ofdm_link.py`
- `tests/test_ofdm_payload.py`
- `tests/test_ofdm_phy.py`
- `tools/ofdm_bench.py`

New implementation/test/documentation files:

- `guardian/ofdm/adaptation.py`
- `guardian/ofdm/coding.py`
- `tests/test_ofdm_adaptive.py`
- `tests/test_transfer_progress.py`
- `docs/OFDM_G2_ADAPTIVE_ARQ.md`
- `docs/OFDM_G2_BENCHMARK_2026-08-10.md`
- `docs/RELEASE_NOTES_2.1.0.md`
- `docs/RELEASE_NOTES_2.1.1.md`

## First on-air test

Install 2.1.1 on both stations. Begin with BENCH/MCS1, ARQ 512 bytes, fixed
4096-byte bursts, and compare FEC 1/2, 3/4, 5/6, and 7/8 using the same file.
Then compare 2048/4096/8192-byte bursts at the best reliable FEC. Record elapsed
time, remote SNR/EVM, first-pass bitmap success, retransmitted bytes, and
protocol overhead. Repeat once under a deliberately degraded condition and
confirm only missing blocks repeat. Enable AUTO after these fixed measurements.

The only remaining blocker is physical OTA calibration: the simulator cannot
determine the IC-705's real FEC margin, audio-level behavior, or turnaround
timing. Tune clean-burst upgrade count, burst bounds, and timeout multiplier only
from those measurements.
