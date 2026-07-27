# Guardian 0.6.4

Guardian 0.6.4 fixes the VARA P2P handoff after a link has been established.

## Reliable VARA payload completion

- Keeps the shared USB audio codec assigned to VARA until its queued payload
  has actually been transmitted.
- Uses VARA's graceful `DISCONNECT` command, which closes the link only after
  the transmit buffer is empty.
- Returns the codec to the AFSK control channel before sending the Guardian
  `RECEIVED` or `CANCEL` response.
- Sends an immediate `ABORT` only after an error or timeout, preventing a
  failed link from repeating BREAK/ARQ bursts indefinitely.
- Serializes VARA payload sessions so two transfers cannot contend for the
  same command, data and soundcard resources.

## VARA PTT setting

- Restores the missing setting that lets Guardian key VARA transmissions
  through Hamlib.
- Applies a changed host-PTT preference immediately without restarting
  Guardian.
- Explains that VARA must not simultaneously own the same COM port.

## Verification

- Adds regression coverage for graceful link closure and the exact ordering
  of VARA completion, soundcard release and Guardian acknowledgements.
- All 78 automated tests pass before packaging.

## Important

Install 0.6.4 on both stations. When Guardian host PTT is enabled, configure
VARA FM without its own access to the radio's COM port.

The Windows installer remains unsigned and can show an Unknown publisher or
SmartScreen warning. Release assets include SHA-256 checksums and GitHub
build-provenance attestations.
