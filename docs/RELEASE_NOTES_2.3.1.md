# Guardian G2 2.3.1 — robust SEFDM and 40 WPM identification

This patch release keeps OFDM, SC-HS, SC-FTN, control frames and ARQ unchanged.

- SEFDM now uses the measured `alpha=0.985`, 58-carrier / 10-pilot profile in
  2,679 Hz of audio.
- Its receiver adds CFO removal, a regularized 97-tap time equalizer, residual
  carrier equalisation and per-symbol pilot tracking.
- MCS6 32-APSK passes 32/32 realistic-channel bursts from 35 through 50 dB.
  MCS4 256-QAM remains explicitly unsupported for this channel.
- The Modem test page selects the robust MCS6 automatically for SEFDM, while
  station settings display the same recommendation for real use.
- The optional post-ACK Morse identifier now sends at **40 WPM** instead of
  50 WPM; its queue ordering and disabled-by-default behavior are unchanged.

The reproducible 8 KiB model completes with SEFDM at 654 B/s using FEC 7/8 and
one selective retry, or 847 B/s with fixed FEC 5/6 and no retry in the seeded
case.  SC-FTN remains the fastest modeled 2.7 kHz mode at 1,553 B/s.

See `docs/G2_WAVEFORM_LAB_2.3.1.md` for the failure analysis, sweep data,
benchmark command and IC-705 procedure.
