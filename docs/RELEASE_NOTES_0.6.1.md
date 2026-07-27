# Guardian 0.6.1

Guardian 0.6.1 fixes unstable on-air AFSK control-frame reception and a race
between the final control burst and VARA audio handoff.

## Robust AFSK control reception

- Replaces the single global bit phase with preamble-aided clock recovery.
- Searches a narrow soundcard clock range so timing error does not accumulate
  through the control frame.
- Normalizes 1200 Hz and 2200 Hz energy before slicing, making reception
  resistant to FM de-emphasis and sound interfaces that strongly favor one
  tone.
- Requires the alternating preamble immediately before the sync word, reducing
  false `bad magic` and CRC candidates produced by noise or unrelated VARA
  audio.
- Collapses competing clock/phase interpretations of one physical burst to the
  most confident frame.

## Safe transition to VARA

- Tracks every queued asynchronous control transmission.
- Waits until `START_VARA` has completely left the radio before closing the
  control input and handing the sound interface to VARA.
- Aborts the payload attempt with a clear error if the pending control burst
  cannot finish within the safety timeout.
- No longer suppresses audio-handoff errors and then starts VARA anyway.

## Verification

- Adds clean AFSK round-trip coverage.
- Adds regression tests with 5000 ppm clock error, additive noise and a
  35 dB imbalance between the two AFSK tones.
- Verifies that VARA-like audio does not produce a control-frame candidate.
- Verifies the asynchronous TX completion barrier and failed-handoff behavior.
- A 50-window random-noise check produced zero false frame candidates.
- All 72 automated tests pass before packaging.

## Important

The Windows installer remains unsigned and can show an Unknown publisher or
SmartScreen warning. Release assets include SHA-256 checksums and GitHub
build-provenance attestations.
