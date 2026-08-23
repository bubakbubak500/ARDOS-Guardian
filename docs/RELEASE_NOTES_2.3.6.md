# Guardian G2 2.3.6 - bounded OFDM recovery

This build follows the 2.3.5 AutoTune build with focused OFDM transfer fixes.

- **Short ACK capture.** A false squelch trigger while waiting for an OFDM ACK
  can no longer inherit the maximum DATA-burst capture time. The capture is
  bounded from the encoded ACK size, so POLL and selective retry run promptly.
- **Safe radio turnaround.** A short real-radio direction guard separates a
  received ACK from the next DATA train or final ACK confirmation.
- **Control off stops payload.** Switching station control off now cancels all
  live sessions, stops the active OFDM audio pipe and prevents the old worker
  from reopening control while it unwinds.
- **Terminal sessions stay terminal.** A late payload callback after cancellation
  cannot revive or complete the cancelled session.

The OFDM link simulations and the payload, session and operations regression
suites pass before packaging. On-air validation should begin with Quick Tune,
then repeat the approximately 30 kB attachment transfer that exposed the stall.
