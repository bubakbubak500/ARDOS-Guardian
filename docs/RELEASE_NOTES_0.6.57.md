# Guardian 0.6.57 — assisted multi-hop discovery

Guardian can now find a route over stations it cannot hear directly. This
release implements the first field-safe RREQ/RREP stage: monitoring and
operator-assisted use. Automatic acceptance and whole-network link
advertisements remain deliberately disabled until the assisted workflow is
validated on real radios.

## Automatic network tab

Network now has a fourth tab, **Automatic network**, beside Routes, Heard
stations and Network builder. It provides:

- Off, Monitor only and Assisted operating modes;
- bounded TTL, dynamic-route lifetime and frame-per-minute budget;
- optional relay allowlist and denylist;
- manual Find route action;
- live query, route, metric, expiry and approval status;
- explicit Approve selected route and Clear dynamic routes actions.

Monitor only is the default and never transmits. Assisted mode can originate,
answer and forward discovery frames, but an originating message pauses until
the operator approves the returned route. Discovery forwarding also requires
message relay to be enabled, so a station cannot advertise a payload path it
would refuse to serve.

## RREQ/RREP protocol

- New frame types `MULTIHOP_RREQ=14` and `MULTIHOP_RREP=15` do not change the
  version-1 binary layout and do not change legacy `ROUTE_QUERY/ROUTE_OFFER`.
- RREQ uses expanding rings (TTL 2, then 4/6/8 up to the configured ceiling),
  per-origin/query/target deduplication and deterministic relay jitter.
- RREP returns only along stored reverse breadcrumbs. One bounded directed
  repeat tolerates a lost answer without flooding the query again.
- Only the actual destination answers in this release; an intermediate cache
  cannot make an attractive but stale claim on behalf of another station.
- The discovery-only flags metric carries hop count and an accumulated coarse
  S/N penalty. Each relay learns its own remaining next hop as RREP returns.
- Airtime budget, TTL ceiling, expiring breadcrumbs and trust lists bound the
  effect of malformed, duplicated or unwanted discovery traffic.

Older Guardian releases reject the new unknown frame types. A mixed-version
path therefore stops at an older station instead of creating incompatible or
unbounded relay behaviour.

## Routing safety

Dynamic routes are runtime-only and expire. They never replace manual routes
or rows generated from the shared topology. Resolution remains operator-first:
an explicit hop and a manual route remain authoritative, followed by a directly
heard destination. An explicitly approved live route may temporarily precede
the planned topology without modifying it; otherwise topology remains the
cold-start plan. A failed configured or learned hop may start assisted repair,
but the repaired route still waits for approval.

An unsuccessful multi-hop search no longer performs a blind direct HAVE_MSG
attempt to a destination that was never heard.

## Verification

The new `GraphRadioBus` models stations that hear only configured neighbours.
Tests cover the operational graph S6–N1–N2–N3–S1, branches S2/S3/S4/S5, a
cycle, expanding TTL, lost RREQ and RREP, duplicate frames, concurrent equal
query IDs from different origins, a pre-discovery station in the path,
allow/deny rules, frame budget, expiry and operator approval. An end-to-end
test then carries the existing payload across all four hops and returns the
final DELIVERED receipt to S6.

All **397 automated tests** pass. Discovery timing is additionally scaled from
the active AFSK/MFSK control-modem airtime rather than relying on the old fixed
one-hop timeout.

## Deliberately deferred

- Step 9: automatic use of a discovered route without operator approval.
- Step 10: bounded `LINK_ADVERT` or equivalent neighbour exchange for discovery
  of stations nobody has queried and full live-topology regeneration.

See [`MULTIHOP_DISCOVERY.md`](MULTIHOP_DISCOVERY.md) for the wire mapping,
precedence, safety limits and on-air validation plan.
