# Guardian 0.6.14

This release fixes the VARA FM data path by preserving one command/data TCP
application session for the lifetime of Guardian's modem connection.

- Keep the startup TCP pair on ports 8300/8301 for every incoming and outgoing
  RF session, matching the native VARA lifecycle used by established clients.
- Do not close and recreate the data socket while the peer is already starting
  an inbound VARA connection.
- Do not resend `LISTEN ON` after `START VARA`; VARA documents that changing
  `LISTEN` during a connection causes a disconnect.
- Retain the 1024-byte framed payload, local queue telemetry, two-second
  command/data ordering barrier, and graceful `DISCONNECT`.
- Add a regression test proving that payload sessions cannot replace the
  startup TCP pair or reconfigure the inbound listener during RF setup.

In 0.6.13 both stations recreated ports 8300/8301 after the audio handshake.
The receiver did this while the caller was already establishing the RF link,
and the later data write went to a new application session that VARA did not
use for that link. The radio ARQ therefore exchanged only link-control frames
while both VARA counters remained at zero.
