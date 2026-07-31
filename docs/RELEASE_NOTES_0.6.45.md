# Guardian 0.6.45

The map has a real map under it now, and *Show all* actually shows them all.

## A topographic background for the Czech Republic

**ČÚZK's WMTS** — the Survey Office states plainly that "WMTS jsou
poskytovány zdarma a bez registrace", and its data has been **CC BY 4.0**
since November 2023. Verified against the live service: standard slippy-map
`z/x/y` tiles in EPSG:3857, the same grid the canvas draws in.

This is why it is not OpenStreetMap: **OSM's tile usage policy forbids
prefetching**, which is exactly what an offline map needs, so pointing
Guardian at their servers would have been a licence breach dressed up as a
feature. ČÚZK gives us the same thing legally, and with better detail for
the country the net actually operates in.

- **Only what you look at is fetched.** No region downloads, no prefetching.
  At most eight requests in flight, and a zoomed-out view that would need
  hundreds of tiles draws none.
- **Everything fetched is kept**, in `%APPDATA%\Guardian\maps\cuzk-ztm.sqlite`.
  Open the map at home and that ground is still there in the field with no
  network — the case Guardian exists for. The window shows how much is
  cached: `12 tiles cached (1.1 MB)`.
- Credited on screen as required: `© ČÚZK (CC BY 4.0)`.
- A **Map background** switch turns it off; the map then works exactly as
  before, on the graticule alone. Nothing about the feature is a dependency.

Outside Czech coverage the service has nothing to serve, so the background
is simply absent — stations, graticule and bearings carry on regardless.

## The canvas moved to Web Mercator

Not cosmetic. The tiles are served in Web Mercator, and drawing them under
stations plotted in any other projection puts the two out of register — on a
map used to find people, an unacceptable kind of wrong. Verified: Prague's
marker lands inside Prague's own tile, and on screen it sits on the city.

## "Fit to stations" → "Show all", and it now does

The old fit took the larger of the latitude and longitude spans as a
*width*, forgetting that the window is wider than it is tall and that
latitude degrees are stretched by the projection. On a Prague-centred view
it asked for 4.33° of latitude and showed 3.49: **Vienna and Berlin fell off
the screen** with nothing to say they existed. That is the "nezoomuje na vše"
you saw.

The fit now works in world units, where both axes are directly comparable,
and is checked against five spreads in three window shapes — including a
tall narrow window and half of Europe.

Two smaller things with it:

- A lone station used to be framed 57 km wide — correct and useless. The
  floor is now 2°, about 180 km.
- The frame is recomputed when the window is first shown. A widget has no
  real geometry until then, so the original fit used the size hint and never
  corrected itself.

## Tests

Every spread visible in every window shape (the bug, pinned); the projection
reversible in both directions; stations landing inside their own tile; the
tile level rendering near native resolution; the world view refusing to ask
for thousands of tiles; no source meaning no requests at all; the cache
surviving a restart, replacing rather than duplicating, and refusing to
store an empty reply; and slippy-map tile numbering including the edges of
the world, where longitude 180 indexes one past the last column and a pole
runs Mercator off to infinity. All six deliberate mutations were caught.
