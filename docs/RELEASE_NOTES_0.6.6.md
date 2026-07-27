# Guardian 0.6.6

Guardian 0.6.6 repairs the lifecycle of VARA's TCP data connection.

## Fresh data connection for every session

- Closes the previous port-8301 socket and opens a fresh data connection before
  every outgoing and incoming VARA payload session.
- Prevents Windows from apparently accepting a write into a stale socket while
  VARA receives no bytes and reports `TX: 0 Bytes`.
- Retains the 0.6.5 `BUFFER` acknowledgement before graceful disconnection, so
  a transfer succeeds only after VARA confirms the payload entered its queue.
- Uses an unlimited blocking mode after connecting the data socket, avoiding
  the short startup timeout leaking into payload I/O.

## Connection-state accuracy

- Clears command, data, link and PTT state when the VARA notification reader
  exits.
- Reports data-port reconnection failures explicitly in diagnostics.

## Verification

- Adds regression coverage proving that stale data sockets are closed and
  replaced before a payload transfer.
- All 81 automated tests pass before packaging.

## Important

Install 0.6.6 on both stations. The Windows installer remains unsigned and can
show an Unknown publisher or SmartScreen warning. Release assets include
SHA-256 checksums and GitHub build-provenance attestations.
