# Guardian legacy UI baseline

Baseline date: 2026-07-23

This document records the Stage 0 baseline before the planned PySide6
migration. It is evidence for comparison, not a claim that the current UI is
visually complete.

## Environment

- Repository revision at audit start: `3ca15b9`
- Application version: `0.1.0`
- UI toolkit: CustomTkinter
- Main window target geometry: 1040×780
- Main window minimum: 900×700
- Host scenario: Windows user profile with no system Python installed
- Automated characterization: 16 tests

The no-Python host is intentional evidence for the installer requirement:
production users need a frozen application and must not be asked to install
Python or run pip.

## Captured rendered states

These images were captured from the real running Guardian application with an
isolated APPDATA directory. No radio, VARA instance, control channel or PTT was
started.

- [Light — Home, not ready](ui/before/1040x780-light-home-not-ready.jpg)
- [Dark — Home, not ready](ui/before/1040x780-dark-home-not-ready.jpg)
- [Light — Station settings](ui/before/1040x780-light-settings-station.jpg)
- [Dark — Station settings](ui/before/1040x780-dark-settings-station.jpg)
- [Light — Advanced protocol test screen](ui/before/1040x780-light-settings-advanced-test-screen.jpg)

Observed in the rendered baseline:

- the permanent brand/sidebar, top operational tabs and nested settings tabs
  create three competing navigation levels;
- the setup checklist dominates the Home screen even after its purpose is
  understood;
- five Home actions have similar visual weight and weak state hierarchy;
- status is repeated in the sidebar, setup checklist and lower cards;
- settings use only a small part of the available workspace but remain inside
  the full operational shell;
- secondary and disabled text has low contrast in both themes;
- the Advanced screen is entirely a protocol development/self-test surface
  exposed as normal user settings;
- CustomTkinter exposes almost no useful accessibility names for the controls;
  the automation tree is predominantly anonymous panes and images.

## Static UI inventory

Main operational tabs:

- Home
- Mail
- Net
- Mesh
- Log
- Settings

Nested settings tabs:

- Station
- Radio
- VARA
- Channel
- Mesh
- Routing
- Advanced

Development/test controls currently exposed in the production surface:

- Bench Force SEND over VARA
- Bench Force RECEIVE
- Simulate receive (demo)
- Compose control burst
- Build burst
- Build and decode self-test
- Hamlib Dummy test rig

The modernization plan removes these controls from the normal user workflow.
PTT testing and raw monitors move to a dedicated Diagnostics surface.

## Structural findings

- `guardian/ui/main_window.py` contains 2,331 lines at baseline.
- One `GuardianApp` owns widget construction, persistence, radio and VARA
  control, network ticking, mailbox presentation, dependency installation,
  logging and tray behavior.
- The UI uses nested tab navigation rather than a native application menu and
  task-oriented settings dialog.
- Hamlib search can recreate as many as 300 CustomTkinter buttons for every
  key release.
- The log textbox has no retention limit.
- Some worker callbacks call UI logging without marshaling through the Tk event
  thread.
- Some radio, PTT, QSY and scanner operations can execute on the UI event
  thread.
- Importing configuration currently creates the Guardian APPDATA directory.
- Importing `guardian.ui` previously imported the complete GUI eagerly; Stage 0
  changed this to a compatible lazy import so non-GUI tooling remains isolated.

## Responsiveness measurement

Stage 0 adds an opt-in event-loop heartbeat. It is disabled during normal use.

Run from a development environment:

```powershell
$env:GUARDIAN_UI_PROFILE = "1"
.\run.ps1
```

Detected stalls of at least 100 ms are appended as JSON Lines to:

```text
%APPDATA%\Guardian\ui-performance.jsonl
```

Recommended manual reproduction:

1. Start with the control channel off and type continuously for 30 seconds in
   Station, Routing and Net fields.
2. Repeat with the audio control channel active.
3. Repeat while Hamlib radio search is filtering its full model list.
4. Repeat with at least 100 mailbox items, heard stations and session entries.
5. Record the count, maximum and percentile distribution of `stall_ms`.

Stage 2 should supplement heartbeat drift with named worker/callback timing.

The short baseline capture session recorded one event-loop stall of 447.7 ms.
It happened during UI navigation/theme interaction, so it establishes that a
visible stall is possible but does not yet identify its callback or reproduce
the reported continuous typing lag. The longer scenario matrix above is still
required.

## Required screenshot matrix

The visual baseline must eventually include real rendered captures for:

- Light and Dark themes;
- 900×700 minimum, 1040×780 default and 1366×768;
- Home not ready / ready;
- Mail empty / populated / selected message;
- Net idle / active session;
- Mesh empty / populated;
- Settings Station / Radio / VARA / Routing;
- a dependency error and a connection error;
- long callsign, model and route values;
- Windows scaling 100%, 125%, 150% and 200% where available.

No screenshot is accepted unless it comes from the running application.
Static source inspection or a fabricated mockup does not count as a baseline.
