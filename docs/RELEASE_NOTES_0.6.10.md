# Guardian 0.6.10

This release targets the remaining VARA FM empty-BREAK loop observed after a
successful RF connection.

- Open command port 8300 and data port 8301 back-to-back, matching native VARA
  clients.
- Put an outbound station into `LISTEN OFF` before `CONNECT` and restore
  unattended listening after the session.
- Wait one second after `CONNECTED` before writing to port 8301, avoiding the
  native VARA data-port race during the first link turnaround.
- Keep the v0.6.9 `BUFFER` safeguard so an unaccepted payload is aborted
  cleanly instead of cycling indefinitely.
