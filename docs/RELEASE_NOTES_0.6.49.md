# Guardian 0.6.49

This release makes the working-channel negotiation usable between two stations
that configured different working frequencies for the same link, and fixes a
compose window that could stay on screen after the message was queued.

## The proposing station names the working channel

- Until now `WORKING_OFFER` was accepted only when the receiving station had
  independently configured the byte-identical frequency and mode. Two stations
  that each had a perfectly good working channel for the link — 145.350 here,
  145.300 there — cancelled the session instead of running it.
- The station that opens the session now names the channel and the receiving
  station follows it. Its own route entry becomes the reference the proposal is
  judged against, not a requirement the proposal has to match.
- A proposal is followed only inside an envelope the receiving station sets for
  itself: separate working channels enabled, automatic QSY enabled, a real CAT
  radio, a mode the local VARA can use, and a frequency in the same amateur
  band this station already works that peer on. Everything else is refused with
  the reason in the session log, exactly as before.
- Without a working channel configured for that peer, the route's calling
  frequency bounds the proposal; with neither, the current dial does; with none
  of the three there is nothing to bound it and the proposal is refused.
- `guardian/radio/bands.py` holds the IARU Region 1 band edges used for that
  bound. It is not a licence check and authorises nothing — it exists so that
  automation driven by another station cannot move this radio across bands or
  outside the amateur service.
- The wire format is unchanged: the same `WORKING_OFFER` / `WORKING_ACK` frames
  carry the same compact channel token, now also readable back by the peer
  (`parse_working_channel_token`).
- Which channel a session uses therefore depends on who opens it. Operators who
  want one fixed frequency per link should still configure both route tables
  alike; the negotiation simply no longer fails when they differ.

## Compose window closes when the message is queued

- Queueing a message opened from the map could leave the compose dialog on
  screen on some Windows machines. The message was stored correctly and the
  window could be closed with its X, but it did not close itself.
- The map opened the modal dialog from inside the canvas mouse-release handler,
  so its event loop ran inside an unfinished mouse event with the implicit
  mouse grab still held. The dialog is now opened from the idle event loop
  after the click completes.
- `ComposeDialog` also closes as soon as the message is on disk, before the
  refresh, the log line and the listener signal. No failure in that bookkeeping
  can leave a dialog standing over a message that was in fact queued.
- Message ids are reported to listeners as a Python object rather than a Qt
  signed 32-bit int. Half of all callsigns hash to a station prefix above
  2^31, and those ids were silently truncated to a negative number in transit.

## Verification

- All 321 automated tests pass.
- New coverage: two stations with different working channels agree and deliver
  on the proposer's; the peer cannot move this station to another band, outside
  the amateur service, or onto a mode the local VARA cannot use; the opt-in,
  automatic QSY and CAT-radio requirements still hold; a proposal bounded by
  the calling frequency alone; the channel token reads back as what it encoded;
  the compose dialog closes and reports the stored id; the map defers its
  dialog out of the click.
- Still to prove on air with two CAT radios: the retune to a followed channel
  and the restore afterwards.
