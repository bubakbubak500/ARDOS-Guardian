# Guardian 0.6.11

This release attempted to replace VARA FM's TCP data connection immediately
before every payload session. It is superseded by 0.6.12 because VARA closes
the command connection when the data connection is closed.

- Keep command port 8300 connected while recycling data port 8301 before each
  outbound and inbound RF link.
- Allow VARA to observe the old data-socket EOF before opening its replacement.
- Pad small Guardian envelopes to a 1024-byte VARA transport block so the modem
  has enough queued data to build its first low-rate air frame instead of
  repeatedly sending BREAK.
- Read and validate the complete padding block before the receiver disconnects.
- Record the data socket generation and local/peer endpoints in diagnostics.
- Preserve the `BUFFER` safeguard and clean abort when VARA still does not
  accept an application payload.
