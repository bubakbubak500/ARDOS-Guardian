# Guardian 0.6.40

**The negotiated slow-keying gap moved to the right edge of the burst.**
0.6.39 held PTT *before* each key-up; the spectrum display says the problem
is the other end of the transmission: a cheap handheld unkeyed the instant
VARA says PTT OFF **cuts the tail off its own burst**, and the peer starts
answering into what is still missing.

The negotiated delay is now a **PTT tail**: after VARA signals PTT OFF, the
transmitter stays keyed for the agreed time and only then drops the carrier.
Key-up is immediate again — VARA starts modulating on its own clock, so
keying late would clip the leader instead.

Everything else about the feature is unchanged from 0.6.39:

- the setting (*VARA FM keying delay*, 0–700 ms, default 0 = off),
- the negotiation (larger of the two requests, carried in spare flag bits of
  the existing HAVE_MSG/ACK_HAVE — wire format untouched, old builds simply
  don't slow down),
- the scope (VARA FM only, host PTT required, per-session, relay legs
  re-negotiate, VARA's speed untouched),
- and the log line, which now reads
  `Slow keying negotiated: PTT held 400 ms after each VARA burst.`

Tests updated to pin the new edge: the tail is held on release only, key-up
never waits, and zero configured keeps today's behaviour — verified against
the three broken variants (tail on key-up, tail on both edges, tail dropped).
