# Guardian 0.6.46

This release turns the station map into an operational messaging view and
makes frequency changes safe for radios that have PTT but no CAT control.

## Clearer map and radio paths

- Own and heard-station locator vectors are thicker, with a dark contrast
  under-stroke, larger markers, and bold labels that remain visible over the
  topographic background.
- A station involved in a currently sending, delivered, or received message is
  connected to this station by a prominent directional line. Its label shows
  the great-circle distance in kilometres and the true azimuth.
- Click any positioned heard station to open the standard message composer
  with that callsign already selected.
- The map is now an independent top-level window. Windows no longer forces it
  to remain above the Guardian main window.

## Safe Hamlib Dummy / no-CAT operation

The Hamlib Dummy model can key an RTS/DTR PTT line, but it cannot read or tune
the physical radio. Guardian no longer treats the Dummy model's simulated CAT
answers as real telemetry.

- A **Current radio frequency** field appears in the station header whenever
  Hamlib Dummy is selected. The operator enters the actual dial frequency; it
  is persisted and used anywhere Guardian needs the current channel.
- When a direct route requires another frequency, Guardian displays the
  destination, old and required frequencies, and mode. It waits for **OK**
  after the operator tunes the radio. **Cancel** leaves the message queued and
  sends no control announcement or payload.
- Automatic multi-frequency alert sweeps are disabled for no-CAT radios;
  simulated Hamlib tuning cannot move the real dial.

## Verification

All 289 automated tests pass. New regressions cover marker contrast, station
click detection, mail-history links, compose prefill, independent map-window
ownership, the no-CAT polling command set, and both confirmed and cancelled
manual-QSY paths.
