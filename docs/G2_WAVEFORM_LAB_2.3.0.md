# Guardian G2 waveform laboratory — 2.3.0

Guardian 2.3.0 keeps the measured OFDM modem and its control/ARQ protocol intact
and adds three opt-in physical layers.  The engineering target is useful bytes
per second through the approximately 2.7 kHz audio path measured on the IC-705,
not nominal constellation rate.

## Implemented families

| Profile | PHY | Occupied audio | Compression | Data geometry |
|---|---|---:|---:|---:|
| `BENCH` | existing DMT/OFDM | 2,438 Hz | orthogonal | 44 data + 8 pilots |
| `SC_HS_2K7` | RRC single-carrier | 2,700 Hz | Nyquist | 2,400 symbol/s, 60 data + 4 pilots/block |
| `SC_FTN_2K7` | RRC single-carrier FTN | 2,700 Hz | τ = 0.90 | 2,666.7 symbol/s, 60 data + 4 pilots/block |
| `SEFDM_2K7` | non-orthogonal multicarrier | 2,674 Hz | α = 0.95 | 52 data + 8 pilots |

The single-carrier receiver estimates frequency error from a repeated preamble,
uses a long regularized complex LMMSE equalizer, and tracks phase with four
pilots per block.  SC-HS uses 31 taps.  FTN deliberately introduces pulse ISI
and uses 81 taps to recover it.  Both support BPSK, QPSK, 16/64/256-QAM and
16/32-APSK through the same soft-LLR convolutional FEC profiles as OFDM.

SEFDM synthesizes carriers on an explicit non-orthogonal basis and solves the
real I/Q least-squares system at the receiver.  Its α = 0.95 value is measured,
not decorative: α = 0.8 with 70 carriers produced a condition number near
`9e8`, catastrophically amplified noise, and was rejected.  The shipped matrix
keeps an approximately 18% data-carrier advantage over BENCH, but still needs a
very clean path and has a 15.9 dB crest factor.  It is a laboratory waveform,
not the recommended first on-air choice.

## Shared protocol and compatibility

All four families use the same version-2 bootstrap header, punctured FEC,
independently CRC-protected subblocks, manifest, bitmap ACK, selective repeat,
retry strengthening and adaptive burst controller.  The verified control frames
and payload negotiation flag did not change.  Consequently both peers must run
2.3.0 and select the same waveform/MCS; an older G2 station cannot infer a new
PHY from the existing payload flag.

The ARQ state machine now consumes a narrow burst-codec interface.  Its default
adapter calls the original OFDM builder and decoder, which prevents an
experimental modem from forking or weakening the proven link behavior.

## Reproducible burst benchmark

Command:

```powershell
python tools\g2_waveform_bench.py --snr 40 --mcs 4 --fec 7/8 `
  --payload 512 --runs 8 --channel awgn
```

OFDM has no MCS4, so the comparison tool holds it at its fastest existing MCS3.
Results are payload bits divided by generated data-burst airtime; ACK/PTT costs
are intentionally excluded from this first table.

| Waveform | MCS | decoded | burst payload rate | TX crest |
|---|---:|---:|---:|---:|
| BENCH OFDM | 3, 64-QAM | 8/8 | 5,333 bit/s | 14.10 dB |
| SC-HS | 4, 256-QAM | 8/8 | 7,341 bit/s | 9.64 dB |
| SC-FTN | 4, 256-QAM | 8/8 | **8,149 bit/s** | 9.93 dB |
| SEFDM | 4, 256-QAM | 0/8 | rejected | 15.96 dB |

At 50 dB in-band SNR the same SEFDM case reaches 8/8 at 6,827 bit/s.  At
40 dB in the repository's combined delay/gain/1 ms echo/2 Hz offset/5 ppm
channel, OFDM, SC-HS and SC-FTN remain 8/8 while SEFDM rejects all eight headers.
The rejection is the correct result: a CRC failure never reaches the caller as
data.

The high-rate 256-QAM/FEC-7/8 AWGN sweep was:

| Applied SNR | OFDM MCS3 | SC-HS MCS4 | SC-FTN MCS4 | SEFDM MCS4 |
|---:|---:|---:|---:|---:|
| 25 dB | 8/8 | 0/8 | 0/8 | 0/8 |
| 30 dB | 8/8 | 7/8 | 0/8 | 0/8 |
| 35 dB | 8/8 | 8/8 | 8/8 | 0/8 |
| 40 dB | 8/8 | 8/8 | 8/8 | 0/8 |
| 50 dB | 8/8 | 8/8 | 8/8 | 8/8 |

These are deterministic simulator results, not promises about an IC-705.

## Modeled full-transfer result

Command:

```powershell
python tools\g2_waveform_bench.py --snr 40 --mcs 4 --fec 7/8 `
  --payload 512 --runs 8 --channel realistic --full-transfer 8192 `
  --burst-bytes 8192 --arq-block-bytes 512 --turnaround 0.25
```

An 8,192-byte message, one 8 KiB selective-repeat burst, 512-byte subblocks,
FEC 7/8, 250 ms modeled turnaround and no retransmission produced.  Every
candidate used the same 40 dB in-band SNR, 0.7 gain, 911-sample delay, 1 ms / 0.3
echo and +2 Hz carrier offset:

| Waveform | MCS | application goodput | bytes/s | channel occupancy |
|---|---:|---:|---:|---:|
| BENCH OFDM | 3 | 7,589 bit/s | 949 B/s | 8.636 s |
| SC-HS | 4 | 11,294 bit/s | 1,412 B/s | 5.803 s |
| SC-FTN | 4 | **12,428 bit/s** | **1,553 B/s** | 5.273 s |

SC-FTN is therefore 63.8% above the current OFDM modeled application goodput in
this high-SNR channel case.  The figure includes the data burst, bitmap ACK and
turnaround model; it excludes CPU wall time because simulated audio is not
played in real time.

## Why τ = 0.90, not 0.80

The first FTN experiment used 3,000 symbol/s (`τ=0.80`).  Sampling the matched
RRC pulse channel exposed a minimum frequency response around `6.7e-5`: a
linear inverse could recover a clean signal but greatly amplified noise and
stalled near 12 dB residual SNR.  Sweeping integer sample spacings showed that
`τ=0.90` raises the minimum response to about `6.1e-2`.  It retains an 11.1%
symbol-rate gain while allowing the regularized receiver to carry 256-QAM at
high SNR.  A future τ=0.80 mode needs MLSE/BCJR or precoding; publishing it as a
faster working modem would be false precision.

## First radio procedure

1. Leave normal mail on OFDM/BENCH until both stations install 2.3.0.
2. In **Modem test**, generate/transmit `SC_HS_2K7`, first QPSK/FEC 1/2, then
   16-QAM and higher rates.  Record the far end and decode it in the same page.
3. Repeat with SC-FTN only after SC-HS is clean.  Compare received crest, EVM,
   residual SNR and decoded repetitions at identical soundcard drive.
4. Try 16/32-APSK beside QAM at the same information rate when the IC-705 path
   shows compression.  Lower PAPR is a measured advantage; APSK winning is not
   assumed.
5. Treat SEFDM as a high-SNR lab experiment.  Do not select it for unattended
   mail until recordings show every header and payload CRC passing.
6. After choosing a family, set the same family and MCS under Station settings
   on both ends, then run a fixed 4 KiB and 8 KiB file while recording elapsed
   goodput, retries and EVM.
