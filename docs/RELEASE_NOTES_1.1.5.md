# Guardian 1.1.5

Guardian 1.1.5 restores the 1.1.2 timing budget for message and routing control bursts, enables native VARA FILES compression, and fixes transfer cancellation and recovery between attempts.

## Control channel and multihop

- Start the audio output stream only when waveform samples are ready, after the PTT lead delay. Starting an empty stream before that delay could report an output underflow and prevent the transition to VARA.
- Restore the 150 ms lead, 250 ms tail and 400 ms audio guard for session and routing controls. Replies allow the peer's historical guard and tail even when it uses a short preamble. Periodic beacons retain their short preamble and edges.
- Drain the queued START frame before giving VARA the shared audio device. Hold further control transmissions and pause their retry timers until the payload releases the radio.
- Give each new route-discovery attempt a fresh query ID while retaining its association with the original message. Relay duplicate suppression no longer discards a fresh attempt merely because the same message is retried.
- Avoid reporting alternative bad-magic candidates when an incomplete repeated FEC frame already contains a CRC-valid copy. Delivery still requires the complete repeated frame.

## VARA transfers

- Send `COMPRESSION FILES` independently of Guardian's optional bundle compression. Migration of the retired compression checkbox no longer forces the native modem into TEXT mode.
- Abort an active VARA transfer when cancelled, suppress its late completion callback, and keep cancellation of queued work from aborting another active message.
- Remove held control requests when their message is cancelled or fails, including its separate route-query ID. Resuming the radio cannot replay those obsolete requests; delivery receipts and unrelated messages are retained.
- Recheck radio ownership after acquiring the transfer lock. A queued transfer cannot bypass a previous transfer's pending radio handoff.
- Preserve a completed payload result while waiting for the final RF handoff. A link closed without BUFFER confirmation is logged as unconfirmed until the receiver acknowledges delivery.
- Add payload-write phase logs and diagnostic session, discovery and handoff metadata without exporting message bodies or attachment contents.

## Verification and scope

- Automated coverage includes direct transfers over a real local TCP command/data pair, ZIP and XZ envelopes, cancellation, serialized radio handoff, multihop retries, control suspension and audio startup ordering.
- The local full suite passed 687 tests; the final cancellation cleanup additionally passed the 218-test focused suite. The frozen Windows application passed its Qt, WinRT and JPEG XL/PNG/XZ compression checks.
- The data-port write, native TCP pairing and Guardian wire envelope remain compatible with 1.1.2.
- Physical radio validation is still pending. The reported CONNECTED/BREAK-only case reached a successful local TCP write without native BUFFER confirmation; these fixes do not by themselves prove that the native modem accepted or transmitted that payload.
