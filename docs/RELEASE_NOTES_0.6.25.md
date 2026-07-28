# Guardian 0.6.25

**"Waiting to send" no longer counts messages that failed.** The station
context line read `Čeká na odeslání: 1` after a completed transfer with nothing
in flight. `MessageStore.counts()` groups by folder alone, and a failed send
parks its message in the outbox so it can be retried — so one old failure from
an earlier test kept the counter at 1 indefinitely. Queued and failed messages
are now reported separately:

```
Unread messages: 2  ·  Waiting to send: 1  ·  Failed, awaiting retry: 1
```

The count was honest about the folder, just misleading about the state: nothing
was pending, and the operator had no way to see which of the two it was.

The automated suite covers the store distinguishing queued from failed, and the
context line showing each separately or neither.

## Confirmed working in the 0.6.23 field logs

- The session fix holds: `OK2IPW confirmed RECEIVED (final destination)` and
  `active_sessions: 0` immediately after. No more stuck "active transfers".
- A 34 KB attachment went end-to-end in 4 minutes 4 seconds over the primary
  `BUFFER`-drain path, 48 `BUFFER` reports, no data-socket reopens, no rejected
  commands.

## Observation, not yet addressed

The 34 KB transfer sat at speed level 2 (1188 bps) for its whole four minutes,
while the reverse direction reached level 3 (2390 bps). Both links reported
24–27 dB SNR and `NARROW`. A healthy-SNR link that will not climb past level 2
in one direction usually points at the transmit audio — drive level or
deviation — rather than at anything Guardian controls. Worth checking VARA FM's
own level meters before changing code.
