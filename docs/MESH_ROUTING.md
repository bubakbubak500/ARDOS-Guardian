# Importing routing into a mesh

Design note written 2026-07-28 after CSV route import landed in 0.6.26.
**Implemented in 0.6.55:** shared link topology, direction/cost, CSV + wizard,
per-station route derivation, manual overrides and heard-next-hop warnings.
**Implemented in 0.6.57:** bounded multi-hop RREQ/RREP, expiring dynamic routes,
monitor-only and assisted operation, reverse breadcrumbs and airtime/trust
limits. Automatic route use and link advertisements remain later steps; see
[`MULTIHOP_DISCOVERY.md`](MULTIHOP_DISCOVERY.md).

## The problem

0.6.26 imports a **route table**: `destination → preferred next hop, backup`.
That is a directional statement, and it is only true from one place in the
network. The same net looks different from every station:

```
OK7PS ──── OK2IPW ──── OK1AAA
```

| At | to reach OK1AAA | to reach OK7PS |
|----|-----------------|----------------|
| OK7PS  | via OK2IPW | — |
| OK2IPW | direct     | direct |
| OK1AAA | via OK2IPW | via OK2IPW |

One shared file cannot be right for all three. Hand each station the same CSV
and two of them get a table that is subtly, silently wrong — the failure mode
being a message that routes away from its destination and dies at a TTL.

So: is a shared network file solvable, or is per-station import the honest
answer?

## It is solvable, by importing the wrong thing

The mistake is importing routes at all. **A link is a symmetric fact about the
world; a route is a directional opinion derived from where you stand.** Import
the facts and let each station derive its own opinion.

A topology file lists links, not routes:

```
station_a;station_b;frequency_mhz;mode;direction
OK7PS;OK2IPW;145.2375;FM;both
OK2IPW;OK1AAA;145.3000;FM;both
```

Every station imports the identical file. Guardian knows its own callsign, runs
a shortest-path search (BFS, or Dijkstra once links carry a cost) from itself,
and generates its own route table. The tables come out different at each
station because they are *computed*, and they cannot contradict each other
because they derive from one shared set of facts. The "reversed table" failure
becomes unrepresentable.

This is link-state routing, the same reason OSPF floods link state rather than
routes.

### Asymmetric paths

RF links are not reliably bidirectional — different power, antenna height,
local noise. Hence the `direction` column: `both` (default), `a_to_b`,
`b_to_a`. It keeps the common case a two-callsign line while letting an
operator record "I hear the repeater site but it cannot hear me".

### Cost

BFS minimises hop count, which is a poor proxy for a radio path. A `cost`
column (or a quality figure) would let a two-hop VHF path win over a marginal
one-hop HF path. Worth having, not worth blocking on.

## What the static file can never know

Propagation today. A file describes the net as planned; only the air says what
works this afternoon. Guardian already has the runtime half:

- the **heard-stations registry**, populated from every received frame;
- **ROUTE_QUERY / ROUTE_OFFER** discovery when no route is known;
- **learned paths** — `_rx_received` records the hop that actually delivered.

These are complementary, not competing. Import is the cold-start plan; heard
stations and discovery correct it once anyone is transmitting. The right split:

- **imported topology** — what the net is supposed to look like;
- **heard/learned** — what is reachable right now;
- **route table** — the resolution of the two, recomputed as either changes.

## Cheap win available before any of that

Even keeping per-station route files, Guardian can cross-check an imported
table against what it hears and warn: *"OK1AAA is routed via OK2IPW, which this
station has never heard."* That catches the copy-paste-the-wrong-file mistake
at import time instead of at the first failed message. A warning, never a
block — the operator may be importing hours before anyone powers up.

## Implemented resolution (0.6.55)

1. The original per-station route CSV remains available for exact manual
   overrides.
2. The Network builder imports/exports **topology CSV** and also edits links in
   a wizard.
3. Each PC derives routes from its configured callsign. Generated rows carry a
   topology source marker; a manual row for the same destination wins.
4. Direction, positive link cost, disabled links, alternate first-hop backup,
   calling and working channels are supported.
5. Startup and the explicit Recompute action rebuild generated rows from the
   saved topology. The builder previews the result and warns about unreachable
   or not-yet-heard first hops.

Propagation remains runtime evidence rather than a property of the file.
Heard/learned paths, one-hop discovery and the separately expiring assisted
multi-hop layer continue to complement the plan. None overwrites manual rows.
