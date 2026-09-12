# Guardian 1.1.6 RF validation

Test date: 13 September 2026, Europe/Prague. Two physical radios on one Windows PC: IC-705 / OK7PS and AIOC-connected radio / OK2IPW, simplex 144.525 MHz FM. Each Guardian used a separate station profile, radio connection and audio pair. VARA FM 4.4.0 used command/data ports 8300/8301 and 8310/8311. Audio levels were unchanged.

## Control-channel regression

The AIOC station heard a single over-air beacon representing the fictional station ZZ0TST, in addition to the real OK7PS station. Its normal `advertise_live_links()` action sent both neighbors in the same advertisement series. The IC-705 station ran normal reception and automatic forwarding.

| Series ID | OK7PS decoded link to OK7PS | OK7PS decoded link to ZZ0TST | First forwarded frame completed |
| --- | --- | --- | --- |
| 1864630276 | 00:31:35.729 | 00:31:37.963 | 00:31:40.614 |
| 1864630277 | 00:32:04.913 | 00:32:06.770 | 00:32:09.456 |
| 1864630278 | 00:32:30.253 | 00:32:32.489 | 00:32:35.112 |

All three series delivered both links. TX log timestamps mark completion, not the exact key-down edge. The waveform regression additionally verifies that the receiving station does not key its relay before the second received burst ends. That same test fails with the unchanged 1.1.5 audio transport.

## VARA FM automatic first hop

A single simulated reciprocal link announcement, carried over RF, represented the absent ZZ0TST station's observation of OK2IPW. OK7PS then derived the two-hop route with source `link-advert`, next hop OK2IPW and automatic approval. OK7PS had no manual route and did not directly hear ZZ0TST.

Message 270532609 was announced at 00:33:36, received ACK_HAVE at 00:33:40 and entered VARA at 00:33:43. VARA accepted 531 bundle bytes / 545 wire bytes at 00:33:46; OK2IPW received the bundle at 00:33:57. The native compressed FILES queue reported 440 bytes and subsequently zero. Both TCP counters agreed on 545 wire bytes. The receiver's bundle entries, manifest and body matched the sender. OK2IPW confirmed RECEIVED and stored the message for onward relay.

This verifies the real OK7PS-to-OK2IPW first hop, including automatic route selection and payload startup. ZZ0TST is fictional, so there was no end-to-end delivery through three radios. Further attempts toward that target were stopped after first-hop confirmation; the historical forwarded state and the receiver's stored transit message are the evidence.

## Guardian SC-FTN direct delivery

Separate clean profiles selected the internal `ofdm_vhf` transport with the `sc_ftn` waveform and 2K7 profile. No Guardian connected to VARA TCP. Negotiation selected G1T2 / SC_FTN_2K7, MCS1 and LDPC-1/2; the sender transmitted 510 bundle bytes in two ARQ blocks at 00:36:57. Both blocks were acknowledged at the first payload burst.

The initial control request and final receipt required automatic retries. The receiver stored the complete message at 00:37:09; the sender received RECEIVED at 00:37:23 and DELIVERED at 00:37:25. Message 270532609 in these separate profiles was in the receiver's inbox and the sender's sent/delivered folder. Bundle contents matched. Actual session transport and SC_FTN events established the modem used; both VARA byte counters remained zero throughout, with command/data connections absent.

## Build and limits

- All 711 automated tests passed, including cancellation, stop/restart, audio cleanup, two-advertisement waveform reception and existing G2 parity checks.
- The frozen Windows application passed Qt, WinRT and JPEG XL, PNG and XZ compression self-tests.
- RF tests used the final 1.1.6 source in the ordinary Guardian application with an isolated request harness invoking the same operations as the UI. Frozen-build verification was separate.
- The original CONNECTED/BREAK-only/no-payload failure reported on another PC was not reproduced here. Earlier physical tests of 1.1.1, 1.1.2 and 1.1.5 on this machine also transferred messages. This release fixes the demonstrated control-channel collision; it does not prove the cause of the other machine's VARA failure.
- This is a small strong-signal two-radio test, not a reliability measurement across weak-signal channels or a full three-radio relay chain. Radios were left idle after testing.
