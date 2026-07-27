# Guardian 0.6.5

Guardian 0.6.5 fixes a race between VARA's separate command and data TCP
connections.

## VARA data-port acknowledgement

- Waits for a `BUFFER` notification proving that VARA consumed the Guardian
  envelope from TCP port 8301.
- Sends `DISCONNECT` only after that acknowledgement, preventing the command
  from overtaking the payload and producing a link with `TX: 0 Bytes`.
- Fails explicitly and aborts the link if VARA does not acknowledge the data
  port, instead of reporting a false successful transmission.
- Keeps the shared audio codec assigned to VARA throughout this sequence.

## Diagnostics and responsiveness

- Adds the latest VARA transmit-buffer size and PTT state to exported
  diagnostics.
- Suppresses high-frequency `BUFFER` updates in the activity panel while
  retaining their latest value, avoiding unnecessary Qt rendering work.

## Verification

- Adds regression coverage for TCP data acceptance before graceful
  disconnection and for parsing VARA `BUFFER` notifications.
- All 80 automated tests pass before packaging.

## Important

Install 0.6.5 on both stations. The Windows installer remains unsigned and can
show an Unknown publisher or SmartScreen warning. Release assets include
SHA-256 checksums and GitHub build-provenance attestations.
