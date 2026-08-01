# Guardian 0.6.48

This release adds an optional, negotiated split between the calling channel
and the VARA payload channel, and makes automatic relay discovery prefer the
best measured signal.

## Optional calling and working channels

- Single-channel operation remains the default and follows the unchanged
  pre-0.6.48 QSY path.
- **Use separate VARA working channels** is an explicit advanced option under
  Network behavior. Its additional route fields stay hidden until enabled.
- Existing route frequency/mode fields retain their previous meaning. New
  `working_freq_hz` and `working_mode` fields persist independently in JSON
  and in backward-compatible appended CSV columns.
- Two opted-in CAT stations exchange `WORKING_OFFER` / `WORKING_ACK` on the
  calling channel. The compact token proves they independently configured the
  exact same frequency and mode before either radio moves.
- `START_VARA` finishes on the calling channel. Control audio is then released,
  both peers tune and settle, VARA carries the payload, and both restore the
  original frequency and mode before `RECEIVED` / `DELIVERED` resumes.
- A disabled option, missing or mismatched route, incompatible VARA mode,
  No-CAT radio, QSY failure or older peer cannot trigger automatic movement.

## Signal-aware relay discovery

- Each `ROUTE_OFFER` stores its own S/N, receive time and frequency instead of
  looking up a later mutable heard-station value.
- A direct destination remains preferred. Other dynamic candidates rank by a
  present S/N measurement, strongest S/N, freshness and callsign as a stable
  final tie-break.
- Explicit, manual and learned paths keep their existing precedence over
  dynamic discovery. The session log records the chosen candidate and S/N.

## Verification

- All 314 automated tests pass.
- Regression coverage proves unchanged default single-channel frames and QSY,
  hidden-until-enabled UI, config/JSON/CSV persistence, exact two-peer channel
  agreement, mismatch and No-CAT refusal, QSY/restore ordering and S/N ranking.
