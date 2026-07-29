# Guardian 0.6.33

**Changing the VARA HF bandwidth in Settings never reached VARA.** Reported
from the air while testing tactical (2750 Hz) mode: the modem stayed on 2300.

The bandwidth command was only ever sent inside `connect_vara()`. Applying
Settings while VARA was already connected re-applied host PTT and nothing
else, so a setting that existed only in Guardian's config could not reach a
running modem. Selecting 2750 and pressing Apply looked like it worked and
changed nothing — with no line in the log to say otherwise.

- The per-session commands (`PUBLIC ON`, `COMPRESSION TEXT`, `CHAT OFF`, and
  on HF the bandwidth plus `P2P SESSION`) moved into
  `apply_vara_session_settings()`, called both at connect **and** when the
  operator changes them. The log now confirms it: *"VARA settings applied
  (BW2750)."*
- A change of **mode, host or port** picks a different VARA instance
  altogether, so that reconnects instead of re-sending.
- Nothing is pushed into a **live RF link** — the reference warns that
  changing session state mid-connection drops it. The change is applied when
  the link closes, and the log says so rather than failing silently.

FM is unaffected and still never sees a `BW` or `P2P SESSION` command; both
are HF/SAT only and VARA FM answers `WRONG`.

Tests cover the bandwidth reaching VARA on an operator edit (this one fails
against the old hard-coded path, verified by reverting), FM never receiving
either HF command, a live link being left alone, and endpoint changes being
told apart from tuning changes.

## Everything else is confirmed working on air

VARA FM and VARA HF both carry mail end-to-end, and beacon and auto-QSY are
verified in live operation. `STATUS.md` records the detail.
