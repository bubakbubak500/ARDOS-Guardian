# Guardian 0.6.20

Checked against *VARA Protocol Native TNC Commands* (Jose Alberto Nieto Ros,
EA5HVK, 2025-10-10). Three of Guardian's VARA interactions contradict it.

The same document also settles what the on-air logs could not. `BUFFER` is
"sent when VARA adds data to queue" — it fires when the payload enters the
queue, long before anything is transmitted. Neither station has ever logged a
single `BUFFER`, so the payload is not reaching VARA's transmit queue at all.
No RF or PTT-timing problem can suppress that notification, and the steady
1.87-second keying both stations show is VARA's idle loop holding a link with
nothing to send.

- **Stop toggling `LISTEN` around a connection.** Guardian sent `LISTEN OFF`
  before `CONNECT` and `LISTEN ON` afterwards. The reference documents the
  outbound flow as `MYCALL`, `LISTEN ON`, `CONNECT`, and warns that both
  `LISTEN ON` and `LISTEN OFF` "will cause a disconnection if it is received
  in the middle of a VARA connection".
- **Send `CHAT OFF` during initialization.** `CHAT OFF` gives "Limited Idle
  Loops. Avoid the stations stay connected forever in a loop", against
  `CHAT ON`'s "Infinite Idle loop". The stalled session that had to be broken
  by killing VARA is exactly that failure mode.
- **Re-enable compression (`COMPRESSION TEXT`).** Guardian disabled it while
  still padding every envelope to 1024 bytes, so ~654 bytes of zero padding
  were being sent raw — about 18 seconds of airtime at 566 bps, for nothing.
- **Surface `WRONG`.** VARA answers a rejected command with `WRONG`, which
  Guardian discarded. A mis-ordered or unsupported command could go unnoticed
  for an entire session; rejections are now logged with the offending command
  and counted in the diagnostics snapshot.

The automated suite covers the send path issuing no `LISTEN` commands and a
`WRONG` response being reported rather than dropped.

The vendor documentation itself is kept out of the repository — it is
third-party copyright, not ours to redistribute.

**Still open:** why the bytes never reach VARA's queue. `Diagnostics → Test
VARA data path` (0.6.19) answers that directly, and the send log now carries
the data socket's health, generation and reopen count.
