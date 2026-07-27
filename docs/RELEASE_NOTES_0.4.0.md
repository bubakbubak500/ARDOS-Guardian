# Guardian 0.4.0

Guardian 0.4.0 adds a dedicated VARA P2P spectrum window so an operator can
see the receive passband, recent audio activity, and the radio state without
crowding the main operational workspace.

## VARA spectrum and waterfall

- Opens a separate, resizable spectrum window at startup when Guardian VARA
  P2P is the selected payload workflow.
- Displays a live FFT scope and scrolling high-contrast waterfall from the
  explicitly configured radio RX audio input.
- Uses a 0–6 kHz audio passband for VARA FM and 0–3 kHz for VARA HF.
- Shows CAT-derived receive and transmit frequency, active PTT state, selected
  VARA mode, and current VARA link state together above the display.
- Adds Pause/Resume and Clear controls for inspecting recent activity.
- Remembers the floating window position and size.
- Can be reopened from **View → VARA spectrum & waterfall** or with
  **Ctrl+Shift+W**.
- Follows Guardian's light, dark, and system themes.

## Operator safety and resilience

- The spectrum monitor is input-only: it never opens an output device, keys
  PTT, connects VARA, or starts an RF transmission.
- Guardian requires an explicitly selected radio RX input and does not
  silently monitor the Windows default microphone.
- If the selected audio input is missing, busy, or cannot be opened, the
  window reports the condition while the rest of Guardian remains available.
- Closing or hiding the spectrum window releases its audio stream.

## Verification

- Adds deterministic FFT tone-detection and waterfall-palette tests.
- Adds Qt coverage for FM passband selection, RX/TX frequency display, PTT
  indication, pause state, and the no-audio fallback.
- The complete automated test suite passes before packaging.

## Important

Actual coexistence of the monitor and VARA on the same sound interface depends
on the selected Windows audio driver supporting shared input access. If it
does not, Guardian reports that live spectrum is unavailable; radio and modem
configuration should be verified before transmitting.

The Windows package is not Authenticode-signed and can show an Unknown
publisher or SmartScreen warning. Release assets include SHA-256 checksums and
GitHub build-provenance attestations.
