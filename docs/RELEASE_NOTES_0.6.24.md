# Guardian 0.6.24

## VARA HF needs one command Guardian was not sending

`P2P SESSION` is documented as **required** for peer-to-peer work: *"Set the
retrie cycle to 4.6 seconds… This command must be used for P2P connections, not
for Gateways connections."* It is VARA HF and VARA SAT only. Guardian sent
nothing, so an HF link would have run on the default `WINLINK SESSION` — a
4.0-second cycle built for RMS gateways, not for two stations calling each
other. Guardian now sends `P2P SESSION` when the station is in HF mode.

This is the same class of fault as the `LISTEN OFF` problem that cost five
releases on FM: a documented requirement the code did not follow. It is fixed
before the first HF test rather than after it.

## The envelope floor drops from 1024 to 256 bytes

Every outgoing message was padded to 1024 bytes — about 14 seconds of airtime
at the unregistered 566 bps rate, paid by the shortest operational message. The
1024 figure was picked while transfers were failing for an unrelated reason
(Guardian toggling `LISTEN` around `CONNECT`, fixed in 0.6.20), so its
justification no longer holds. 256 bytes is roughly 3.6 seconds there and still
a substantial air frame.

It stays a shared constant rather than a value negotiated per link: both ends
must agree on the padding length exactly, and carrying it in the envelope would
be a wire-format change not worth the risk for the few seconds left. **Both
stations must run 0.6.24** — a 0.6.23 receiver expects 1024-byte padding.

Attachments are unaffected: padding only ever applied below the floor, so a
55 KB photo was never padded and is not now.

The session layer duplicates this floor for its transfer deadlines; a test now
asserts the two stay equal.

The automated suite covers the new floor, large payloads staying unpadded, and
the disconnect budget still scaling with airtime where it matters.
