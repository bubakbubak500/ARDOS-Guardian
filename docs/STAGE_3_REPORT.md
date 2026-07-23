# Stage 3 report — PySide6 shell and Monitor design system

## Outcome

Guardian now starts with a native PySide6 Home shell using the same Monitor
design language as the neighboring Modeling Anten application. The established
CustomTkinter application remains available through **Open operational
console** and `Guardian.exe --legacy`; its radio, VARA P2P and Winlink behavior
is unchanged.

## Design system

- Semantic Light and Dark tokens derived from Modeling Anten:
  - application and panel surfaces;
  - subtle/structural borders;
  - primary, secondary, muted and inverse text;
  - accent, success, warning, danger, info and inactive states;
  - 4 px spacing base and 2–4 px radii.
- Theme preferences: Light, Dark and Follow system.
- Application-wide Qt palette and stylesheet.
- Native Windows UI font; fixed-width font is limited to the activity log.
- Status indicators combine a symbol and text, not color alone.

## Shell architecture

- Native File, View, Tools, Settings and Help menus.
- Compact station-context/operation header.
- One dominant action that opens the unchanged operational console.
- Mail/network metric strip sourced from immutable Stage 2 snapshots.
- Station-readiness workspace and bounded structured activity feed.
- Integration status strip for Radio, VARA, control channel and Hamlib.
- Persisted theme and window geometry through `QSettings`.

The new Home imports snapshots and the event bus. It does not import or
duplicate payload protocol logic.

## Verification

- 27 tests pass.
- Minimum logical window size: 1180×720.
- Windows renders captured at:
  - 1366×768 Light and Dark;
  - 1920×1080 Light and Dark;
  - 1180×720 with 125 %, 150 % and 200 % Qt scale factors.
- The 200 % capture retains all controls, table columns, primary action and
  status indicators without overlap.

Reference captures are stored in `docs/ui/stage3-windows/`.

## Migration boundary

Stage 4 will replace disabled Settings/Diagnostics placeholders with the new
first-run and dependency workflow. Stage 5 will migrate Mail, Network and Log.
Only after parity is proven will the legacy console be removed in Stage 7.
