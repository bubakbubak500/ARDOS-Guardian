# Guardian 0.6.55

Guardian 0.6.55 turns a known radio-net diagram into correct local routes at
every station and separates relay handoff from proven final delivery. The
control-frame version and payload format remain compatible.

## Shared network builder

- The former Channel scanner tab in Network is now **Network builder**. The
  tested scanner engine remains in the backend, but it is no longer presented
  as the primary operator workflow.
- A three-step wizard identifies the configured local callsign, imports or
  edits links, and previews the exact routes this station will receive.
- One semicolon-separated topology CSV is valid for the whole network. Links
  include station A/B, `both`/`a_to_b`/`b_to_a` direction, calling frequency
  and mode, optional VARA working channel, positive cost and enabled state.
- Deterministic Dijkstra routing orders paths by total cost, hop count and
  callsign path. A direct neighbour becomes a direct route; a remote target
  names the first hop. The best path with another first hop becomes backup.
- Generated route rows are marked **Topology**. Existing/manual rows remain
  authoritative overrides and are never erased by a topology recompute.
- The topology persists separately from `routes.json`, is recomputed at
  startup and can be exported back to the shared CSV. The UI warns about
  unreachable nodes and first hops this station has not yet heard.

For the reference chain S6–N1–N2–N3–S1, the same file derives S1 via N1 at S6,
via N2 at N1, via N3 at N2 and direct at N3.

## Truthful delivery state and relay receipts

- `RECEIVED` from an intermediate hop now means **Forwarded**. It no longer
  places an unproven message in the UI as Delivered.
- The final station emits a directed `DELIVERED` receipt to the previous hop.
  Each relay forwards it over a local reverse breadcrumb until the origin is
  reached. If an intermediate Guardian restarted, it recovers that breadcrumb
  from the persisted mail hop history. A late receipt upgrades a timed-out
  Forwarded record to Delivered.
- The receipt uses the existing DELIVERED frame fields; no frame type, version
  or payload contract changed.
- Direct delivery retains its fast path: RECEIVED from the final destination
  is already sufficient proof.

## Reliable Transit handling

- A relay reserialises the mail bundle after storage, so the full traversed hop
  history reaches the destination instead of stopping at the last relay.
- The resolved next hop is persisted in the local Transit index. On a later
  automatic pickup it is also recomputed from current manual/topology/learned
  evidence, so a topology edit or restart does not strand the message.
- A failed relay handoff remains Waiting in Transit rather than becoming a
  permanently failed message. Automatic attempts have a five-minute guard to
  avoid repeatedly keying against a peer that remains visible but unavailable.
- A documented `backup=ANY` now enters one-hop ROUTE_QUERY discovery after the
  configured primary exhausts its announcements.

## Multi-hop discovery stays theoretical

`docs/MULTIHOP_DISCOVERY.md` specifies a possible bounded RREQ/RREP extension:
deduplication, TTL, jitter, reverse replies, whole-path metrics, mixed-version
behaviour, trust controls and an RF-graph test plan. Nothing from that proposal
is transmitted by 0.6.55; known networks should use the topology builder.

## Verification

- 372 automated tests pass, including the S6/N1/N2/N3/S1 topology derivation,
  asymmetric/costed links, backup first hops, topology JSON/CSV round trips,
  manual overrides, a directed receipt across three relays, route-history
  carriage, persistent Transit retry and offscreen wizard/workspace behavior.
- The Windows frozen application and installer are built from the same version
  source and smoke-checked before publication.
