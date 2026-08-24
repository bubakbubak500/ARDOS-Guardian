# Guardian 1.1.0 — efficient transfers without disturbing the control path

Guardian 1.1.0 ports the selected operator-facing transfer features from the
Guardian G2 development line into the stable VARA-based application. The radio,
routing and message protocols otherwise remain unchanged.

## Compression choices from G2

- **Native VARA FILES compression** is available in Settings and sends
  `COMPRESSION FILES` to the selected VARA modem instead of the normal TEXT
  mode.
- **Guardian BZIP2 compression** performs one measured, lossless pass over the
  standard message bundle. It uses the result only when it is smaller, so
  already compressed attachments retain the ordinary ZIP bundle.
- The two compression layers are mutually exclusive and may both remain off.
  This is the final G2 setup after its benchmark removed the slower external
  PAQ-family candidate search.

## Transfer visibility

- The segmented header meter continues to report bytes genuinely handed off by
  VARA on transmit.
- Incoming transfers now show the same panel from the beginning of reception,
  then count received wire bytes against the envelope size.
- Both directions show VARA's reported link bitrate when it is available.

## Optional trailing Morse identification

The final destination can append `SENDER DE RECEIVER` at 40 WPM after its
`RECEIVED` and `DELIVERED` control frames. The option is disabled by default and
uses the existing control-channel audio/PTT path.

## Mailbox/VARA concurrency fix

Deleting an older Inbox message no longer competes with a transfer callback for
the mailbox index. Mailbox access is serialized, the JSON index is replaced
atomically, and UI deletion runs outside the main control loop. This prevents a
brief local file operation from disrupting the processing of the control frames
that close a VARA delivery.

## Field stations

OK7PS / OK2IPW / OK6LZ / OK2MTV
