# Guardian 0.6.0

Guardian 0.6.0 makes radio audio selection verifiable, removes spectrum-related
UI lag, and fixes automatic tuning for direct station routes.

## Exact audio endpoint selection

- Shows complete input and output endpoint names in the Audio selectors, with
  non-elided popup rows and full-name tooltips.
- Removes Windows `Microsoft Sound Mapper` and primary-driver aliases that are
  not physical audio endpoints.
- Keeps an unavailable saved name visible in the editable field, but no longer
  presents it as if it were a currently connected device.
- Refuses to fall back to the Windows default microphone or an ambiguous name.
- Validates 48 kHz mono input/output support before starting the control
  channel.
- Records and displays the exact PortAudio endpoint name and index that was
  opened.
- When audio settings change while the control channel is active, closes and
  reopens the channel immediately and reports whether the new endpoints were
  successfully verified.

## Responsive spectrum and waterfall

- Moves FFT and waterfall palette calculations from the Qt UI thread to a
  dedicated single-worker analyzer.
- Caps the rendered spectrum at 512 points and reduces waterfall history
  memory without changing its operator-facing passband.
- Reduces refresh frequency from 12.5 to 6.25 frames per second.
- Stops the spectrum timer and releases input audio while its window is hidden.
- Local UI rendering measurement improved from 1.904 seconds to 0.141 seconds
  for 100 updates, a reduction of about 92.6 percent in main-thread work.

## Direct-route QSY and frequency display

- Tunes a configured direct destination before the first `HAVE_MSG` control
  announcement when automatic QSY is enabled.
- Aborts the send safely if that required QSY fails, instead of transmitting
  on the wrong channel.
- Performs QSY before handing radio resources to VARA, reacquires the radio
  after payload transfer, and stays on the peer channel until the control-layer
  confirmation arrives.
- Preserves the original frequency across the complete direct session,
  including announcement failures.
- Displays MHz values consistently with four decimal places.

## Verification

- Adds regression coverage for pseudo-device filtering, exact PortAudio
  endpoint reporting, direct-QSY ordering, VARA handoff ordering, and the
  four-decimal frequency format.
- All 66 automated tests pass before packaging.

## Important

The Windows installer remains unsigned and can show an Unknown publisher or
SmartScreen warning. Release assets include SHA-256 checksums and GitHub
build-provenance attestations.
