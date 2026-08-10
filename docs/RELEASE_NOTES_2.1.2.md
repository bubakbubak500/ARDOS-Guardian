# Guardian G2 2.1.2 — mode-aware transfer status

This maintenance release makes the live payload status match the transport
negotiated for each hop:

- OFDM sessions now log **starting OFDM VHF** and
  **receiving payload over OFDM VHF** instead of referring to VARA.
- VARA sessions and per-peer VARA fallback continue to use the existing VARA
  startup and receive messages.
- The message transfer bar is now visible on both the sending and receiving
  station in VARA and OFDM modes. It shows a live transfer-speed line in every
  case; OFDM also shows FEC, burst size, ARQ block size, retries, retransmitted
  bytes, and the first-pass result.
- VARA receive progress is counted against the complete wire size learned from
  the incoming message header. OFDM follows acknowledged sender or receiver
  bytes and reports measured goodput.
- VARA keeps its existing BUFFER-based progress calculation. An old OFDM
  status can no longer replace or hide the VARA bar when an OFDM-configured
  station negotiates VARA fallback with a peer.
- Slow-keying status uses the active modem name, so an OFDM transfer no longer
  produces VARA-specific wording.

The payload negotiation and on-air formats are unchanged.
