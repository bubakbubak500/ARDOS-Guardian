# Guardian G2 2.3.0 — high-capacity waveform laboratory

This release keeps the verified OFDM modem and control frames intact and adds
three independently selectable Guardian G2 payload PHYs:

- **SC-HS** — 2,400-symbol/s RRC single-carrier with a lower measured crest
  factor than OFDM and a regularized LMMSE receiver.
- **SC-FTN** — faster-than-Nyquist single-carrier at τ=0.90 / 2,666.7 symbol/s,
  with an 81-tap interference-aware equalizer.
- **SEFDM** — α=0.95 non-orthogonal multicarrier for high-SNR experiments.

The new families support QPSK, 16/64/256-QAM and 16/32-APSK choices, soft FEC
rates 1/2 through 7/8, the existing version-2 manifest/CRC format, selective
repeat, bitmap acknowledgements and adaptive burst sizing.  Both peers must run
2.3.0 and select the same family and MCS.

The Modem test workspace can benchmark, save, transmit and decode each waveform.
Station settings expose the same waveform/MCS selection for real message
payloads.  OFDM remains the default and its existing profile/MCS choices are
unchanged.

A new reproducible comparison tool measures decoded runs, occupied bandwidth,
payload airtime, crest factor, residual SNR and complete selective-repeat
transfers.  In the deterministic 40 dB model with delay, a 1 ms echo and 2 Hz
carrier offset, OFDM MCS3/FEC-7/8 delivered 949 B/s, SC-HS
256-QAM delivered 1,412 B/s, and SC-FTN 256-QAM delivered 1,553 B/s.  These are
simulator results, not on-air claims; the detailed limits and IC-705 procedure
are in `docs/G2_WAVEFORM_LAB_2.3.0.md`.

The application now identifies itself explicitly as **Guardian G2** in the
window title and About surfaces so this development line cannot be mistaken for
the separately installed G1 product.
