# Stage 0 report — baseline and functional safeguards

Completed: 2026-07-23

## Outcome

Stage 0 establishes a reproducible baseline before installer work and the
presentation-layer migration. No ARDOS protocol, routing, modem, VARA P2P or
Winlink Manual behavior was intentionally changed.

## Added safeguards

- Standard Python project metadata in `pyproject.toml`.
- One application version source in `guardian/_version.py`.
- Editable development installation with a `dev` dependency group.
- Windows GitHub Actions test workflow.
- Isolated test APPDATA so tests never use the operator's real Guardian data.
- 16 automated characterization tests covering:
  - configuration persistence and VARA mode ports;
  - mail bundle serialization and mailbox persistence;
  - route normalization, replacement and persistence;
  - direct control-session delivery and no-route behavior;
  - VARA payload envelope, send and receive contracts;
  - Winlink hand-off callback and resource order;
  - UI heartbeat stall recording.
- Lazy `guardian.ui` import so non-GUI tools do not load CustomTkinter.
- Opt-in UI responsiveness heartbeat controlled by
  `GUARDIAN_UI_PROFILE=1`.

## Verification

```text
16 passed
pip check: no broken requirements
version: 0.1.0
git diff --check: clean
```

The application was also launched from the new editable environment and its
real rendered UI was captured without starting radio, PTT, VARA or the audio
control channel.

## Baseline findings now backed by evidence

- A normal host may have no installed Python.
- Importing configuration creates the APPDATA directory.
- The legacy UI has three competing navigation levels.
- The permanent setup checklist and repeated status consume much of Home.
- The Advanced settings page is a protocol self-test screen.
- CustomTkinter exposes a very weak accessibility tree.
- The short capture session recorded one 447.7 ms event-loop stall.
- The exact callback responsible for reported typing lag remains unproven;
  Stage 2 needs named callback/worker timing after the longer reproduction
  matrix.

## Deferred by design

- PyInstaller spec cleanup and Inno Setup belong to Stage 1.
- Moving blocking hardware operations and introducing service snapshots belong
  to Stage 2.
- Full visual-state population requires deterministic UI fixtures; the initial
  rendered baseline covers Home and settings in Light and Dark plus the
  exposed Advanced test screen.
- No release/update channel was added in Stage 0; only test CI exists.

## Gate for Stage 1

Stage 1 changes are acceptable only while:

- the characterization suite remains green;
- `guardian._version.__version__` remains the single version source;
- a clean install never requires system Python;
- user data stays outside the installation directory;
- no installer action silently keys PTT, starts a radio connection or launches
  VARA.
