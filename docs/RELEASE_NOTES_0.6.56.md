# Guardian 0.6.56

Fixes messages arriving one behind — the payload delivered was the previous
session's — and adds a way to wipe the mail database from Settings.

## Payloads no longer arrive one behind

Reported from the field: a new message to a peer transferred normally, but
what the peer's Guardian delivered was the *previous* message sent to it.
Every later message stayed shifted by one.

- The 8301 data socket between Guardian and VARA is deliberately persistent
  across sessions. When an exchange fails midway (CRC error, timeout) or a
  payload arrives with no receive running to claim it, its bytes stay queued
  in that socket's receive buffer.
- The next session's reader then consumed the *old* envelope first — a
  complete, valid envelope, so every check passed — and left its own payload
  behind in the buffer, re-arming the same fault for the session after it.
  One stale envelope therefore shifts every following message by one, which
  is exactly how the fault presented.
- Both the sender and the receiver now drain whatever is already readable on
  the data socket at the start of each session, before this session's RF
  link exists — at that moment, anything buffered provably belongs to an
  earlier exchange. A drain is reported in the log
  (`discarded N stale bytes left on the data socket by an earlier session`).

## Delete all messages

- **Settings → Station** gains *Delete all messages…* — every stored message
  (inbox, outbox, sent, transit) is removed after an explicit confirmation
  that states the count. A recovery tool for a mailbox whose state itself is
  the problem.
- Refused while a transfer is running: a session mid-flight still reads its
  message by id, and wiping under it would fail the transfer confusingly.
- The message-id counter deliberately survives the wipe. Ids already reached
  other stations' session tables and dedup state; a cleared mailbox is no
  reason to mint ids the net has seen from this station before.
- The Home mailbox counters update immediately; the result is logged.

## Verification

- All 377 automated tests pass.
- New coverage: a buffered stale envelope is discarded and the current
  session's payload delivered (plus a regression demonstration that the old
  behaviour delivered yesterday's message); the drain on a real socket pair
  discards only what was already buffered; clearing the store removes every
  bundle while the id counter keeps counting; clearing is refused
  mid-transfer and zeroes the mailbox counters; the Settings button asks
  first, does nothing on Cancel, and reports the count deleted.
