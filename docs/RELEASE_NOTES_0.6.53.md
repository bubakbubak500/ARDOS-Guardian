# Guardian 0.6.53

Own-position setup is now one coherent workflow in the station map: detect a
one-time Windows location, pick a point on the map, or type a Maidenhead
locator. The radio protocol and the position carried by beacons are unchanged.

## Detect from this PC

- **Explicit consent, every time.** Detection starts only from the new button
  in **Station map → My position** and a separate Guardian dialog explains the
  one-time request before Windows Location Service is contacted. It never runs
  at startup or in the background.
- **Review before saving.** Guardian converts the temporary coordinates to the
  existing ten-character Maidenhead locator, shows the Windows-reported
  accuracy and source, and draws an amber preview on the map. Nothing changes
  until the operator presses **Use locator**; **Discard** leaves the previous
  station position intact.
- **No precise-coordinate storage.** Latitude and longitude exist only in the
  short-lived preview object. They are not written to `config.json`, events or
  diagnostics. The accepted `station_grid` remains the only stored position.
- **Honest uncertainty.** A fix worse than one kilometre is labelled
  approximate and the operator is directed to verify it on the map or type a
  known locator. Guardian does not turn a city-level IP estimate into a claim
  of house-level accuracy.
- **Native Windows sources, no Guardian location service.** Windows may use
  satellite, Wi-Fi, cellular, IP, a configured default or an obfuscated coarse
  position. Guardian scans no BSSIDs and calls no third-party GeoIP endpoint.
- **Failures stay usable.** Denied desktop-location access, disabled services,
  no data, timeout and missing runtime components have distinct messages. The
  Windows privacy-settings link appears when access is denied, while map
  picking and manual entry remain available.

## Map position controls

- **My position** now groups all three ways to set `station_grid`, the manual
  locator field, and **Send in beacons** in one responsive two-row block.
- **Pick on map** retains the deliberate crosshair and one-click behaviour.
  Manual entry still validates 2, 4, 6, 8 or 10 Maidenhead characters.
- Detecting or accepting a locator never enables beacons and never changes the
  **Send in beacons** switch. For identical configuration the on-air beacon is
  byte-for-byte the same as in 0.6.52.

## Packaging and deferred QR bridge

- The Windows-only PyWinRT Geolocation and Foundation projections are bundled
  in the frozen application; source installs on other platforms retain the
  unavailable fallback without installing Windows wheels.
- The QR-to-phone idea remains documented as a future general companion bridge
  for pairing, profile/message hand-off and possibly a phone fix. 0.6.53 adds
  no server, cloud dependency, token protocol or QR package.

## Verification

- All 348 automated tests pass.
- New coverage validates coordinate bounds, source/accuracy reduction, WinRT
  completion marshaling, denial and timeout, preview-before-save, explicit
  acceptance, discard, approximate warnings, manual/map fallback and the
  invariant that location detection cannot enable position transmission.
- A real unpackaged PySide/Win32 run on the release machine reached Windows
  Location Service and correctly classified its globally disabled desktop-app
  permission as **denied**; the success path is deterministic under a fake
  WinRT provider and awaits a machine with location access enabled for a live
  accuracy comparison.
