# Guardian 1.1.6

Guardian 1.1.6 addresses a control-channel scheduling failure observed between an IC-705 and an AIOC-connected radio: a station forwarding the first neighbor advertisement could stop receiving while its peer was still sending the next advertisement. A missing link then prevented automatic route discovery from reaching the payload transfer.

This release builds on the 1.1.5 feature set. The repository history retains the temporary rollback to 1.1.2 and the subsequent restoration; existing release tags are unchanged.

## Control-channel correction

- Keep receiving while a queued transmission is waiting, being modulated, or opening its audio output. Mute reception only for the actual local transmission.
- Recognize an incoming AFSK preamble before the complete frame is decoded. Wait for its bounded frame and radio turnaround time, and check again immediately before keying PTT. A second neighbor advertisement extends that wait.
- Preserve the existing beacon preamble, PTT timings and relay jitter. There is no blanket increase in the spacing between all transmissions.
- Cancel already admitted, waiting requests when their message is cancelled or the control channel is stopped. A quick restart cannot transmit an old request; receipts and other messages are retained.
- Stop and close the audio output on cancellation and keying failures, while continuing to report failures to drain actual playback.

The VARA TCP client, data framing and payload write path are unchanged from 1.1.5.

## Verification scope

- All 711 automated tests passed. The two-advertisement regression fails against the unchanged 1.1.5 audio transport and passes with this correction.
- The locally frozen Windows build passed Qt, WinRT and JPEG XL/PNG/XZ compression checks.
- Physical RF tests on 13 September 2026 used an IC-705 (OK7PS) and an AIOC-connected radio (OK2IPW), both at 144.525 MHz FM. Three normal two-neighbor advertisement series were received completely with automatic forwarding enabled.
- VARA FM automatically transferred the first hop toward a simulated third station: 531 bundle bytes / 545 wire bytes, matching sender and receiver contents, native queue drain and a received acknowledgment. The route was learned from RF link advertisements without a manual route or approval on the sending station.
- Guardian SC-FTN 2K7 delivered one message from OK7PS to OK2IPW: 510 bundle bytes, matching content and confirmed delivery. VARA TCP remained disconnected with zero transferred bytes. Control requests and the final receipt needed automatic retries; the test establishes successful recovery and delivery, not a retry-free exchange.

See [the RF validation report](RF_VALIDATION_1.1.6.md) for test details and limits. Radio tests ran the release source through the normal application operations; the packaged build was checked separately as described above.

The previously reported failure on another PC, where native VARA shows CONNECTED but no payload arrives, has not been reproduced on this test machine. This control-channel correction does not establish the cause of that separate failure. Physical tests involving a simulated third callsign validate only the real first hop, not end-to-end delivery through three radios.
