# Guardian 0.6.47

This release completes the software side of the P0 operational backlog:
production channel scanning, modem-compatible alert sweeps and evidence-rich
capture of rejected control candidates.

## Operational channel scanner

- The Network workspace now has an explicit **Channel scanner** page with
  Start/Stop, dwell, optional Hamlib S-meter hold threshold, current channel and
  live Scanning/Holding/Paused state.
- The current radio frequency is the home channel. Distinct compatible route
  frequencies form the rest of the frozen scan plan; stopping returns frequency
  and mode home.
- CAT tuning runs through the worker pool under the shared radio lock, never on
  the Qt event loop.
- A decoded control frame or the configured S-meter threshold restarts the full
  dwell. Incoming sessions and payload ownership pause scanning automatically.
- No-CAT/Dummy radios cannot start a simulated scan. Outbound mail, alerts and
  PTT tests require the operator to stop scanning first, preventing an
  unsynchronised transmission on whichever channel happened to be current.

## Correct modem on every channel

Retuning does not replace the live audio control modem. Scanner plans and alert
sweeps therefore include only FM-family channels for AFSK-1200 and SSB/data
channels for MFSK-16. Guardian no longer reports a locally successful FM-to-HF
sweep while transmitting an unusable waveform on the destination channel.

## Rejected-control evidence

An invalid demodulation candidate is now classified before it can reach the
orchestrator. Guardian records its reason, modem, S/N, payload length and hex
bytes in `last-bad-control.json` beside the existing diagnostic WAV. The data is
included in Diagnostics, while valid frames continue through the normal queue.
This prepares the occasional on-air `bad magic` observation for a
capture-driven fix instead of a speculative modem change.

## Verification

All **299 automated tests pass**. Scanner CAT movement still needs a real-radio
field pass; alert sweep timing/copy counts and the next rejected-control capture
remain explicitly marked as awaiting on-air verification.
