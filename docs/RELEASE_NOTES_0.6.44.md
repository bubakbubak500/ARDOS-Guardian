# Guardian 0.6.44

**Stations on a map.** A beacon can now carry this station's position, and a
new window plots everyone who sends one — with the distance and bearing to
each.

## Position in the beacon

The beacon used to be twenty-two bytes of "I am here": type and callsign,
nothing else. It now carries a **Maidenhead locator** in the address field,
which is unused for a broadcast — the same trick alerts use, so the wire
format is untouched and an older build reads the beacon and ignores the
extra text.

Maidenhead rather than binary latitude/longitude for a decisive reason: the
address field is ASCII and upper-cased on the way out, so binary coordinates
come back as `?`. A locator is text, amateur-standard, and small:

| locator | square | beacon | spare |
| --- | --- | --- | --- |
| `JN89` | 100 × 200 km | 26 B | 19 B |
| `JN89HE` | 4.6 × 9.3 km | 28 B | 17 B |
| `JN89HE12` | 460 × 930 m | 30 B | 15 B |
| `JN89HE12AB` | **~50 × 90 m** | 32 B | 13 B |

Guardian stores and sends **all ten characters** — a coarse square can be
derived from a fine one, never the other way round — and it fits beside any
callsign Settings will accept.

Nothing is transmitted unless you set a position *and* beacons are enabled
(they are off by default), and there is a separate *Send in beacons* switch
for using the map without putting your position on the air.

## The map window

*View → Station map* (`Ctrl+Shift+M`):

- Drag to pan, wheel to zoom, and **Fit to stations** frames whatever there
  is — you, whoever you hear, or the world.
- **Pick my position** turns the cursor into a crosshair; one click sets your
  locator. Dragging the map never moves your station.
- Or **type the locator** — most operators know their square, and this works
  before the map has anything on it.
- Heard stations appear with their callsign and square. The square is drawn
  when it is big enough to mean something at that zoom, and a dot when it is
  not; stations not heard for ten minutes fade back.
- A status line gives the path to each: `OK2IPW JN89HE 184 km 121°`.

The map is drawn with QPainter on an equirectangular projection. That is a
deliberate choice over embedding a web map: Leaflet in QtWebEngine would add
roughly **150 MB to a 41 MB installer** for a picture we can draw ourselves,
and it would want a network the whole point of ARDOS is to survive without.
So **the map is useful with no map data at all** — the graticule, the
stations, and the bearings are most of what an operator wants from it. An
offline raster background is a layer to add later, never something the
window depends on.

## In the heard-stations table

Two more columns: **Locator** and **Distance** (`184 km 121°`). Distance
needs both ends of the path, so it stays empty until this station knows
where it is itself — no invented numbers.

## Tests

Locator encoding against six cities whose squares are documented (Prague,
London, Berlin, Tokyo, Washington, Sydney); every precision bracketing the
point it came from; the finest square measuring tens of metres and round-
tripping; distance and bearing Prague→Brno both ways; positions beyond the
dateline or the poles still producing a real square — without the clamp the
index runs negative and Python indexes from the end, handing back a "Z" no
locator alphabet contains.

On the air: a beacon carrying the locator and staying inside the frame; a
station with no position beaconing exactly as before (including when the
config throws); and the locator surviving beside a 16-character callsign.
Critically, **only a beacon sets a position** — a group named `JN89HE` is an
ordinary thing to route to, and reading that destination as a locator would
drop the sender onto the map in Brno.

In the window: picking storing all ten characters, a typed locator accepted
and nonsense refused without losing the good one, and stations without a
position having nowhere to go. All thirteen deliberate mutations were caught.
