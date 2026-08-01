# Guardian 0.6.50

Fixes the working-channel negotiation shipped in 0.6.49, which refused a valid
proposal on air, and stops a channel disagreement from throwing the message
away.

## An unknown reference is no longer a refusal

On air on 2026-08-01 OK7PS proposed 145.300 MHz FM and OK2IPW refused it with
"not a channel this station works that peer on" — both stations on 2 m, both
opted in, both on CAT radios.

- The band test compared the proposal against a reference frequency: the local
  working channel, else the route's calling frequency, else the current dial.
  When none of the three could be produced the comparison failed closed and the
  link could not agree a channel.
- Both inputs go missing on a healthy station. The receiving station need not
  have a route entry for the proposer at all, and a single failed CAT poll
  replaces the whole radio snapshot — frequency included — with an empty one.
- The mode and the amateur bands are now the whole envelope when no reference
  is known. A reference, when one exists, still confines the proposal to its
  band.
- Where the peer was last **heard** is a new reference source, ahead of the
  current dial. It survives both a missing route entry and a failed CAT poll.
- Refusals now name the failed test, the reference and where that reference
  came from, so the next one is diagnosable from a single log line.

## A refused channel keeps the message

- A working channel that cannot be agreed used to cancel the session. The
  message failed after its retries, which is the one thing a store-and-forward
  net must not do over a channel disagreement.
- The receiving station now answers `WORKING_ACK` with a calling-channel token
  instead: both peers stay where they are and VARA carries the payload on the
  channel the control frames are already getting through on.
- That is the existing single-channel behaviour, reached by agreement. A zero
  working frequency is what the payload layer already reads as "do not move",
  so nothing below the session layer changed.
- Only the proposer's own token or the calling-channel answer starts VARA; a
  stale or corrupted token still leaves the session negotiating.

## Verification

- All 325 automated tests pass.
- New coverage: the on-air case end to end — the proposer's channel is followed
  when the receiving station has no route entry and no frequency in its
  snapshot; a proposal bounded by where the peer was heard; a refused channel
  delivering on the calling channel with no `CANCEL`; an unrelated token still
  ignored.
- Still to prove on air: the retune to a followed channel and the restore
  afterwards, and the calling-channel fallback.
