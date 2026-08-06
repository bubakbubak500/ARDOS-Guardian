# Guardian 0.6.59 — route discovery you can actually verify

0.6.58 shipped the multi-hop discovery plane behind a mode selector whose
default position could not do anything useful, inside a tab that hid three more
tabs. An operator following the obvious path saw an empty heard-stations table,
an empty route table, a Find route button that silently refused, and no
explanation for any of it. This release removes that dead end.

## Two positions, not three

- **Monitor only is retired.** It recorded breadcrumbs it was never allowed to
  answer with an RREP, returned no route to its own operator, and advertised
  nothing to anybody else. Since it was the default, the normal outcome of
  installing Guardian was a station that took no part in the network and said
  nothing about it.
- `discovery_mode` is now **`off`** or **`assisted`**. A profile that still
  stores `monitor` is read as `assisted` on load; an unrecognised value falls
  back to `off`, because a typo must never be the reason a station transmits.
- The default is **`assisted`**: a fresh install answers a query about itself
  and can look for a route when asked. Approval before a learned route carries
  payload is unchanged, and forwarding for *other* stations still requires both
  discovery forwarding and message relay.

## Network is five flat pages

**Routes · Heard stations · Network builder · Route discovery · Live topology
(experimental)**

- No page hides a second row of tabs inside itself.
- `LINK_ADVERT` is the only remaining experiment and it is the last page. The
  automatic-use switch is no longer labelled experimental; it is an ordinary
  choice between operator approval and immediate use.
- Discovery bounds — forwarding, maximum TTL, route lifetime, the
  frames-per-minute airtime budget and the allow/deny lists — moved to
  **Settings → Network behavior**, beside relay and TTL. The Route discovery
  page keeps only what an operator touches while working, so the tables get the
  space.

## Nothing fails silently

- **Find route** and **Advertise neighbours now** are disabled, with the reason
  on the page and in the tooltip, when the control channel is off, when
  discovery is off, or when `LINK_ADVERT` is off. They are read from the running
  station, not from the widgets, so a setting that has not been saved yet cannot
  make a button look ready.
- **Heard stations** now distinguishes "the control channel is off, so nothing
  can be heard" from "listening, but no control frame has arrived yet", and in
  the second case points at the presence beacon. Its column headers no longer
  clip.
- Unsaved discovery settings are called out with the name of the button that
  applies them.
- A disabled primary action no longer renders in the accent colour. The id
  selector outranked the generic `:disabled` rule, so every disabled primary
  button in Guardian looked like the thing to press.

## Directly heard stations are shown as routes

A station heard directly already resolves as a one-hop next hop. Route discovery
now lists it that way — source **Heard directly**, one hop, expiring with the
heard entry — instead of leaving the operator to combine two tables in their
head. It is an observation, so it never enters the planned route table.

## Verification

- A new end-to-end test builds two stations **entirely from the shipped
  defaults** and walks the whole path: query out, reply back, route in the table,
  operator approval, message delivered. It asserts the answering station both
  replied to a query about itself and heard the asker — neither of which the
  retired monitor position could do.
- Configuration migration (`monitor` → `assisted`, unknown → `off`, default
  `assisted`), the flat page structure, the two-position picker, the
  disabled-action reasons and the heard-derived one-hop row are all covered.
- The complete automated suite passes: RF-graph discovery over
  S6–N1–N2–N3–S1 with branches and loops, lost RREQ/RREP, mixed-version gaps,
  trust and budget limits, `LINK_ADVERT` reciprocity and expiry, modem framing,
  mail forwarding, UI behaviour and the updater.

Real on-air testing is still the next step: assisted with manual approval first,
then automatic use, then `LINK_ADVERT`, measuring airtime at each stage.

Protocol and safety details are in
[`MULTIHOP_DISCOVERY.md`](MULTIHOP_DISCOVERY.md).
