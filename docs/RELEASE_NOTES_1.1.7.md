# Guardian 1.1.7

Fix slow VARA attachment transfers being cancelled while data is still moving. In the reported 1.1.6 exchange, OK7PS cancelled reception of a roughly 30 kB attachment exactly 180 seconds after START_VARA, despite continuing VARA activity. The sender then reported that its peer closed early.

## Corrections

- Feed received bytes and decreasing VARA transmit-buffer counts into the session progress watchdog. Local TCP writes, repeated BUFFER values and PTT activity alone do not count as RF progress.
- Use the incoming payload header's wire size when calculating the receiving session's timeout and absolute safety cap.
- Refresh the internal receive and transmit-drain inactivity budgets when bytes actually move. Keep the session's independent absolute safety cap and stalled-transfer timeout.
- Check cancellation during blocked socket reads at intervals of at most 250 ms. A cancelled reader no longer waits for the rest of the old payload before releasing transfer ownership.

These changes apply to the shared VARA FM/HF backend, for sending and receiving, both locally originated transfers and store-and-forward relay hops. They do not depend on radio model, CAT/PTT interface, VARA registration status or attachment type. Existing SC-FTN progress handling remains in place. No configuration migration or wire-format change is required.

## Verification

All 719 automated tests passed locally before release.

Automated regressions exercise the real VARA socket-reader and buffer-drain code through the payload backend and session watchdog, with a simulated slow 30 kB transfer exceeding the previous receive and transmit deadlines. Both directions and relay contexts are covered, along with stalled progress, the absolute safety cap, repeated buffer reports and cancellation during a blocked read.

The field logs establish the original failure. This release has not yet been validated by a new physical RF transfer on the reported radio pair; simulated timing tests do not establish throughput or RF reliability.
