# Guardian 0.6.39

Relay/handshake polish from the first multi-hop field reports, and a
negotiated slow-keying gap for VARA FM stations on AIOC-class cables.

## Handshake: fewer blind transmissions, no more stranded ACKs

- **A lost ACK no longer strands both stations.** When the initiator
  re-announced HAVE_MSG (because the responder's ACK_HAVE was lost on RF),
  the responder — already in ACKED — stayed silent, so every retry burned
  against a peer that had in fact answered. A repeated announcement is now
  answered with a fresh ACK_HAVE. This alone removes a class of
  "transmission counts don't add up" sessions.
- **A blind hop gets 1× ROUTE_QUERY + 2× HAVE_MSG, not 1+3.** When route
  discovery runs and *nobody* offers, Guardian tries the destination
  directly — that is a guess, and the channel already carried one unanswered
  query. Blind announces are now capped at two. A hop somebody vouched for
  (configured route, learned path, discovery offer) keeps the full three.

## Negotiated slow keying for VARA FM (AIOC / cheap handhelds)

A cheap handheld's transmitter dies down slowly after unkeying. Guardian's
short control bursts don't mind, but the long back-and-forth of a VARA FM
transfer does: the peer keys its reply while the other carrier is still
decaying, and the first syllable of every burst is lost.

- **New setting** in *Radio control*, next to Test PTT: **VARA FM keying
  delay** (0–700 ms, steps of 100; default **0 = off, today's behaviour**).
  The operator with the slow radio sets it to match their hardware.
- **Negotiated in the existing handshake.** The request rides in spare bits
  of the flags byte already present in every HAVE_MSG/ACK_HAVE — the wire
  format is unchanged. Both stations settle on the **larger** of their two
  requests, and the responder's ACK carries the result back, so both key
  with the same hold-off.
- **Applied to key-up only**, before each VARA PTT ON, for the duration of
  that one session. Release timing, control bursts, and VARA's own speed and
  protocol timing are untouched. The negotiated value is logged
  (`Slow keying negotiated: 400 ms before each VARA key-up`).
- **FM only.** An HF configuration never requests it and the extra dead air
  never appears there.
- **Backwards compatible.** A 0.6.38-or-older peer parses the frame fine,
  echoes the unknown bits back, and simply doesn't slow down — the
  configured station still applies its own gap; nothing breaks.
- **Each relay leg negotiates its own gap.** What A and B agreed belongs to
  those two radios; when B relays onward to C, the next leg starts from B's
  own setting.
- Requires *Let Guardian key the radio for VARA* (host PTT) — Guardian can
  only slow keying it performs itself. AIOC stations already run this way.

Practical note: keep the value as low as works. With host PTT the hold-off
eats into VARA's own key-to-modulation lead, so very large values can clip
the start of the VARA leader — start at 100–200 ms and raise only if the
far end still loses burst starts.

## Where "the wrong message was sent" comes from

Investigated alongside: the mail list selection and send path key strictly
off the message id, and no defect was found there. What *does* transmit a
message the operator did not just click is **automatic delivery** (Settings →
Network behavior, on by default): when a waiting Outbox/Transit message's
next hop comes on air, Guardian announces it by itself — one message per
sweep, logged as `OK1AAA is heard — sending waiting message #N`. Together
with the re-announce fix above, transmission counts should now match what
the log claims. If the automatic behaviour is unwanted, switch *Automatic
delivery* off.

## Tests

Loopback tests for the blind-announce budget (and the full budget for
vouched hops), the duplicate-announce re-ACK (including no re-ACK once
receiving), negotiation to the larger request on both sides, zero-request
stations keeping today's timing, relay legs starting from their own request,
and the flag-bit field's round-trip, overwrite, rounding, cap and
old-build tolerance; operations-level tests for the FM-only gate and wire
cap, key-up-only sleeping, and the gap following the session and dying with
it; and the settings spinbox default and persistence. All 10 deliberate
mutations of the new logic were caught by these tests.
