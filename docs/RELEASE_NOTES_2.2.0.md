# Guardian G2 2.2.0 — compression, VARA AES and final Morse ID

This feature release adds the complete payload-efficiency and identification
controls requested for G2:

- VARA continues to start in its existing `COMPRESSION TEXT` mode. A new,
  disabled-by-default setting selects VARA's native `COMPRESSION FILES` mode
  for Guardian message bundles.
- A new adaptive Guardian compressor works above both VARA and OFDM. It compares
  stored ZIP, DEFLATE, BZIP2 and LZMA representations, its own mixed-entry
  strategy, and bundled ZPAQ 7.15, PAQ8PX v187 and LPAQ8 candidates. High-ratio
  candidates run off the UI thread and must pass a bounded decode and SHA-256
  round-trip before selection. Their versioned `GCP1` envelope is negotiated
  per hop using an active receiver capability bit; a legacy peer gets the
  standard ZIP fallback before transfer. No LLM or cloud service is used.
- VARA FILES and Guardian compression cannot be enabled together, preventing a
  counterproductive second compression pass.
- VARA's AES-256 fixed-key option can be configured from Guardian for authorised
  non-amateur/commercial operation. The setting updates VARA's own INI keys and
  fails closed if encryption is enabled without a valid 1–32 character key.
- A new disabled-by-default option lets only the final receiving station send
  `SENDER DE RECEIVER` in Morse at 50 WPM. It waits for the queued `RECEIVED`
  and `DELIVERED` frames, keys once more, sends the identifier and releases PTT.
- The settings have a dedicated bilingual **Compression & identification** page
  and remain disabled in old profiles unless the operator opts in.

The existing VARA P2P and OFDM payload framing remains compatible. VARA's three
native compression commands are mutually exclusive modes, so FILES replaces
TEXT while selected rather than running beside it.

The exact ZPAQ/LPAQ upstream archives, the published PAQ8PX v187 binary with
complete corresponding source, and licence notices are shipped under
`guardian/codecs/vendor`. PAQ8PX level 1 uses about 417 MB; candidates run
sequentially off the UI thread so their memory peaks do not stack. Every result
must finish its own timed verification decode before it may win.
