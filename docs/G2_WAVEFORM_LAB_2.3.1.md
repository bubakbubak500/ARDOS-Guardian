# Guardian G2 waveform laboratory — 2.3.1

Guardian 2.3.1 is a corrective waveform release.  OFDM, SC-HS, SC-FTN,
control frames and selective-repeat ARQ are unchanged.  The release repairs
the SEFDM operating point that 2.3.0 correctly rejected in the realistic
channel, and changes the optional final Morse identifier from 50 to 40 WPM.

## Why SEFDM 2.3.0 failed

The 2.3.0 receiver first inverted the channel-free non-orthogonal carrier
matrix and then divided the resulting carriers by scalar channel estimates.
Those operations commute for orthogonal OFDM but not for SEFDM under
multipath.  The error was deterministic rather than random: increasing input
SNR could not remove the resulting distortion floor.  The original
`alpha=0.95` / 256-QAM profile therefore remained below its CRC threshold.

The rejected frame was always discarded.  No benchmark delivered wrong bytes.

## 2.3.1 receiver and profile

The revised receiver:

1. estimates CFO from the repeated preamble;
2. removes CFO from the analytic passband signal;
3. learns a regularized 97-tap time-domain inverse from the complete known
   preamble and training prefix;
4. performs the exact real I/Q SEFDM matrix inversion on the equalized audio;
5. removes the residual per-carrier response measured by both training symbols;
6. tracks phase and amplitude on every data symbol with its pilots;
7. still requires every header, manifest and payload CRC to pass.

A sweep around the orthogonal boundary selected this reviewed profile:

| Parameter | Guardian 2.3.0 | Guardian 2.3.1 |
|---|---:|---:|
| Compression `alpha` | 0.950 | **0.985** |
| Active carriers | 60 | 58 |
| Pilot spacing | 8 | **6** |
| Pilots / data carriers | 8 / 52 | **10 / 48** |
| Occupied audio | 2,674 Hz | **2,679 Hz** |
| Robust high-rate MCS | none in realistic channel | **MCS6 32-APSK** |

`alpha=0.985` was selected from a measured sweep, not rounded for appearance.
At 0.980 only 11/16 seeded runs decoded; 0.9825 decoded 8/16; 0.985 and 0.9875
decoded 16/16.  The wider 0.990 point fell to 14/16.  The final value retains
non-orthogonal compression while giving the detector the best measured margin.

## Reproducible result

```powershell
python tools\g2_waveform_bench.py --snr 40 --mcs 4 --sefdm-mcs 6 `
  --fec 7/8 --payload 512 --runs 8 --channel realistic `
  --full-transfer 8192 --burst-bytes 8192 --arq-block-bytes 512
```

The realistic burst channel includes delay, 0.7 gain, a complex 1 ms echo,
2 Hz carrier offset, 0.8 rad phase rotation and 5 ppm sample-clock error.

| Waveform | MCS | decoded | burst payload rate | full-transfer goodput |
|---|---:|---:|---:|---:|
| BENCH OFDM | 3, 64-QAM | 8/8 | 5,333 bit/s | 949 B/s |
| SC-HS | 4, 256-QAM | 8/8 | 7,341 bit/s | 1,412 B/s |
| SC-FTN | 4, 256-QAM | 8/8 | **8,149 bit/s** | **1,553 B/s** |
| SEFDM | 6, 32-APSK | **8/8** | 5,172 bit/s | 654 B/s, one retry |

The wider 32-seed SEFDM sweep produced 27/32 at 30 dB and **32/32 at 35,
40, 45 and 50 dB**.  In the 8 KiB transfer model, fixed FEC 5/6 avoided the
seeded retry and reached 847 B/s.  SC-FTN remains the capacity choice; SEFDM is
now a functioning research alternative rather than the fastest mode.

MCS4 256-QAM still does not pass this realistic SEFDM channel.  The UI says so
and the Modem test page selects MCS6 automatically when SEFDM is chosen.  A
future 256-QAM SEFDM mode needs nonlinear interference cancellation, MLSE or
sphere decoding; merely claiming more nominal bits would not improve delivered
bytes.

## Radio procedure

1. Install 2.3.1 on both stations and keep OFDM/BENCH for normal traffic.
2. In **Modem test**, choose SEFDM.  It opens on MCS6 32-APSK.
3. Start with FEC 1/2, record the far-end audio, and verify header and payload
   CRC.  Progress through 3/4 and 5/6 before trying 7/8.
4. Compare fixed 4 KiB and 8 KiB transfers at the same IC-705 audio drive.
5. Record EVM, residual SNR, retries, clipping and application B/s.  Do not use
   MCS4 for unattended traffic until an on-air recording proves otherwise.
