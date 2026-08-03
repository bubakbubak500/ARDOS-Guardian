# Guardian 0.6.54

The station map gains practical planning, reachability and field-preparation
tools. The radio protocol, beacons and stored station positions are unchanged.

## Locator grid, range and measurement

- A persistent **Locator grid** selector draws either 4- or 6-character
  Maidenhead cells over raster tiles or the offline graticule. Work and label
  density are bounded when the view is too wide.
- Optional geodesic **50/100/200 km rings** are centred on the accepted station
  locator. They are true great-circle distances rather than degree estimates.
- **Measure** uses two map clicks to show distance and initial bearing. It is
  temporary, stores nothing, and is cleared by Esc, right-click or turning the
  tool off. Position picking and measurement cannot be armed together.

## Reachability colours

- Marker colours and an on-map legend separate a station heard directly, one
  reachable through current manual/learned/advertised relay evidence, one no
  longer currently reachable, and a historical-only position.
- A selected station receives a white ring; active alerts remain the highest
  visual priority with their red pulse. Operators can disable reachability
  colours and retain the earlier age-based rendering.

## Deliberate offline area

- **Save area offline** plans only the currently visible coverage of the public
  ČÚZK topographic WMTS service. The operator chooses minimum and maximum zoom
  after seeing total, cached, missing and estimated additional size.
- One task is limited to 750 tiles, four concurrent requests and a paced queue;
  the SQLite cache is capped at 512 MB. A running job can be cancelled and
  successfully completed tiles remain available.
- The existing automatic on-screen cache remains unchanged. No unseen region
  is crawled and an unavailable tile never prevents the vector map from being
  used.

## PNG situation export

- **Export PNG** saves exactly the rendered canvas: background already present,
  locator/range/measurement overlays, stations, reachability legend, mail and
  relay paths and alert rings. It never triggers a tile request.
- The image is stamped with Guardian version, local creation time and the map
  attribution (or identifies the offline graticule).

## Scope decisions

- Mobile-station trails (M5) remain deferred.
- The proposed day/night terminator was removed from the roadmap by operator
  decision.

## Verification

- All 356 automated tests pass, including pure overlay geometry, bounded tile
  planning, a real Qt-network prefetch into SQLite, cache limits, persisted UI
  choices, reachability classification and a readable rendered PNG artifact.
