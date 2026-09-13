# Guardian 1.1.8

Improve operation on small or scaled displays, prefer known routes before active discovery, and complete the mailbox and map usability fixes.

## Windows and startup

- Fit large dialogs to the monitor work area, accounting for display scaling and window decorations. Scroll long Settings and Network pages while keeping confirmation controls accessible (issues #2 and #7).
- Adapt the main window, mail dialogs, map, diagnostics and other large windows to smaller screens.
- Keep the startup logo centered as its parent moves or changes size, and bounded by the current screen.
- Correct screen-tracker lifetime handling when transient windows are closed and collected.

## Routing and relay reliability

- Use fresh reciprocal LINK_ADVERT routes without a preceding RREQ/RREP exchange. Preserve explicit/manual route priority and respect the advertised evidence lifetime (30 minutes by default).
- After bounded retries, try configured backup and other known alternatives before recovery discovery. Cancel obsolete queued RREQ frames when a known route becomes available.
- Lower a failed live route's ranking without removing automatic approval; preserve failure history when rebuilding topology. Neither failure nor success extends advertised evidence expiration.
- Retain alternatives through different first hops and prevent immediate relay loops back to the previous hop.
- Ignore late control frames from abandoned hops. Repeat custody receipts after a lost RECEIVED frame without restarting an active outbound relay transfer.

## Beacons

- Randomize each automatic beacon interval by +/-5 seconds (60 seconds becomes 55-65), preserve the 15-second minimum gap, and spread startup over 0-5 seconds.
- Defer beacons during recognised incoming control activity and existing busy/session conditions. Retry after a random 1-5 seconds without accumulating missed beacons.
- Busy detection covers recognised control activity; it is not a general voice or arbitrary-carrier detector.

## Mail and map

- Delete removes one or multiple selected messages through the existing confirmation workflow; the shortcut is scoped to the message list (issue #8).
- Add Forward beside Reply: create a new message quoting the original sender, recipient, subject and body, with all attachments copied. The operator supplies the new recipient (issue #5).
- Refresh header mailbox counts from the same current store used by the mail folders.
- Add pinch/native zoom and pixel-based touchpad wheel support to the map, keeping the gesture position anchored. Provide + and - buttons as a fallback (issue #4).

## Verification

The combined source regression suite passed all 755 tests. Checks cover routing recovery and relays, beacon scheduling, mailbox actions and counters, map gestures, and window lifecycle handling.

The Windows build environment also passed all 755 tests. The packaged EXE passed its Qt 6.11.2/WinRT and JPEG XL, ZopfliPNG and XZ compression self-tests.

Offscreen rendering and geometry checks covered ten dialogs and the main window at 1366x768 with 100%, 125% and 150% scaling, 1920x1080 at 150%, and 2560x1440 at 100%. Compact mail actions and map controls were also visually reviewed.

Physical touchpad, multi-monitor and on-air verification remain pending. Simulated routing transfers and gesture events do not establish RF reliability or behavior of every touchpad driver.

The VARA progress-watchdog corrections from 1.1.7 remain included.
