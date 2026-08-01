# Guardian 0.6.51

Named radio profiles on the Settings → Radio control page.

## Save the radio page under a short name

- **Save profile…** sits beside **Test PTT** and stores the radio settings as
  the fields currently read them, under a short name the operator types. The
  picker next to it loads a saved profile back into those fields, and
  **Delete** removes the selected one.
- A station used with more than one rig or cable had to re-enter nine fields
  from memory on every swap: control method, model, CAT/PTT port, baud,
  rigctld host, port and executable, PTT method and line, and the VARA FM
  keying delay. That list is exactly what a profile carries.
- **The radio page only.** No callsign, audio device, VARA port or network
  setting is stored in a profile or changed by loading one — swapping radios
  must not quietly move anything else about the station.
- Loading a profile fills the fields and nothing more. The radio is reached
  only when the operator presses Save or Apply, as with any other change on
  the page.
- Saving under an existing name replaces it. Profiles are written to
  `config.json` the moment they are named, so a profile does not depend on the
  operator also pressing Save on the way out.
- Names are trimmed to a single line and 24 characters.
- A profile saved by an older build cannot blank a field it never knew about:
  only the keys a profile actually carries are written back.

## Verification

- All 330 automated tests pass.
- New coverage: save two profiles from the page and restore each by the
  picker; a loaded profile reaches the config only through Apply; callsign and
  audio survive a profile swap; an empty or cancelled name saves nothing;
  profiles persist through save/load; a damaged profile block is dropped on
  load; a partial profile leaves unknown fields alone.
- The Apply path and the profile now read the radio page through the same
  function, so what is stored under a name and what reaches the station cannot
  drift apart.
