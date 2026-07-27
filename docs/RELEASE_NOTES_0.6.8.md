# Guardian 0.6.8

This release fixes CAT/PTT timing during VARA FM transfers.

- Consume complete multi-line `rigctld` replies so a mode passband or `RPRT`
  line cannot be mistaken for the response to the next CAT/PTT command.
- Pause periodic frequency, mode, PTT, and signal polling while VARA owns the
  radio, keeping host-PTT edges within VARA's timing window.
- Serialize host PTT with the remaining radio operations.
- Configure VARA for binary file compression and a known initial LISTEN state.
- Abort a stalled payload session after 45 seconds instead of allowing an
  extended PTT/ARQ cycle.
- Include data-port byte counters and clearer sender/receiver stages in
  diagnostics.
