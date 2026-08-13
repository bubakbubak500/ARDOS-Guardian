# Guardian G2 2.3.4 — offline phone companion

This release adds a completely local iOS/Android companion. Guardian itself is
the server. A phone talks directly to the Windows station over a shared Wi-Fi
network or a Guardian-created Wi-Fi Direct network; no account, Internet
connection, cloud relay or external API participates.

## Start and pair

Open **Tools → Offline phone companion** (`Ctrl+Shift+P`), then:

1. Start the companion server.
2. Optionally start **Guardian offline Wi-Fi**. Guardian asks Windows for a
   Wi-Fi Direct legacy access point with a fresh random WPA2 password. If the
   adapter cannot host one, Mobile Hotspot settings open as the supported
   fallback.
3. Scan the Wi-Fi QR, then the companion QR.
4. On iOS, use Safari's **Add to Home Screen**; on Android use Chrome's
   **Add to Home screen**.

The pairing secret carries 192 bits of entropy, expires after five minutes and
is invalidated by its first successful use. The resulting session is carried in
an HttpOnly, SameSite cookie, expires after twelve idle hours, and can be
revoked immediately from the desktop. Restarting the server drops every
session and disarms remote RF transmission.

## Mobile station view

The responsive phone UI includes:

- Inbox, unread badges and message details;
- live activity and radio/control-channel status;
- station link latency and loss indication;
- routine, priority and urgent messages queued to the desktop Outbox;
- a dark Field Watch mode with audio, vibration and link-loss warning;
- optional local notification, app badge and screen wake lock where the
  browser's secure-context rules allow them.

The local server uses a long-held state request rather than rapid polling. This
keeps the link observable while avoiding continuous traffic and battery churn.
Every mailbox mutation and RF action crosses a command queue back to Guardian's
Qt thread; HTTP handler threads never operate the radio or message store.

## Emergency send is deliberately narrow

The phone may key the radio only when all of these are true:

- the message priority is **Emergency**;
- the operator armed immediate emergency RF transmission in the desktop
  companion dialog for the current server session;
- the phone confirms immediate transmission;
- Guardian's normal control channel and radio safety checks accept the send.

Routine remote transmission is rejected by the server. It may be queued, but
the station operator sends it through the normal workflow. RF authority is
never persisted to `config.json`.

## Two additional field tools

The companion also introduces two facilities that do not transmit over radio:

- **Return/check-in timer.** Start a 5–180 minute timer before leaving the
  vehicle. The phone counts down and both ends record an overdue check-in.
- **Field notebook.** Up to 50 short observations are stored locally in the
  Guardian profile and synchronized to every paired phone. Notes are separate
  from mail and can never be transmitted accidentally.

An additional **Ping operator at station** action writes a high-visibility
warning to Guardian's activity log.

## Platform boundary

The first 2.3.4 transport is local HTTP. It works in Safari and Chromium, but
iOS suspends a background web app and secure-only browser APIs such as Wake
Lock are unavailable over an untrusted HTTP origin. Consequently this release
does not claim a locked-screen offline iPhone push notification. Field Watch
must remain visible; the UI says so explicitly. No APNs, FCM, Web Push or relay
server is contacted.

## Verification

The companion has focused tests for one-use pairing and revocation, HTTP
authorization, ordinary queueing, emergency authorization, notes, check-in and
Wi-Fi credentials. The existing full Guardian suite is also run before the
Windows package is built.

The automated suite verifies the local HTTP and application integration paths.
Wi-Fi Direct driver compatibility, browser installation/suspend behavior and
the complete frozen-application journey remain hardware/device acceptance
tests; this release does not present them as universally verified.

The 2.3.3 capacity modem is otherwise unchanged. Its modern LDPC, adaptive
train/MCS and protocol-v3 modes remain experimental until the staged two-radio
procedure in `docs/G2_MODEM.md` has been completed.
