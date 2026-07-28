# Guardian 0.6.28

**The beacon shipped broken in 0.6.27.** The orchestrator method is `beacon()`;
0.6.27 called `send_beacon()`, which does not exist. Every attempt raised
`AttributeError`, was swallowed by the surrounding guard, and logged
`Beacon failed: …` — so the switch went from silently doing nothing to loudly
doing nothing. Fixed: `Operations._tick_beacon` calls `self.net.beacon()`.

The test was the reason this got through. It replaced
`operations.net.send_beacon` with a recording lambda — **creating the very
attribute it was supposed to be exercising** — so it passed against code that
could never work. It now spies on `operations.net.transport.send` instead and
asserts a real `FrameType.BEACON` frame reaches the transport with the right
source callsign. Reverting the fix makes it fail, which was verified rather
than assumed.

Auto-delivery was unaffected: `send_queued`, `heard.active` and
`mailstore.list` all exist and its test patched a real method.

No other change.
