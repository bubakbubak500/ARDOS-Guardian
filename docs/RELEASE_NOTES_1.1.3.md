# Guardian 1.1.3

Guardian 1.1.3 brings the SC-FTN modem and image-aware XZ compression from Guardian G2 into Guardian G1, alongside the existing VARA transport. It also simplifies station settings and explains automatic operation in the Czech and English help.

## Modem and Auto Tune

- SC-FTN is the only G2 waveform included. Available bandwidth profiles are 1K2, 2K7, 4K5, 5K, 10K and 20K; each selects the corresponding modem geometry and operating limits.
- Automatic MCS selection, FEC/LDPC protection, ARQ retransmissions, adaptive blocks and bursts, soft combining, receiver feedback and recovery operate together. Operators do not switch this policy off. Radio-specific limits, timing and the 2K7 clock-tracking path are retained.
- Guardian UART/AIOC radio support and the relevant handheld profiles accompany the modem. VARA remains available alongside SC-FTN, with per-hop transport negotiation.
- Paired Auto Tune includes bidirectional level measurements, peer consent, progress and cancellation, protocol retries, hardware-specific calibration storage and audio-gain recovery. Its page remains accessible in VARA mode, with its action buttons disabled until SC-FTN is selected.
- SC-FTN status is under Diagnostics. Settings show waveform, bandwidth and policy details only for the Guardian modem.

## Compression and automatic network operation

- The single Guardian compression option uses XZ/LZMA2. JPEG attachments can be repacked as lossless JPEG XL and reconstructed to the exact original JPEG bytes; PNG optimization preserves image pixels. Compression runs in a bounded worker and falls back when conversion fails or provides no useful reduction. Legacy VARA FILES/BZIP2 choices migrate to the current option.
- Guardian controls radio keying, automatic route selection, relaying, delivery on hearing a station, automatic retuning, forwarding bounded discovery requests and immediate use of discovered routes. These fixed policies are no longer redundant checkboxes.
- Route discovery has Off/On modes. LINK_ADVERT topology exchange is enabled by policy and follows discovery/control-channel availability, transmission budgets and expiry rules. Reciprocal observations provide live links; RREQ/RREP discovers routes when needed. Presence beacons and separate working channels retain their own operator settings.
- Help now describes these dependencies, LINK_ADVERT versus route discovery, SC-FTN operation, MCS, FEC, LDPC, ARQ and Auto Tune. Search supports multiple words, Czech text without diacritics and the common “MSC” spelling when looking for MCS.

## Everyday operation

- Startup displays the approved white shield animation, formed from ASCII and filled into the logo, at the faster preview timing.
- The map offers GPS loading only for a selected, connected IC-705. It resolves an identifiable GPS USB(B) port separately from CAT/PTT and rejects ambiguous ports; the separate map port selector is removed.
- Redundant Operations menu commands are removed; their main-window controls remain available. G1 mailbox, receipt tracking, urgent notifications and manual beacon functionality are retained.

## Compatibility and verification

- Use current Guardian versions at both ends for the new compressed payload format. Existing local mailboxes remain readable. Other G2 waveforms and Companion features are not included.
- The final local automated suite passed 649 tests, including G2 reference parity probes, modem/session integration, compression, configuration migration, routing and Qt behavior. Clean CI skips the 11 optional comparisons that require the separate G2 reference snapshot; these passed locally. Release CI repeats the checkout-independent suite and verifies the frozen application's Qt imports and real JPEG XL/PNG/XZ round trip before publishing.
- Windows packages include pinned, SHA-256-verified libjxl tools and their license notices. The release includes the installer, portable ZIP, update manifest and SHA-256 checksums.
- Physical two-station RF throughput and the new radio/GPS paths have not been revalidated on hardware as part of these automated release checks.
