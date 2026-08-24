# Guardian G2 2.3.7 - deterministic Quick Tune

This build replaces the former short characterizer with a focused radio-volume
calibration for the current Guardian modem setting.

- **One setting, ten levels.** Quick Tune keeps the selected waveform,
  bandwidth, modulation and FEC. Each direction transmits ten real modem frames
  at Guardian output levels from 10% through 100%.
- **One reply per direction.** The receiving station ignores key-up transients,
  measures the complete sweep and returns only the selected level and its
  diagnostics. The long report pause between every individual burst is gone.
- **Automatic Guardian volume.** A byte-valid, unclipped selection is stored in
  `g2_tx_scales` for that waveform and takes effect immediately. If no safe
  point exists, the previous value is preserved. Quick Tune never changes the
  Windows output mixer.
- **Clear progress and evidence.** The workspace shows ten numbered progress
  segments. The final report shows all ten tested levels, clearly marks the
  selected one, and retains useful SNR, EVM, peak, sync, CFO, JSON/CSV and raw
  WAV diagnostics.
- **Both stations calibrated.** After the first sweep completes, the same
  procedure runs in the reverse direction so each station receives and stores
  its own transmit-volume result.

The Quick Tune, station-lab UI, operations and real-codec round-trip regression
checks pass before packaging. On-air validation should first use the same
waveform and modulation already proven for ordinary message transfer.
