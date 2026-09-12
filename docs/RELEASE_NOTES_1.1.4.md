# Guardian 1.1.4

Guardian 1.1.4 fixes automatic use of known routes and the unnecessarily long AFSK preamble on periodic beacons.

## Automatic routing

- Routes learned while forwarding a route reply now follow the same automatic approval policy as routes discovered for locally originated mail. A station can send its own message through the learned next hop without starting an unnecessary new discovery query.
- Enabling automatic route use also resumes messages already waiting for manual approval.
- A message waiting for discovery starts through an available route learned from live topology or another query. Its obsolete discovery query is removed instead of continuing to flood or timing out.
- Manual route precedence, route expiry, failed-route restrictions and discovery Off behavior remain in effect. A one-way topology observation alone does not establish a bidirectional route.

## Beacon airtime

- Identical periodic beacons use the ordinary 24-byte AFSK preamble. They no longer enter the 120-second retransmission cache and acquire a 128-byte preamble simply because their position did not change.
- Extended acquisition remains available for actual control-message retries. At 1200 baud this removes approximately 693 ms of unnecessary preamble from each affected beacon.

## Verification

- The full local automated suite passed 654 tests.
- Regression coverage includes a known route through OK2IPW to OK2JLD, recovery from a pending manual approval, delivery when live topology supplies a route during an unanswered discovery query, and beacon versus message-retry audio lengths.
- These changes address reproduced software defects. The original single-station diagnostic does not establish where the remote RREP was lost; physical RF validation still requires the participating stations.
