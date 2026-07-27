# Guardian 0.6.16

This release fixes the remaining VARA FM race where the RF link connected but
Guardian disconnected before the payload entered and drained from VARA's
transmit queue.

- Require a post-write `BUFFER > 0` report proving that VARA ingested the
  payload, followed by `BUFFER 0` proving that the RF queue drained, before
  issuing `DISCONNECT`.
- Keep stale `BUFFER 0` notifications from falsely confirming a new transfer,
  and keep locally written byte counts separate from VARA queue telemetry.
- Treat a peer disconnect before queue drain, or a drain timeout, as a failed
  send instead of reporting delivery from link closure alone.
- Let the drained sender close a completed link; the responder no longer races
  the sender's final `BUFFER 0` notification with its own `DISCONNECT`.
- Retain a clearly logged degraded path for VARA installations that provide no
  usable `BUFFER` reports: wait through the command/data ordering barrier and
  let VARA perform its documented graceful disconnect.
- Raise payload receive timeouts to at least 120 seconds, scale longer waits by
  wire size for slow/noisy links, and keep the session timeout at least 60
  seconds above the payload deadline (180 seconds minimum).
- Restore blocking mode on the shared VARA data socket after every bounded
  receive.
- Send `CANCEL` when a responder cannot validate an inbound payload so the
  initiator fails promptly instead of idling until timeout.

The automated suite covers queue-ingest/drain ordering, stale zero telemetry,
early peer closure, the no-telemetry fallback, socket timeout restoration, and
responder cancellation. A two-station on-air check remains necessary to verify
the modem- and radio-specific path.
