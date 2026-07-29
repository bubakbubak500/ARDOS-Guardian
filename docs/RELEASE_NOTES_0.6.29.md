# Guardian 0.6.29

## The HF control modem was six times too wide

MFSK-16 never decoded a single frame on air. Orthogonal MFSK ties three things
together — `spacing = baud = sample_rate / n_per_symbol` — and
`n_per_symbol` was a fixed 256. That is the designed 31.25 Hz at the 8 kHz
default, but the sound card runs at **48 kHz**, where it becomes **187.5 Hz**:
the 16 tones spread across 600–3412 Hz instead of 600–1069 Hz, and the top
three land outside any SSB filter. The preamble alternates tone 0 and tone 15,
so half of it never arrived either.

Confirmed from the failed-audio capture rather than inferred. Tones measured at
**593, 994, 1141–1170 and 2281 Hz** — a 187.5 Hz grid — with energy above
2900 Hz at 4616 against 167898 in the 1100–2900 Hz band. That is the top of the
signal being cut off, and it explains the log exactly: frames detected, sync
erratic (`buffer too short`, `bad magic`), and every one failing CRC, several
with a CRC field of `0x0000` where the tail decoded as silence.

`n_per_symbol` now follows the sample rate, so the on-air geometry is fixed at
31.25 Hz spacing and 600–1069 Hz occupied whatever the device does. AFSK on FM
was never affected: its tones are absolute frequencies.

## Session timeouts now follow the modem

Fixing the geometry makes MFSK as slow as it was always meant to be: a 34-byte
`HAVE_MSG` is **5.15 s** on air against 0.91 s for AFSK. Announce plus reply
cannot fit the fixed 8-second ACK budget, so the handshake would have kept
failing for a second reason.

Both modems gained an exact `airtime(payload_bytes)`, and `ack_timeout` /
`start_timeout` are now per-orchestrator, scaled from the modem in use: **8 s
unchanged on FM, ~17 s on HF**. A transport with no modem keeps the defaults.

## Why it shipped

There was **no test of the MFSK modem at all** — none at any sample rate. The
new `tests/test_mfsk.py` covers geometry across five sample rates, every tone
sitting inside an SSB passband, round trips at the real 48 kHz device rate
(clean and noisy), and `airtime()` matching the transmitted length exactly.
Three of them fail against the old code, which was verified by reverting.
