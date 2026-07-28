# Guardian 0.6.18

Guardian was aborting VARA transfers while VARA was still transmitting them.

The 2026-07-28 OK7PS/OK2IPW logs show the whole sequence. The sender handed
1024 wire bytes to VARA at 18:37:04, saw no `BUFFER` notification within the
10-second ingest window, and fell through to the degraded path, which sent
`DISCONNECT` at 18:37:14 and then waited a flat 30 seconds. VARA honours a
graceful `DISCONNECT` by putting its queued payload on the air *first*, and it
was still keying PTT once a second the whole time. At 18:37:44 Guardian
declared the transfer failed and sent `ABORT`, tearing down a link that was
mid-transmission — VARA logged `PTT ON` twice more after that. OK2IPW,
correspondingly, read zero payload bytes.

The budget was never realistic. Both stations negotiated `BITRATE (1) 566 bps`,
VARA FM's unregistered rate. A padded 1024-byte block is ~29 seconds of
wall-clock airtime at that rate once the ARQ acknowledgements are counted —
the entire 30-second disconnect budget, with nothing left for a single retry.

- Budget the disconnect wait from the airtime actually required, using the
  bitrate VARA reports, instead of a fixed 30 seconds. A 1024-byte block on a
  566 bps link now gets ~87 seconds; a fast registered link is unaffected.
- Never give up on a link that is still keying. `wait_link` now accepts a PTT
  grace period and extends its deadline for as long as VARA keeps keying the
  transmitter, bounded by a hard cap so a wedged modem cannot hold the session
  open forever. A modem that has gone quiet is stuck; one that is transmitting
  is working.
- Parse the `BITRATE` notification, and count `PTT` keyings and `BUFFER`
  reports. These are now in the diagnostics snapshot, so a failing on-air run
  shows whether VARA ever reported a buffer and how much it actually
  transmitted.
- Report how many payload bytes reached the receiver when a receive fails,
  instead of only the exception.

The automated suite covers the airtime and disconnect budgets, the bitrate
parser, the keying counters, and the PTT-aware link wait holding a session open
while the modem transmits and releasing it once quiet.

This diagnosis comes from the two station logs, not from an on-air run — the
timings, the abort, and the zero bytes received all line up, but a two-station
check is still needed to confirm the transfer now completes.
