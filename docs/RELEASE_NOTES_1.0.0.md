# Guardian 1.0.0 — an emergency communication system for HF/VHF

Guardian is a control and routing layer that sits in front of VARA FM/HF for
amateur-radio emergency messaging. Short ARDOS control bursts negotiate who
carries what; VARA moves the message itself. No Winlink, no internet, nothing
transmitted without an operator asking for it.

Version 1.0.0 does not add a new subsystem. It is the release where the parts
proven on air over the 0.6 series — the control modems, the VARA P2P transfer,
routing with five sources on one page, the station map, net alerts — stop
looking like a work in progress. This release is about what the operator sees.

## The interface says what it is doing

- **A segmented transmission meter.** While VARA is moving a payload, the
  previously empty middle of the header fills with a block meter and a byte
  count. It is driven by the two numbers VARA already reports — bytes handed to
  the modem for this envelope, and bytes still queued for RF — so it shows what
  has genuinely left the station rather than an animation. It is deliberately
  segmented: on an unregistered 566 bps FM link a sliver of a smooth bar
  creeping forward reads as "stuck", while a block lighting up every few
  seconds reads as "working".
- **Checkboxes are visible in the dark theme.** The Windows 11 widget style
  paints a checkbox indicator near-black whatever the palette says, which
  disappeared completely against Guardian's dark surfaces. Guardian now draws
  the indicator itself from a dedicated `control_border` token, with the tick
  rendered as a generated glyph rather than a binary asset in the repository.
- **Detected tool paths are shown.** An empty *VARA FM / VARA HF / rigctld*
  field in Station settings meant "follow detection", but it looked exactly like
  a station whose VARA was missing. The field now shows the path Guardian is
  actually using. Browsing to a file still creates an explicit override; leaving
  it empty still follows detection.

## Less chrome, more content

- **The map window can be maximised**, and its tools are one block instead of
  three strips. *Map background* and *Show all* moved into the tools group and
  the ČÚZK attribution shares the button row, which returns three lines of the
  window to the map itself.
- **Route discovery and Live topology** now carry a one-line description each.
  The five-line paragraphs that used to sit above those tables are in Guardian
  help, where they can be read once instead of skipped every time.
- **Save network settings** sits beside *Clear live topology* rather than on a
  row of its own, and the **Station readiness** dialog no longer spreads three
  components down a half-empty window.

## The help is a manual now

Guardian help grew from 10 topics to 15, and every existing topic was revised.

- **From composing to delivery** walks the whole chain: Outbox, next-hop
  resolution, `HAVE_MSG`/`ACK_HAVE`, the `START_VARA` switch, the envelope, and
  why `RECEIVED` from a relay is not `DELIVERED`.
- **Net alerts and notifications** explains why an alert travels as one byte
  plus 25 characters, what the seed codes mean, the channel sweep, and the rule
  that a notification chime must never reach the transmitter.
- **VARA spectrum and waterfall** documents the window that had no
  documentation.
- **Glossary** defines the vocabulary the rest of the help assumes.
- Stale references to the manual Winlink hand-off — dropped back in 0.6.26 —
  are gone, and *Troubleshooting* gained entries for a meter that will not move,
  and for a route that exists but sends nothing.

## Verification

The full suite (424 tests) passes. Every changed surface was rendered and
reviewed in both themes and both languages, and the frozen application was built
locally. Nothing in the on-air protocol, the frame format, the route file or the
message store changed in this release: a 1.0.0 station and a 0.6.60 station
interoperate.

OK7PS / OK2IPW / OK6LZ
