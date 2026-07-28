# Guardian 0.6.23

- **A finished transfer no longer stays "active".** The OK7PS logs of
  2026-07-28 show message #270532637 delivered and filed, while the shell kept
  reporting "active transfers: 1". The initiator's session goes to `CONFIRMED`
  on a peer `RECEIVED` and waits for a separate end-to-end `DELIVERED` frame;
  OK2IPW sent it at 20:13:30, OK7PS never decoded it, and `CONFIRMED` is not a
  terminal state, so the session stayed open for the rest of the run. Where the
  station confirming receipt *is* the final destination — every direct route —
  `RECEIVED` already proves delivery and the session now ends there. For a
  relayed message the end-to-end receipt is genuinely outstanding, so it is
  still awaited, now under a 120-second bound instead of forever. Mailbox
  status is unchanged: `CONFIRMED` and `DELIVERED` both filed as delivered
  already, which is why the mail itself was always correct.
- **Double-click a message to read it in a window.** The same laid-out form
  used for writing, with From/To, subject, status, route and attachments in
  fields above a full-height body — easier on the eyes than the narrow panel
  under the list. Read-only throughout.
- **Attachments over 200 KB ask before queueing.** A plain OK/Cancel notice
  giving the size and roughly how many minutes of airtime it costs. It is a
  heads-up, not a limit — confirm and it goes.

The automated suite covers `RECEIVED` from the final destination closing the
session, a relayed confirmation ageing out instead of hanging, the reading
window being read-only, and large attachments prompting while small ones do
not.
