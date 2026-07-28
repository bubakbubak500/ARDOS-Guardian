# Guardian 0.6.19

The 0.6.18 disconnect budget worked: OK7PS no longer aborted the link, waited
63 seconds and saw a clean `DISCONNECTED`. But OK2IPW still read **zero**
payload bytes over a 90-second link at 28.1 dB SNR, and neither station logged
a single `BUFFER` report. The payload is not reaching the air at all, and that
root cause is still open.

This release fixes what the run did prove, and adds the means to settle the
rest without tying up two stations and a radio.

- **A killed VARA no longer reads as a delivered payload.** When VARA's TCP
  session dies, the reader thread forces `link_state` to `DISCONNECTED` — the
  same value a graceful RF close produces. `wait_link` accepted that as
  success, which is why OK7PS logged `payload completed` at 18:59:49: the
  operator had just killed VARA to break the stalled cycle. Transport loss is
  now tracked separately, `wait_link` never reports it as a reached state, and
  the send logs it as an unconfirmed payload instead of a completed one.
- **The data socket is verified before every write.** A lone `sendall()` into
  a socket the peer has already closed succeeds locally — the reset arrives
  afterwards — so a payload can be "written" to a VARA that will never see it,
  with `data_bytes_written` climbing all the same. Guardian now peeks at the
  socket first and reconnects port 8301 if VARA has dropped it, counting the
  reopens.
- **New: Diagnostics → Test VARA data path.** Writes a short block to port
  8301 and reports whether VARA holds the socket and answers with a `BUFFER`
  report. It needs no radio and no second station, and it separates "VARA
  never sends BUFFER" from "VARA is not reading our data socket".
- Send and receive logs now carry the data-socket health, its generation, and
  the reopen count.

The automated suite covers transport loss not being mistaken for a closed RF
link, the degraded send reporting a killed VARA as unconfirmed, and a write
reconnecting a data socket the peer has closed.

**Still unexplained:** why VARA transmits for 70+ seconds without delivering a
byte, and why no `BUFFER` ever appears. The next step is the new data-path test
plus a look at VARA FM's own window during a send — if its queue stays empty,
the bytes are not reaching VARA and the fault is on Guardian's side of port
8301.
