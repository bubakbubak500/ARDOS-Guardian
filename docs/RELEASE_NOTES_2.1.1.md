# Guardian G2 2.1.1 — clearer modem workspace

This maintenance release removes two simulator-only pages from the
operator-facing **Modem test** tab bar:

- **Whole transfer** simulated both OFDM endpoints, channel impairment, ARQ and
  acknowledgements entirely in memory.
- **Decode rate vs SNR** injected synthetic noise and repeatedly decoded seeded
  blocks to locate the modem's software reliability cliff.

Both were useful during modem development, but neither transmitted through the
configured radio or measured the station's actual RF/audio path. Their benchmark
engine and automated coverage remain intact, and developers can still run the
same experiments with `tools/ofdm_bench.py`.

The practical **One burst** and **Files and radio** pages remain. They provide
waveform inspection, direct test transmission, clean WAV generation, and decoding
of an actual radio recording.

Guardian's adaptive FEC, aggregate bursts, selective-repeat ARQ, settings, wire
format, and compatibility behavior are unchanged from 2.1.0.
