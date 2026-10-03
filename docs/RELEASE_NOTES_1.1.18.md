# Guardian 1.1.18

## Adaptive SC-FTN capacity

- SC AUTO learns MCS and FEC independently for each peer and direction. One clean DATA superframe can immediately earn a short trial of a higher-capacity profile, without waiting for two or six more successful frames.
- The automatic density ladder now extends beyond the former MCS17 ceiling through the supported MCS19 mode. Strong DATA feedback can select a larger step; successful delivery can also earn a trial when a fixed SNR threshold would otherwise prevent progress.
- Higher modulation can be combined with stronger FEC when that combination increases useful bits per symbol. Clean delivery no longer causes a downgrade solely because SNR is below a table threshold.
- Trials are limited to 2048 payload bytes and the existing airtime limits. A failed trial restores the previous profile and preserves already acknowledged blocks. Failed candidates are remembered and retried when DATA indicators improve; robust retransmissions do not qualify a faster normal profile.
- The same capacity policy applies across all six SC-FTN bandwidths. Existing radio keying guards, acquisition timing and maximum transmission durations are retained.

## SC-FTN receive display

- Receive progress identifies transferred bytes and marks the total as an estimate until the final block length is known.
- DATA quality reported by the receiver is displayed separately from locally measured ACK quality. Control reception no longer overwrites the active receive DATA quality.
- Channel estimates and data-airtime rates are labeled separately from measured transfer goodput.

**VARA and ARDOP control frames, connection negotiation, control-channel timing and modem implementations are unchanged.** This release follows Guardian 1.1.17.

## Verification

- Local regression verification covered 468 distinct tests, including selective retransmission, independent directions, RX/UI, VARA TCP transfer, AFSK/MFSK, ARDOP and two-radio coordination.
- Real DSP codec tests cover higher constellations and a 32768-byte ARQ transfer through a simulated noisy channel with frequency offset, clock drift and worsening signal quality.
- The release workflow runs the complete test suite and frozen Qt, image-compression and ARDOP self-tests before publishing the Windows packages.
- No new physical radio measurement was performed. Higher field throughput and a performance advantage over VARA remain to be measured on the same radio path and payload.

## Files

- `Guardian-1.1.18-setup-win-x64.exe` — Windows x64 installer.
- `Guardian-1.1.18-win-x64.zip` — portable Windows x64 application.
- `release-manifest.json` — update manifest.
- `SHA256SUMS.txt` — checksums for the release packages.
