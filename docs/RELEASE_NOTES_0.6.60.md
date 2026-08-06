# Guardian 0.6.60 — one routing picture, with the source on every row

0.6.59 made route discovery work and explain itself, but it still left the
operator holding three tables: planned routes on one page, discovered routes on
another, live topology on a third. This release merges them into **Network →
Routes** without letting volatile evidence into the stored route file.

## Every source on one page

Routes now lists, in the order routing actually consults them:

| Source | Origin | Expires |
|---|---|---|
| Manual | operator | — |
| Topology | Network builder | — |
| Heard directly | a received control frame (e.g. a beacon) | with the heard entry |
| Discovered (RREQ) | multi-hop discovery | route lifetime |
| Live topology | reciprocal `LINK_ADVERT` | oldest link evidence |

- A new **Expires in** column marks live rows. `—` means stored.
- A discovered route that has not been approved says so, because an unapproved
  route will not carry a message.
- A planned row hides the duplicate observation for the same destination, so the
  operator's own decision is what they see.
- A one-line note under the table states the precedence rule. Without it a
  topology row for a station that is also heard reads as a contradiction rather
  than a hop that simply is not needed.

## Live rows stay live

Observed rows are read-only. They expire, they vanish on restart, and they are
never written to `routes.json`. Trying to remove one explains that it is an
observation rather than silently doing nothing. This is deliberate: a route
learned at 20:00 with a 30-minute lifetime must not still be asserting itself at
08:00 the next morning.

## Save as manual route

A new button copies the selected live or generated row into the route table as a
**permanent manual route** — the transfer, but as an explicit operator decision:

- A directly heard station becomes a direct route with **no preferred hop**, and
  carries the frequency it was actually heard on, which is a real measurement.
- A discovered route keeps its next hop.
- The stored row is labelled Manual, not the source it came from, because it is
  now an operator decision that was merely informed by the evidence.
- A destination that already has a manual route is refused rather than
  overwritten.
- It also works on a generated Topology row, creating the manual override that
  `network.topology_remove_hint` has always told operators to make but never gave
  them a button for.

## Why the source is not stored as-is

Storing `source="rreq"` in the route table was the obvious implementation and it
is the wrong one. `Route.normalised()` collapses every source to `manual` or
`topology`, so a discovery row would be relabelled **Manual** — which in
`_resolve_next_hop` is the highest precedence there is, above topology — and
`save()` would persist it past the expiry that made it trustworthy. Volatile
evidence would have quietly become a permanent operator override. The read-only
view plus an explicit promote button gives the same overview with none of that.

## Verification

New tests cover the merged table (all five sources, expiry column, unapproved
marker), a planned route hiding its duplicate observation, promoting a heard row
into a direct manual route with its measured frequency, refusing a second
promotion, refusing to remove a live row, and promoting a topology row into a
manual override. The full suite passes, and the frozen application and installer
were built locally.
