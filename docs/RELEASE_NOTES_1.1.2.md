# Guardian 1.1.2

Guardian 1.1.2 improves everyday station operation, mailbox management and urgent notifications. It includes issue #1 items 1, 2 and 4–9, plus the requested Home log filtering and acknowledgements update.

## Changes

- **Transfer context:** progress shows the original sender, final destination and immediate relay partner when known. Incoming transfers do not guess the original sender before message metadata arrives, and finished or failed transfers clear their context.
- **IC-705 GPS:** a one-shot GPS Out read on a separate USB(B) serial port previews a station locator. Guardian converts validated NMEA coordinates with its existing Maidenhead converter; accepting the preview updates only the local station locator. It does not automatically enable position sharing. The configured CAT port is excluded, and reads support cancellation and timeout.
- **Manual beacon:** Network / Heard stations includes a send-now action that works with automatic beacons disabled. It respects ongoing transfers, radio/control activity and position-sharing settings, and limits repeated requests. Queueing a beacon is not a reception acknowledgement.
- **Mailbox:** Ctrl/Shift multi-selection survives refresh and sorting. Bulk deletion confirms the selected count, runs in the background, protects active/preparing transfers and reports file failures. Refresh preserves the current message and reader selection without repeatedly unpacking attachments.
- **Mail dates and columns:** a sortable local timestamp follows Size; columns can be resized and scrolled horizontally. Received/transit mail uses local receipt time, Sent uses the first successful outbound handoff, and drafts/outbox use creation time. Unknown historical receipt/handoff times remain unknown. Headers distinguish From and To, with both endpoints shown for transit mail.
- **Logs:** important events are separated and highlighted in yellow; errors remain distinct. Normal VARA PTT/BUSY ON/OFF notifications are hidden only from Home activity and remain available in the full log and diagnostics. Filtering does not change PTT handling or counters. Home history survives raw notification churn and UI rebuilds without duplicate rows.
- **Urgent notifications:** acknowledgements work over modal dialogs, including a later dialog opened after the first emergency. Multiple urgent events queue instead of replacing one another. Escape, close, minimization and application shutdown have explicit lifecycle handling.
- **About:** added OK2MTV to the acknowledgements in both languages.

## IC-705 setup

Enable GPS Out to USB(B) on the radio and select its separate GPS COM port in the station map. The radio's USB(B) function must permit GPS output (OFF or DV Data); select DATA → USB(B) under GPS Out. Keep the CI-V/CAT port separate. A valid recent GPS fix is required; NMEA does not supply a verified accuracy estimate here.

## Compatibility and verification

- Radio frames and transferable mail bundles retain their existing format. Receipt/handoff timestamps are local mailbox-index metadata, and older mailboxes remain readable.
- The reviewed implementation passed 512 automated tests, including Qt modality, bulk-delete failures, compression concurrency, GPS parsing/cancellation and VARA notification/diagnostics checks. Release CI repeats the full suite and builds the Windows packages.
- These new changes have not yet been exercised in a physical IC-705/VARA session with a second station. Existing on-air validation of earlier versions is not a hardware validation of the new GPS and UI behavior.
- Alert-note diacritics and automatic VARA VOX configuration from issue #1 are not changed in this release.
