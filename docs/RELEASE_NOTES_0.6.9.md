# Guardian 0.6.9

This release fixes empty VARA sessions where the RF link connected but the
native data port never queued the Guardian envelope.

- Start the VARA command reader before pairing TCP data port 8301.
- Enable public/native operation and LISTEN before P2P sessions.
- Disable modem-side compression for Guardian's already framed binary stream.
- Require a `BUFFER` notification after writing the payload.
- Abort immediately when VARA does not queue the data, instead of sending
  `DISCONNECT` and cycling empty BREAK/QRT exchanges.
- Enable TCP_NODELAY and check the data socket error state before every write.
