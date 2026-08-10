# Guardian G2 2.1.0 — adaptive OFDM bursts and selective repeat

This release attacks the bottleneck measured in the 2026-08-09 IC-705 tests.
The radio path could already move coded symbols at a useful rate; Guardian lost
most of that advantage by keying, synchronizing, turning around, and sending an
ACK after every 512-byte block. Format 2 groups several independently protected
blocks into one keyed transmission and acknowledges them with one bitmap.

## Faster clean links without giving up the robust floor

The existing K=7, 171/133 convolutional code remains the mother code. Guardian
now supports explicit punctured rates 1/2, 2/3, 3/4, 5/6, and 7/8. The robust
header names the exact profile; the receiver never guesses. Headers, manifests,
and ACKs remain BPSK/rate-1/2.

AUTO starts at the known-safe rate 1/2 and upgrades cautiously after three clean
bursts. A failure retreats immediately. Fixed mode can force any rate for
reproducible radio sweeps.

## One PTT cycle, several recoverable blocks

Format 2 adds a robust manifest and up to 32 independently FEC/CRC-protected ARQ
subblocks per keyed burst. Burst targets range from 256 bytes to 16 KiB; ARQ
subblocks can be 256, 512, or 1024 bytes and default to 512.

The receiver keeps every valid subblock and returns a variable bitmap. A retry
contains only missing identities and uses stronger FEC. Duplicate and late
retransmissions cannot duplicate application bytes. The 2.0.5 final-ACK linger
now repeats the complete bitmap if the last acknowledgement was lost.

## Joint adaptation and duration-aware timing

One controller owns FEC and keyed-burst size. After a clean hysteresis window it
alternates one faster FEC step and one longer-burst step. On trouble it first
strengthens FEC, then shortens the next burst. Success, retry, remote link SNR,
and modeled goodput are retained as EWMAs.

ACK and receive deadlines are derived from the actual variable waveform length,
PTT turnaround, and processing margin. A 0.5–4.0 multiplier is available for
measured radio-specific margin; there is no single timeout sized for the old
512-byte frame.

## Operator and diagnostic changes

Station settings add AUTO/fixed FEC, AUTO/fixed/min/max burst size, ARQ subblock
size, timeout scaling, and a legacy comparison switch. The live transfer panel
shows FEC, burst/ARQ sizes, first-pass success, retries/retransmitted bytes, and
measured goodput.

The Modem test workspace and `tools/ofdm_bench.py` can run fixed or adaptive
profiles. Their efficiency report separates data-carrier raw rate, FEC loss,
frame/ACK airtime, retransmission loss, turnaround loss, and final application
goodput.

## Simulated result and validation

For the same 2471-byte size used in the radio comparison, BENCH/MCS1 at 20 dB
with 0.4-second modeled turnaround measured:

- legacy 512-byte stop-and-wait/FEC 1/2: 17.536 s, 1127 bit/s;
- format 2, 4096-byte burst/FEC 1/2: 12.512 s, 1580 bit/s;
- format 2, 4096-byte burst/FEC 3/4: 8.912 s, 2218 bit/s;
- format 2, 8192-byte burst/FEC 7/8: 7.880 s, 2509 bit/s.

These are deterministic simulated channel-occupancy figures, not over-air claims.
The new test suite covers all five codecs, header metadata, manifest integrity,
partial corruption, selective retry, lost ACKs, duplicates, hysteresis,
fixed/legacy modes, 256/512/1024-byte ARQ blocks, timeout scaling, and exact
end-to-end reassembly. See `OFDM_G2_BENCHMARK_2026-08-10.md` for the full setup.

## First radio test

Both stations should install 2.1.0. Start with BENCH/MCS1 and compare fixed FEC
1/2, 3/4, 5/6, and 7/8 at a 4096-byte burst; then compare 2048, 4096, and 8192
bytes at the best reliable rate. Record elapsed time, first-pass bitmap result,
remote link SNR/EVM, and retransmitted bytes for the same file. Enable AUTO only
after those fixed points establish the IC-705's real margin.

Frame format 1 remains decodable and a fixed legacy switch remains available,
but format sub-version negotiation is not part of the control handshake. Use the
same release and mode at both ends; enable legacy mode on both ends for a 2.0.x
comparison.
