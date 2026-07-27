# Guardian 0.6.3

Guardian 0.6.3 makes the FM control channel resilient to the real bit errors
observed in both directions between two IC-705 stations.

## Error-corrected AFSK control frames

- Adds a new protected AFSK framing format with a distinct synchronization
  marker.
- Sends the frame length and control payload three times.
- Reconstructs every bit by majority vote before validating the existing
  Guardian CRC.
- Retains receive compatibility with the legacy unprotected framing used
  through Guardian 0.6.2.
- Reduces the default AFSK output amplitude to avoid overdriving an FM radio's
  USB modulation input.

Both stations must run 0.6.3 to transmit the protected format.

## Actionable audio diagnostics

- Automatically saves the most recent failed four-second control-channel
  recording as `%APPDATA%\Guardian\last-bad-control.wav`.
- Adds the recording path and live, peak and maximum input levels to the
  exported diagnostic report.
- Rate-limits recording updates to avoid unnecessary disk activity.

## Verification

- Verifies recovery after one entire transmitted payload copy is lost.
- Retains clock-error, noise, severe tone-imbalance and unrelated-audio
  regression coverage.
- Verifies the diagnostic WAV format.
- All 76 automated tests pass before packaging.

## Important

The Windows installer remains unsigned and can show an Unknown publisher or
SmartScreen warning. Release assets include SHA-256 checksums and GitHub
build-provenance attestations.
