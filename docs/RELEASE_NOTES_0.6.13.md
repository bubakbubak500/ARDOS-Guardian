# Guardian 0.6.13

This release is superseded by 0.6.14, which keeps the original VARA TCP
application session alive instead of replacing it during each RF handshake.

This release stops aborting a valid VARA session while waiting for optional
transmit-buffer telemetry.

- Treat a successful TCP `sendall()` on port 8301 as the application-to-VARA
  handoff boundary.
- Track the bytes handed to VARA locally, as established VARA clients do.
- Treat asynchronous `BUFFER` messages as queue telemetry rather than an
  acknowledgement of a particular write.
- Keep the two-second command/data ordering barrier, then send the documented
  graceful `DISCONNECT`, which lets VARA empty its TX queue before closing.
- Retain complete 8300/8301 pair renewal and the 1024-byte transport block.

In 0.6.12 Guardian wrote all 1024 bytes successfully, then sent `ABORT` after
five seconds only because no immediate `BUFFER` message arrived. That abort
caused the peer's empty receive and has been removed.
