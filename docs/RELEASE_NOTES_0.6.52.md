# Guardian 0.6.52

Desktop notifications with two levels of urgency, and a situational map:
a side panel with the numbers, the real relay paths mail travelled, and a
pulsing mark where an alert came from.

## Desktop notifications

- **The polite level.** A new message or a routine net alert arriving while
  Guardian is in the background shows a Windows tray toast and plays a soft
  two-tone chime. Nothing fires while the operator is already looking at the
  window, and starting Guardian over a week of unread mail replays nothing.
- **The unmissable level.** An URGENT or EMERGENCY alert — or mail flagged
  with that priority — raises an always-on-top window with a sound that
  repeats every four seconds until acknowledged. This level deliberately does
  not use Windows toasts, so no Focus Assist setting can silence a MAYDAY,
  and it fires even when notifications are otherwise disabled.
- **The chime can never leave the transmitter.** Sounds play only on the
  Windows default output device, and the player first proves that device is
  not the one configured as the radio's audio output. Unknown default, or the
  radio codec as default: silence, with the reason logged once. Chimes are
  also withheld while PTT is keyed or VARA holds the codec.
- **Tray presence.** Guardian now has a tray icon — click to raise the
  window, right-click for Open/Exit. A station running unattended finally has
  a face in the taskbar.
- Both sounds are synthesized at first use, like the app icon — no binary
  blobs in the repository. Two switches on the Station settings page:
  notifications, and sound.

## Situational map

- **A side panel with the numbers.** Every heard station in a table beside
  the canvas: callsign, locator, distance, bearing, S/N, age, the channel it
  was heard on, and what it can reach. Stations without a position are listed
  too, their geometry left honestly blank. Click a row to centre the map on
  that station; double-click to compose to it. Selection survives the
  once-a-second refresh.
- **The relay path mail actually took.** Received messages record their hops,
  and the map now draws them: dashed cyan segments from the originator
  through each relay to this station, arrowheads pointing the way the message
  travelled. Direct exchanges stay with the orange links; an unmapped hop
  splits the chain rather than inventing a position for it.
- **Alerts have a place.** The station that originated an active alert (last
  15 minutes) pulses with a red ring, and a red chip above the map says what,
  who and how long ago. The geography of the emergency is one glance, not a
  callsign lookup.

## Verification

- All 341 automated tests pass.
- New coverage: chime WAVs are generated, valid and distinct; the player
  refuses the radio-as-default and unknown-default cases and honours the
  sound switch; startup seeding, once-only announcements, window-active and
  PTT suppression, priority routing to the emergency window, own and stale
  alerts staying quiet; the panel rows with and without positions; the relay
  chain drawn for relayed mail and not for direct; the alert ring and chip.
- Not yet proven in the field: the toast and chime on a real desktop session
  (automated tests exercise the decision layer, not the Windows tray itself).
