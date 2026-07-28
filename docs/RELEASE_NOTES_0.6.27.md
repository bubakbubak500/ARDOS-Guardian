# Guardian 0.6.27

## Two network switches were doing nothing

Checked after the question was asked, and the answer was worse than expected.
Of the four mesh options in Settings → Network:

| Switch | Before |
|---|---|
| Automatic route discovery (`auto_route`) | wired — drives `ROUTE_QUERY` |
| Automatic relay (`auto_relay`) | wired — gates `_maybe_relay`, TTL, loop avoidance |
| **Presence beacon** (`beacon_enabled`, interval) | **dead** — `send_beacon()` existed, nothing called it |
| **Auto-delivery** (`auto_deliver`) | **dead** — no consumer anywhere, and default **on** |

Both are now wired.

- **Beacon** transmits `FrameType.BEACON` on its configured interval so peers
  can hear this station and route to it.
- **Auto-delivery** sends a waiting Outbox/Transit message as soon as its next
  hop is actually heard — the point being to catch a peer coming on air.

Both key the radio with no operator asking, so both are gated the same way:
only with a live control channel, only when no session is in flight, and never
while a payload transfer holds the codec. Auto-delivery additionally sweeps at
most every 10 seconds, sends one message per sweep, tries each message once per
run, and skips anything marked failed — a failure stays the operator's to
retry.

**Neither has been on air.** They are wired and unit-tested; treat the first
run as a test.

## VARA HF bandwidth

Settings → VARA gains a bandwidth picker, shown only in HF mode because the
reference lists no bandwidth command for FM. `BW2300` (standard) stays the
default; `BW500` (narrow, for poor conditions or a crowded band) and `BW2750`
(tactical) are the alternatives. Sent during initialisation alongside
`P2P SESSION`.

Both stations must agree on it — it is an operator decision, not something
Guardian infers.

## Not done, deliberately

- **`CLEANTXBUFFER`** — registered-user only, so it cannot be relied on for
  every station. Dropped.
- **`COMPRESSION FILES`** for binary attachments — noted for a later test.
  Whether the setting is sender-side only or must match at both ends is still
  unestablished, and a JPEG is already compressed, so the gain may be nil.
