# Guardian 0.6.43

The evening OK2IPW spent chasing a VARA session that looked perfect and put
nothing on the air, turned into four changes.

## Guardian keys the radio for VARA by default

**"Let Guardian key the radio for VARA" is now on for new profiles.** With
our own rigctld holding the CAT port, it is the only configuration that can
work: with it off, Guardian ignores VARA's `PTT ON` and VARA has no port
left to key through either. The result is a textbook-looking session —
`CONNECT`, `BITRATE (1) 566 bps TX`, `PTT ON`, `PTT OFF`, `DISCONNECTED` —
with the transmitter never coming up and not one watt reaching the antenna.
Both directions then fail, because a VARA link is two-way ARQ: the peer's
connect gets no answer either.

**A profile that already stores `false` keeps it.** That station may be
keying through VARA deliberately, and taking that over behind the operator's
back could double-key the radio.

## And it says so when it cannot key

At the moment the codec is handed to VARA, if host PTT is off *and* rigctld
holds the CAT port, the log now says:

> Guardian is not keying the radio for VARA and rigctld holds COM3, so VARA
> has no port left to key through. If the radio stays in receive, enable
> 'Let Guardian key the radio for VARA'.

The one line that would have ended the search in a minute.

Related: if a slow-keying delay is negotiated but host PTT is off, that is
now a warning rather than a cheerful confirmation — Guardian can only slow
keying it performs itself, and silence would leave the peer believing both
ends were holding their tail.

## Slow keying: it already works in both directions

Asked whether an AIOC station gets its negotiated delay when *someone else*
starts the session — it does, and has since 0.6.39. The exchange is
symmetric: the initiator's request travels in `HAVE_MSG`, the responder
answers with the **larger of the two** in `ACK_HAVE`, and both sides adopt
it. So an AIOC station configured for 400 ms gets 400 ms whether it dials or
is dialled, and its peer holds the same tail without configuring anything.

If the peer runs an older build it simply ignores the flag bits and does not
slow down — the AIOC station still applies its own delay locally, which is
the half that matters.

## No more 78 dB signal reports

A squelched FM receiver delivers **digital silence**, so the noise-floor
tracker (which chases the minimum) collapses toward the `1e-5` term in its
own update. Dividing a burst by that produced `S/N ~78.7 dB` on a frame the
same session otherwise scored around 40 dB — measured floor at that moment:
`3.8e-5`.

Silence is not a noise measurement. The reference is now clamped to a level
below any real receiver noise but far above nothing at all, the reading is
capped at **40 dB** (past that the number carries no information anyway), and
no estimate is offered at all until the tracker has heard two seconds of
audio.

## A quieter ABORT

VARA answers `WRONG` to an `ABORT` with no link up, which left a permanent
`"VARA rejected: ABORT"` in the diagnostics of a station whose *peer* had
failed to transmit — an alarming line about the one component that was
working correctly. `ABORT` is now only sent when there is a link to abort.

## Tests

The new default and that a stored `false` survives a config load; the
cannot-key warning firing exactly when both conditions hold; the
unapplicable-delay warning; the squelched-receiver cap and the clamp that
stops a *weak* burst reading the ceiling; the settle guard; and `ABORT`
being sent only with a live link. All seven deliberate mutations were
caught.
