# Guardian 0.6.21

Fixes the VARA data-path test shipped in 0.6.19, which was broken in two ways.

- **It never found the VARA client.** The test looked for `runtime.vara`, but
  the client lives on `runtime.operations.vara`, so it reported "VARA is not
  connected" against a fully connected modem. It now reads the right object.
- **It would have written into a dead stream.** Port 8301 is a bridge that
  only carries traffic during a link, so a write with no connection is either
  discarded or — worse — left sitting in VARA to corrupt the next real
  transfer. With no link the test now reports socket health only and says so;
  it writes and waits for `BUFFER` only while a link is up, where a missing
  `BUFFER` is conclusive.
- The report also shows the rejected-command count and the negotiated bitrate.

VARA FM 4.4.0 accepts all five initialization commands from 0.6.20
(`PUBLIC ON`, `COMPRESSION TEXT`, `CHAT OFF`, `MYCALL`, `LISTEN ON`) — five
`OK` responses and no `WRONG`. That is confirmed against a live modem, without
transmitting.

The automated suite now covers the probe locating the client through
`runtime.operations` and writing nothing when no link is up.
