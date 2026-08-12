# Guardian G2 2.3.3 capacity modem — operating and measurement notes

## Recommended first radio sequence

1. Install 2.3.3 on both stations and keep frame v2, classic FEC and 2K7 for the
   first contact.
2. Run Quick Tune at the intended waveform/width and retain both raw WAV and
   JSON/CSV evidence.
3. Run Full Characterizer. Start with SC-FTN and SC-FDE-FTN at 2K7, then open
   5K/10K/20K only on a radio mode whose audio/RF mask permits it.
4. Compare ordinary QAM with APSK, GQAM and PAS64 at equal digital RMS and
   approximately equal RF deviation. Select by valid goodput and GMI, not EVM
   alone.
5. Enable modern LDPC, then adaptive MCS/train. Enable superframe v3 last and
   only when both peers are 2.3.3.

## Equalization output

Every experimental receive now exposes:

- trained equalizer mode and bounded iteration count;
- normal-matrix condition number;
- colored-noise enhancement in dB;
- residual ISI RMS;
- reference EVM and residual SNR after a CRC-valid decode;
- GMI in bit/symbol;
- number of HARQ-combined blocks and decoder-feedback iterations.

The important 2.3.3 correction is the 48-symbol single-carrier edge guard. The
81-tap receiver has radius 40; the former eight-symbol guard left the end of the
last codeword outside the valid equalizer interval. On a clean SC-FTN 2K7
loopback after the fix, representative CRC-valid results were approximately
1.1% EVM for 16-QAM and 1.5% for 64-QAM, with GMI reaching the full 4 and 6
bit/symbol respectively. These are deterministic loopback values, not an RF
guarantee.

SC-FDE and the time-domain MMSE path intentionally produce the same decisions
for a short stationary channel. SC-FDE's immediate benefit is bounded memory
and scalable processing for long/wide superframes; its radio advantage must be
demonstrated on frequency-selective captures.

## Synthetic FM matrix

The checked-in capacity matrix uses the end-to-end FM modulator, limiter, RF
noise and discriminator, two repetitions per point, 192-byte payloads and
LDPC-3/4. It is designed to reject bad candidates early, not to estimate an
IC-705's absolute throughput. The raw rows preserve seed, profile, SNR, FEC,
success ratio, GMI, EVM, nominal PHY rate and decoded payload airtime rate.

Re-run it with:

```powershell
.\.python311\python.exe -m guardian.waveforms.capacity_report `
  --profiles SC_FTN_2K7 SC_FDE_FTN_2K7 SEFDM_2K7 `
  --mcs 2 3 6 16 17 20 --snr 18 24 30 `
  --fec LDPC-3/4 --repeats 2 --payload 192 `
  --output docs\G2_CAPACITY_BENCH_2.3.3
```

## What measurements decide

- APSK/GQAM/PAS are alternatives, not a guaranteed monotonic ladder. PAS64 has
  lower raw rate than uniform 64-QAM and wins only if its shaping margin reduces
  retries enough to recover that rate loss.
- Wider profiles scale nominal rate almost linearly, but only while received
  SNR density and the radio passband remain adequate.
- A longer superframe saves acquisition/PTT time but increases the amount at
  risk in a fade. Adaptive train length is therefore slow-up/fast-down.
- No mode is called better than VARA until repeated two-radio wall-clock tests
  with an identical payload show it.
