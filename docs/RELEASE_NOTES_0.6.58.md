# Guardian 0.6.58 — experimental self-detecting network

This release completes multi-hop discovery steps 9 and 10 behind two
independent, default-off feature flags. Operators can test automatic route use
and live topology regeneration separately without changing manual routes or the
shared Network builder topology.

## Automatic route use (step 9)

- **Experimental: automatically use fresh discovered routes** lets Assisted
  RREQ/RREP discovery continue directly into the existing HAVE_MSG / VARA /
  DELIVERED pipeline without waiting for route approval.
- The switch has no automatic effect in Monitor mode. Returning to Monitor or
  turning the switch off removes only automatic approvals; explicitly approved
  dynamic routes remain approved.
- Explicit next hops, manual overrides and directly heard destinations keep
  their existing precedence. Dynamic evidence never rewrites a manual or
  imported topology row.
- A failed dynamic next hop is degraded and unapproved. At most one fresh,
  bounded discovery attempt is made; payload is never flooded or sent as a
  blind multi-hop guess.

## LINK_ADVERT live topology (step 10)

- New control frame `LINK_ADVERT=16` carries one recent direct-neighbour
  observation. Older Guardians safely ignore the unknown frame and therefore
  form a discovery gap rather than relaying it.
- A one-hop presence advert bootstraps stations that have not heard any traffic
  yet. A changed neighbour set is advertised immediately; unchanged state uses
  the configured interval (minimum one minute).
- A link becomes routable only after both stations independently advertise that
  they hear each other. One-way observations remain visible but cannot create a
  route.
- Confirmed links build a runtime-only graph and Dijkstra-derived routes marked
  with source `link-advert`. Route expiry is capped by the oldest observation on
  its path.
- Advert relaying requires Assisted mode, discovery forwarding and message
  relay. TTL, deterministic jitter, deduplication, allow/deny lists and the
  shared frames-per-minute budget bound channel use.
- Disabling the switch removes only LINK_ADVERT observations and derived routes.
  Manual routes, imported topology and RREQ/RREP routes remain untouched.

## Network UI

**Network → Automatic network** now contains three sub-tabs:

- **Route discovery** contains the existing RREQ/RREP controls and the new
  automatic-use flag.
- **Live topology** contains the separate LINK_ADVERT flag, advertisement
  interval, directed observations, reciprocal state, quality/age/expiry,
  manual advertise action and clear-live-state action.
- **Settings and limits** keeps operating mode, forwarding, TTL, lifetime,
  airtime budget and trust lists together without reducing table space.

Both experimental flags default to off after installation or upgrade.

## Verification

- The RF graph tests cover the S6–N1–N2–N3–S1 route and its branches, automatic
  end-to-end delivery, a completely quiet network bootstrapping itself, one-way
  links, a lost first advert, a mixed-version gap, finite flooding through
  loops, feature-flag combinations, expiry and selective live-state removal.
- The complete automated suite passes, including configuration compatibility,
  UI behavior, routing precedence, modem framing, mail forwarding and updater
  tests.
- Real on-air testing remains required before enabling either experiment by
  default. Start with Monitor, then Assisted/manual approval, then enable one
  experimental flag at a time while measuring airtime.

Protocol and safety details are in
[`MULTIHOP_DISCOVERY.md`](MULTIHOP_DISCOVERY.md).
