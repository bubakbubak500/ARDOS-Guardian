# Guardian 0.6.7

Guardian 0.6.7 fixes the VARA TCP-pair regression introduced in 0.6.6 and
completes the payload handoff synchronization.

## VARA command and data connection

- Keeps VARA command port 8300 and data port 8301 together for the lifetime of
  one application connection. Closing only port 8301 can make VARA terminate
  port 8300, so payload sessions no longer reconnect the data socket alone.
- Reopens the complete 8300/8301 pair when an existing connection is stale.
- Treats VARA as connected only while both sockets and both connection states
  are healthy.
- Cleans up both sockets when the command reader exits. An obsolete reader
  cannot tear down a newer connection.

## Reliable payload handoff

- Keeps the final data write ahead of the independent `DISCONNECT` command with
  the synchronization delay required by VARA's native TCP protocol.
- Uses `BUFFER` as transfer diagnostics without failing a valid transfer merely
  because that asynchronous notification has not arrived yet.
- Preserves graceful `DISCONNECT`, allowing VARA to drain its transmit buffer
  before closing the RF link.

## Verification

- Regression tests cover full-pair recovery and ensure the data handoff barrier
  occurs before `DISCONNECT`.
- All automated tests pass before packaging.

## Important

Install 0.6.7 on both stations. The Windows installer remains unsigned and can
show an Unknown publisher or SmartScreen warning. Release assets include
SHA-256 checksums and GitHub build-provenance attestations.
