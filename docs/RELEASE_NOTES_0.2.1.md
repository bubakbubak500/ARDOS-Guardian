# Guardian 0.2.1

Guardian 0.2.1 improves direct routing, station input ergonomics, status
visibility, and local VARA startup.

## Routes and radio settings

- Allows the preferred intermediate hop to remain empty. Such a saved route is
  treated as an explicit direct connection even when automatic route discovery
  is enabled.
- Normalizes callsigns to uppercase while they are entered in station settings,
  message composition, and route fields.
- Displays route frequencies in the familiar MHz form, such as
  `144.520 MHz`, while preserving integer hertz internally.
- Replaces free-form route mode entry with choices for VARA FM (`FM`) and
  VARA HF (`USB`) so Hamlib receives a valid radio mode.

## Station context and VARA

- Uses the station context area for actionable information about unread
  messages, queued outgoing or relay traffic, active transfers, and VARA link
  establishment.
- Keeps the context area uncluttered when no action or transfer is pending.
- Lets Connect VARA start the locally configured FM or HF executable when the
  selected modem is not already running, then waits for its TCP command port.
- Reports when the selected VARA variant is unavailable and never attempts to
  launch software for a remote VARA host.
- Honors an explicitly configured VARA executable path instead of silently
  falling back to another installation.

## Verification

- Adds automated coverage for direct routes, formatted frequency input,
  uppercase callsigns, station-context message state, and selected VARA
  startup.
- Verifies both light and dark layouts with the existing offscreen UI render.

## Important

The Windows package is currently not Authenticode-signed and can show an
Unknown publisher or SmartScreen warning. VARA remains proprietary third-party
software maintained by its author; Guardian does not redistribute it or accept
its licence on behalf of the operator.
