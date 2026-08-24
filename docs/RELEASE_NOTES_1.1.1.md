# Guardian 1.1.1 — explicitly selected local map tiles

Guardian 1.1.1 adds one isolated map option and otherwise preserves the radio,
messaging, routing and station-map behaviour of 1.1.0.

## Manually installed XYZ map

- The Windows installation and portable distribution include a writable
  `maps` directory beside `Guardian.exe`.
- An operator may manually copy the exact directory layout
  `maps\tiles\<zoom>\<x>\<y>.png`. Tiles must be 256 × 256 PNG images in the
  standard XYZ/Web-Mercator numbering already used by Guardian.
- Guardian only offers the alternative after it detects that structure. The
  operator must then select **Use manually installed map** in the station-map
  window and explicitly confirm the choice.
- The local files are read directly, never downloaded, modified or copied into
  Guardian's cache. The normal ČÚZK source remains selected when files are
  absent, consent is declined, or the local option is switched off.
- Offline saving is disabled only while the already-offline local source is
  selected. Station markers, locator grid, range rings, reachability colours,
  measurement, panning, zoom, message shortcuts and PNG export are unchanged.

The map files themselves are not distributed by Guardian. The operator is
responsible for manually supplied data and its licence.
