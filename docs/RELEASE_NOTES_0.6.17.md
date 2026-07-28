# Guardian 0.6.17

This release fixes message-list selection. Clicking a row in the mailbox left
the row unmarked and drew a focus rectangle around every cell instead, so the
list looked like a grid of editable boxes with a cursor in each one.

- Mark the whole row. The mailbox, routes, and heard-stations tables now share
  a single read-only row table: one row selected at a time, no cell grid, and
  no per-cell focus rectangle.
- Keep the row marked. Opening a message marks it read and rebuilds the list,
  which silently dropped the selection and left only the focus outline behind.
  The list now restores the selected message after every rebuild, so the
  highlight stays on the row the operator clicked.
- Style tables from the theme. `QTableView` had no stylesheet rule at all and
  fell back to the platform default, which is where the grid lines and the
  focus outline came from. The selected row now uses the theme's selection
  colour and keeps it when focus moves to the reader pane.
- Stop silent edits of the routing table. Route cells were editable by
  double-click and the typed value was discarded on the next refresh; the table
  is now read-only, and routes are still changed through the form below it.
- Switching mailbox folders now starts from a clean list, with the reader
  cleared and Reply/Send disabled until a message is selected.

The automated suite covers row-selection behaviour, selection surviving a
refresh, folder switching clearing the selection, and the network tables being
read-only row selectors.
