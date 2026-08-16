# Guardian G2 2.3.5 — faster field workflow

This build focuses on the radio workflow operators use repeatedly in the field.

- **Modem test is file-focused.** The lower area now contains only **Files**.
  Live recording moved there, uses the waveform selected in Modem test, and is
  decoded automatically after it stops.
- **Shorter modulation settings.** The page hides settings that do not apply to
  the selected Guardian G2 waveform and collapses inactive fixed/adaptive FEC,
  burst and train branches.
- **Immediate return to the control channel.** A final OFDM ACK confirmation
  ends the receiver's loss-protection hold immediately on a healthy link. The
  old re-ACK fallback remains available when that confirmation is lost.
- **Radio AutoTune responds reliably** to tuning requests even when the radio
  tuning screen is not open.
- **Cleaner navigation.** Modem test is listed only under View, and the live
  topology page no longer carries an experimental label.
- **Simpler compression.** Guardian compression uses one measured ZIP/BZIP2
  path with a standard DEFLATE fallback when BZIP2 would grow the payload.
  VARA FILES remains an independent, mutually exclusive alternative.

The complete automated suite passes before packaging. Compression measurements
and their sample construction are recorded in
[G2_COMPRESSION_BENCHMARK_2026-08-15.md](G2_COMPRESSION_BENCHMARK_2026-08-15.md).
