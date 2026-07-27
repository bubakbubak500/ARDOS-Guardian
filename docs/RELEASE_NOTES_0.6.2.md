# Guardian 0.6.2

Guardian 0.6.2 fixes bidirectional CRC failures observed on the AFSK control
channel between two IC-705 stations.

## CRC-aware AFSK timing

- Keeps the competing clock and symbol-phase interpretations of one received
  burst until the Guardian control-frame CRC can be checked.
- Prefers a CRC-valid interpretation over a slightly stronger but mistimed
  interpretation.
- Prevents a valid frame from being discarded merely because a corrupt timing
  hypothesis has marginally higher tone energy.
- Applies the same payload-validation interface to both control modem profiles.

## Safer IC-705 transmit tail

- Extends the postamble so the final control symbols remain clean.
- Keeps PTT asserted for 250 ms after PortAudio finishes playback, allowing USB
  and radio-internal buffers to transmit the final CRC before CAT releases PTT.
- Clears receive history around local half-duplex transmissions so samples from
  opposite sides of a TX gap cannot form an artificial frame.

## Verification

- Adds a regression test in which the higher-energy timing hypothesis has a bad
  CRC while a lower-energy hypothesis from the same burst is valid.
- Adds coverage for the USB audio PTT lead/tail sequence.
- All 74 automated tests pass before packaging.

## Important

The Windows installer remains unsigned and can show an Unknown publisher or
SmartScreen warning. Release assets include SHA-256 checksums and GitHub
build-provenance attestations.
