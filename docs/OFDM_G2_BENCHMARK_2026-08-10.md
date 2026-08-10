# G2 adaptive OFDM simulated benchmark — 2026-08-10

This is a deterministic software benchmark, not an over-air result. It uses the
BENCH waveform, MCS1/QPSK, a 2471-byte seeded payload (the same size as the
2026-08-09 radio comparison), 20 dB in-band simulated SNR, the realistic echo/CFO
path from `guardian.ofdm.bench`, and 0.4 seconds of modeled PTT turnaround per
keyed transmission.

| Link setting | Result | Modeled channel time | Goodput | Data + ACK bursts | Retransmitted |
|---|---:|---:|---:|---:|---:|
| Legacy v1, 512 B stop-and-wait, FEC 1/2 | pass | 17.536 s | 1127 bit/s | 5 + 5 | 0 B |
| Format 2, 512 B burst, FEC 1/2 | pass | 18.256 s | 1083 bit/s | 5 + 5 | 0 B |
| Format 2, 4096 B burst, FEC 1/2 | pass | 12.512 s | 1580 bit/s | 1 + 1 | 0 B |
| Format 2, 4096 B burst, FEC 3/4 | pass | 8.912 s | 2218 bit/s | 1 + 1 | 0 B |
| Format 2, 8192 B burst, FEC 7/8 | pass | 7.880 s | 2509 bit/s | 1 + 1 | 0 B |

The deliberately small format-2 burst is slightly slower than legacy because it
pays for the new manifest and bitmap without amortising any extra turnaround.
That is expected and is why AUTO starts at 2048 bytes. At the same robust FEC,
one 4096-byte keyed burst improves this modeled transfer by about 40% over the
legacy exchange. Higher punctured rates improve it further on this clean channel.

For the fixed 4096-byte/FEC-1/2 case, generated samples account for 11.376 seconds
of data and 0.336 seconds of ACK airtime; turnaround is 6.4% of modeled channel
occupancy. At 512-byte bursts, turnaround is 21.9%. That is the bottleneck the
new protocol was designed to remove.

The deterministic selective-repeat integration test erases exactly one member of
a four-block burst. The receiver retains three blocks, sends a partial bitmap,
and the transmitter resends 512 bytes—not the original 2048-byte burst—with
stronger retry FEC. Final reassembly is byte-identical and first-pass accounting
reports 3/4.

Reproduce fixed profiles with the shared CLI engine:

```powershell
python tools\ofdm_bench.py --profile BENCH --mcs 1 --fec 1/2 `
  --burst 4096 --arq-block 512 --bytes 2471 --snr 20

python tools\ofdm_bench.py --profile BENCH --mcs 1 --fec 7/8 `
  --burst 8192 --arq-block 512 --bytes 2471 --snr 20
```

CPU wall time is excluded because the simulator hands samples between queues
faster than a radio plays them. `channel_seconds` is generated data airtime plus
generated ACK airtime plus configured PTT turnaround, so it is comparable across
profiles. The next IC-705 test must record real elapsed wall time as well.
