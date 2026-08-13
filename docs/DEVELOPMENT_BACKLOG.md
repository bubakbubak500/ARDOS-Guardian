# Guardian G2 development backlog

_Updated after the 2.3.4 release preparation on 2026-08-13._

This is the only active work list. Completed implementation plans have been
removed; dated release notes and measurement reports remain the historical
record.

## P0 — prove the modem on real radios

1. Run a two-radio progression on the same 2K7 path: frame v2/classic FEC
   baseline, SC-FTN, SC-FDE-FTN, modern LDPC, adaptive MCS/train, then
   protocol-v3 superframe.
2. Test 8, 64 and 256 KiB incompressible payloads at least five times in both
   directions. Record median and p95 wall-clock goodput, PTT cycles, keyed duty,
   first-pass rate, retransmitted bytes and decode time.
3. Save raw WAV plus JSON/CSV for every run and require exact payload equality.
4. Repeat the same payload and timing with VARA FM Narrow. Call the result a win
   only if median goodput improves by at least 10% without worse completion or
   RF/duty behaviour.
5. Verify occupied bandwidth and adjacent-channel emissions with an external
   SDR or service monitor before treating a high-capacity mode as operational.

## P1 — make measurements statistically useful

1. Expand transition-region capacity sweeps from two repeats to at least 30
   deterministic seeds and report confidence intervals instead of a single
   pass ratio.
2. Add 8/64 KiB cases, p50/p95 completion time and separate waveform airtime
   from CPU decode time.
3. Maintain a versioned raw-capture regression corpus. Every receiver change
   must decode the same captures rather than a newly favourable random channel.
4. Correlate the FM model against IC-705 captures and keep linear-channel,
   FM-model and on-air results visibly separate.

## P1 — harden the phone companion

1. Exercise the frozen application on supported Windows versions with shared
   Wi-Fi, Wi-Fi Direct where supported and the Mobile Hotspot fallback.
2. Test pairing, suspend/resume and Field Watch on current iOS Safari and
   Android Chrome devices.
3. Add command/pairing rate limits, a strict Content-Security-Policy and
   explicit Host/Origin validation.
4. Give remote emergency arming a short absolute lifetime and automatically
   disarm after a transmission attempt.
5. When previews are hidden, omit message bodies from the state response and
   fetch a selected body through a separate authenticated action.

## P2 — experiments selected by evidence

- Per-carrier bit loading or carrier disabling, only if real captures show
  stable notches or frequency-selective loss.
- Explicit redundancy versions for incremental-redundancy HARQ, compared with
  the current soft combining on expected wall-clock goodput.
- Lower SC-FTN packing factors only after detector complexity and CPU budget are
  measured.
- Non-linear predistortion only with external RF-mask measurement.
- More complex SEFDM/time-frequency packing only if it beats SC-FTN on the same
  captured channel and CPU budget.

## Cross-line operational checks

- Capture and diagnose the next real `RX bad frame: bad magic` occurrence.
- Complete on-air alert-frequency-sweep validation.
- Validate assisted discovery and live topology over at least three RF hops.
- Add Authenticode signing before broad public distribution.

## Release discipline

- Build installers only from a committed, tagged source tree.
- Keep G1 and G2 update URLs, AppIds, executable names and data directories
  separate.
- A speed claim must say whether it is nominal PHY rate, decoded airtime rate,
  modeled channel goodput or measured wall-clock application goodput.
