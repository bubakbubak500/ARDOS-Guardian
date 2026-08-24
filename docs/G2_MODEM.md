# Guardian G2 soundcard modem

This is the current operating and measurement guide for the modem shipped in
Guardian G2 2.3.7. Dated waveform and station-lab reports remain useful
evidence, but this document defines the current choices and compatibility
boundary.

## Compatibility defaults

VARA P2P remains the default payload transport. Guardian's modem is used only
when both peers negotiate it. For the first contact between two G2 stations:

1. select a 2K7 profile appropriate for the radio path;
2. leave protocol-v3 Superframe and Modern LDPC disabled;
3. begin with frame v2, classic FEC and one train burst;
4. enable newer features one at a time after a clean baseline.

Frame v3, the newer MCS identifiers and modern LDPC require G2 2.3.3 or newer
at both ends. Wider profiles require an audio and RF path that actually passes
their occupied bandwidth. The IC-705 FM path measured in the 2026-08-09 test
passed about 3 kHz, so 5K/10K/20K are not valid assumptions for that setup.

## Available modem families

- **OFDM** — established compatibility baseline.
- **SC-HS** — Nyquist single carrier with frequency-domain equalisation.
- **SC-FTN** — faster-than-Nyquist single carrier at the proven `tau=0.90`
  setting.
- **SC-FDE-FTN** — bounded-memory overlap-save implementation for long or wide
  FTN frames.
- **SEFDM** — experimental compressed multicarrier mode; useful for research,
  not currently the capacity default.

The modem shares framing, interleaving, CRC protection and selective-repeat
ARQ across waveform families. Modern modes add systematic LDPC, soft retry
combining, adaptive MCS/train control and an opt-in protocol-v3 superframe with
independently protected ARQ blocks.

## Recommended radio sequence

1. Record a frame-v2/classic-FEC 2K7 baseline.
2. Run Quick Tune and retain its WAV and JSON/CSV evidence.
3. Compare SC-FTN and SC-FDE-FTN at the same digital RMS and approximate RF
   deviation.
4. Enable modern LDPC, then adaptive MCS, adaptive train and finally
   Superframe v3.
5. Back off immediately if header failures, clipping, unstable EVM/GMI or
   retransmissions rise.

Select a mode by valid wall-clock goodput and completion probability, not raw
bits per symbol or EVM alone. APSK, GQAM and PAS are alternatives rather than a
guaranteed monotonic ladder.

## Evidence to retain

Every meaningful test should record:

- Guardian version, both callsigns, radio, mode, frequency and audio devices;
- waveform/profile, MCS, FEC, frame version, train and ARQ sizes;
- TX scale, lead/tail/guard/hangover and measured wall-clock interval;
- CRC/PER, EVM, residual SNR, GMI, retries and keyed duty cycle;
- the raw receive WAV and exported JSON/CSV;
- exact payload hash before and after transfer.

Use 8, 64 and 256 KiB incompressible payloads, at least five repeats in both
directions. A comparison with VARA must use the same radios, channel, power,
deviation, PTT timing and payload.

## Software-only checks

The UI at **Tools → Modem test** can generate, transmit, record and decode test
bursts. Developer tools include:

```powershell
.\.python311\python.exe tools\ofdm_bench.py
.\.python311\python.exe tools\g2_waveform_bench.py
.\.python311\python.exe -m guardian.waveforms.capacity_report `
  --profiles SC_FTN_2K7 SC_FDE_FTN_2K7 SEFDM_2K7 `
  --mcs 2 3 6 16 17 20 --snr 18 24 30 `
  --fec LDPC-3/4 --repeats 30 --payload 8192 `
  --output artifacts\g2-capacity-review
```

The checked-in 2.3.3 matrix used only two repeats and 192-byte payloads. It is a
deterministic smoke matrix, not a statistically reliable IC-705 throughput or
waterfall measurement.

## Current verification boundary

The original OFDM PHY has moved messages between two IC-705 stations. Adaptive
frame v2, modern LDPC/HARQ, protocol-v3 superframes and the newer capacity
waveforms still require staged two-radio validation. No mode is described as
better than VARA until the repeated wall-clock acceptance test passes.

Historical evidence:

- [`OFDM_AIR_RESULTS_2026-08-09.md`](OFDM_AIR_RESULTS_2026-08-09.md)
- [`G2_STATION_LAB_2.3.2.md`](G2_STATION_LAB_2.3.2.md)
- [`G2_WAVEFORM_LAB_2.3.1.md`](G2_WAVEFORM_LAB_2.3.1.md)
- [`G2_CAPACITY_BENCH_2.3.3.csv`](G2_CAPACITY_BENCH_2.3.3.csv)
- [`RELEASE_NOTES_2.3.3.md`](RELEASE_NOTES_2.3.3.md)
