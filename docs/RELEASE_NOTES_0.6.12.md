# Guardian 0.6.12

This release fixes the VARA TCP-session regression introduced in 0.6.11.

- Renew command port 8300 and data port 8301 together because VARA treats them
  as one application session and closes 8300 when 8301 is closed.
- Restore `PUBLIC ON`, `COMPRESSION OFF`, and `MYCALL` on the fresh command
  connection before selecting inbound or outbound mode.
- Keep obsolete command-reader threads from tearing down the replacement pair.
- Retain the 1024-byte transport block and `BUFFER` safeguard for small
  payloads.
- Update the transfer log to report renewal of the complete 8300/8301 pair.

The complete-pair handoff was exercised repeatedly against a local VARA FM
instance without starting an RF connection or keying PTT.
