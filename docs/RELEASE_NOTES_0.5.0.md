# Guardian 0.5.0

Guardian 0.5.0 restores explicit radio audio-device selection and corrects the
Windows focus behavior of the floating VARA spectrum introduced in 0.4.0.

## Audio settings restored

- Adds a dedicated **Audio / Zvuk** page to Station settings.
- Lists radio RX input and radio TX output devices separately.
- Adds **Refresh devices / Obnovit zařízení** without discarding the current
  selection when an interface is temporarily disconnected.
- Saves both selections back to the station profile on Apply or Save.
- Keeps the fields editable for unusual PortAudio endpoint names.
- Shows a clear status when Windows reports no audio devices.

## More resilient device matching

- Normalizes harmless punctuation and whitespace differences in saved Windows
  device names.
- Recognizes the previously observed
  `Mikrofon (USB Audio CODEC )` spelling as
  `Mikrofon (USB Audio CODEC)` when that endpoint is available.
- Tolerates numeric endpoint prefixes and MME-style name truncation when the
  remaining hardware identity is unambiguous.
- Refuses ambiguous fallback matches instead of silently opening the wrong
  audio endpoint.
- Applies the same resolution to the ARDOS control channel and the spectrum
  monitor.

## Independent spectrum window

- Removes the Windows owner relationship between the main Guardian shell and
  the VARA spectrum window.
- Either window can now receive focus and come to the foreground normally.
- The spectrum remains a separate floating window, but is no longer forced
  above the main application.
- Guardian still closes and releases the spectrum audio monitor during normal
  application shutdown.

## Verification

- Adds regression tests for legacy USB Audio CODEC names, input/output
  direction, and ambiguous-device rejection.
- Verifies that Station settings persist both audio selections.
- Verifies that the spectrum has no parent and no always-on-top flag.
- All 62 automated tests pass before packaging.

## Important

The Windows installer remains unsigned and can show an Unknown publisher or
SmartScreen warning. Release assets include SHA-256 checksums and GitHub
build-provenance attestations.
