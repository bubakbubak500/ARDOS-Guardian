# Stage 2 report — application services and safe UI workers

## Scope

Stage 2 introduces the UI-independent runtime boundary needed by both the
current CustomTkinter interface and the planned PySide6 interface. It does not
change the VARA P2P or Winlink payload protocols.

## Implemented

- `guardian.services.events`
  - thread-safe structured log events;
  - bounded 2,000-event history;
  - pending-event queue drained by the UI thread;
  - source and severity fields for future filtering and diagnostics.
- `guardian.services.snapshots`
  - immutable Radio, VARA, Mailbox, Network and Dependency snapshots;
  - atomic snapshot replacement with a monotonically increasing revision.
- `guardian.services.workers`
  - bounded named worker pool;
  - duplicate-operation protection;
  - result/error delivery performed only when the UI drains completions.
- Legacy UI integration
  - radio connect/disconnect and `rigctld` startup no longer block the UI;
  - VARA connect/disconnect no longer block the UI;
  - Hamlib download/install and model discovery use the shared worker pool;
  - worker and VARA reader threads never write directly to Tk widgets;
  - radio polling is serialized with radio connection operations;
  - the visible log is bounded as well as its structured history;
  - the status poll publishes Radio, VARA, Mailbox and Network snapshots.

## Verification

- 24 tests pass, including new service concurrency and snapshot tests.
- Python compilation succeeds for `guardian` and `tests`.
- The Stage 0 characterization tests for config, routing, mailbox, sessions and
  both payload backends remain unchanged and green.

## Deliberately unchanged

- VARA P2P payload framing and transfer behavior.
- Winlink manual hand-off behavior.
- Session state-machine and routing decisions.
- Audio modem and PTT timing.

Some hardware-critical callbacks (PTT, QSY, codec hand-off) remain owned by the
existing orchestrator. Moving those independently would change timing and must
be done only with hardware-backed tests. The new service boundary makes that a
contained later migration rather than a UI rewrite prerequisite.

## Gate for Stage 3

The PySide6 shell may read snapshots, publish commands through workers, and
consume the event bus. It must not import or duplicate VARA P2P/Winlink
protocol logic.
