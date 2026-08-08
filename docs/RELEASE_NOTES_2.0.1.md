# Guardian G2 2.0.1 — a modem of Guardian's own

This is the first release on the private **G2** development line. It adds an
experimental payload transport that needs no VARA: a native OFDM modem that puts
a message on the air through the soundcard and Guardian's own PTT.

VARA P2P remains the default and is unchanged. An existing configuration loads
untouched, and nothing about the ARDOS control plane — AFSK 1200 on FM, MFSK 16 on
HF, the handshake, routing — has moved.

**Nothing in this release has been on the air.** Every measurement below is from a
simulated channel on a PC. That is the point of the milestone: the modem is
measurable before any RF is involved, and the remaining hardware step is isolated
to one class.

## Guardian OFDM VHF

Selectable in Settings as *Guardian OFDM VHF (Experimental)*. What it is:

- **A real OFDM physical layer**, built the DMT way so the samples handed to the
  soundcard are real by construction rather than a complex waveform with its
  imaginary half thrown away. Subcarrier index maps straight onto audio frequency,
  which is what makes the occupied bandwidth an honest parameter instead of an
  implicit constant.
- **BPSK, QPSK, 16-QAM and 64-QAM**, Gray-mapped, power-normalised, with
  soft-decision output. All four are implemented and tested; QPSK is the default
  for data and BPSK carries the header and every acknowledgement.
- **Synchronisation that does not assume a tidy array.** A burst is found in a
  buffer with an arbitrary delay and a noise floor either side, located to within
  two samples, and its frequency offset measured to a fraction of a hertz.
- **Channel estimation and per-carrier equalisation**, with the pilot phases fitted
  to a straight line each symbol — the constant term removes residual frequency
  offset, and the slope removes the timing drift that two independent soundcards
  produce.
- **FEC and interleaving** reusing Guardian's existing rate-1/2 K=7 convolutional
  code with soft decisions, and an interleaver whose spreading properties follow
  from its construction rather than from luck with one profile.
- **Stop-and-wait ARQ** with acknowledgements, negative acknowledgements, retry
  limits and duplicate suppression, where PTT turnaround is an explicit parameter
  rather than slack hidden in a timeout.
- **Measurements, and no invented ones.** SNR, EVM, sync confidence, frequency
  offset, per-carrier channel response, audio level and crest factor, packet error
  rate, retransmission rate. Anything unavailable is reported as unavailable rather
  than guessed, because an adaptation controller will eventually believe these.

### What it measured

Through the deterministic channel simulator, MCS1 QPSK, 512-byte blocks, twenty
seeded runs per point:

| In-band SNR | Blocks decoded | SNR the receiver measured | Wrong bytes delivered |
|---|---|---|---|
| 12 dB | 20/20 | 12.1 dB | 0 |
| 10 dB | 20/20 | 10.1 dB | 0 |
| 8 dB | 20/20 | 8.1 dB | 0 |
| 6 dB | 18/20 | 6.1 dB | 0 |
| 5 dB | 8/20 | 5.1 dB | 0 |
| 4 dB | 0/20 | 4.1 dB | 0 |

The measured SNR tracks the applied SNR to about 0.2 dB, which is the check that
the whole chain is scaled correctly. **Zero wrong bytes at every SNR** is the
column that matters: below the cliff, blocks are rejected, never delivered
corrupted.

A 4096-byte transfer at 12 dB with a forced retransmission completes at
**1247 bit/s** end to end — measured from channel occupancy, with the preamble,
training block, header, acknowledgements and PTT turnaround all counted. Not a
nominal figure, and not an over-the-air claim.

The waveform also survives arbitrary delay, ±10 Hz of frequency offset, gain from
×0.02 to ×3, clipping, ±20 ppm of clock offset, a 1 ms echo, three carriers
notched 30 dB down, and all of those at once.

### Bandwidth is not decided

The occupied bandwidth a VHF radio actually passes is still to be measured. The
one profile that exists, BENCH, is labelled a simulation and bench profile
everywhere it appears: 48 kHz sampling, 1024-point FFT, 52 carriers spanning about
2.4 kHz. Deriving a real air profile is a new entry in the profile registry, not a
modem change — and `docs/ofdm-vhf.md` describes the five-step two-radio procedure
for producing one.

Sample rate and occupied bandwidth are separate quantities throughout. 48 kHz of
sampling carries a burst about 2.4 kHz wide.

### Mixing with VARA stations is safe by construction

`START_VARA` keeps its identity on the wire and now means "begin the negotiated
payload phase". That is only sound if a station can never be played OFDM while it
is listening for VARA, so both peers must claim the transport independently: the
initiator sets a spare bit in `HAVE_MSG`, and the responder echoes it in
`ACK_HAVE` only if it is configured the same way. Either side alone leaves the
pair on VARA.

A build that predates this never sets the bit and keeps unknown flag bits intact,
so a mixed pair degrades to VARA **before `START_VARA` is ever sent** — on the
control channel, where both operators can see it happen. The wire format is
unchanged and the protocol version stays 1.

### Readiness stops demanding VARA

An OFDM station with no VARA installed anywhere is ready. What is checked instead:
the receive and transmit audio devices resolve, the radio and PTT are configured,
and the waveform profile is valid. The VARA installer and readiness flow are
untouched for VARA stations.

## The transmitter is never left keyed

The keying path is `ptt(True) → lead → play → wait`, and then in a `finally`,
`tail → ptt(False)`. Tests cover release on success, on a playback exception, on a
refused channel change, on a failed codec handoff, and on a soundcard that cannot
be opened at all. A failed transfer costs one message; a transmitter stuck on
costs the band.

The soundcard also goes back to the control modem *before* the transfer reports
its result, matching the VARA backend hook for hook — that callback may key the
radio immediately to send a RECEIVED frame, and it would find no codec otherwise.

## The control modem got faster

Guardian's Viterbi decoders now evaluate all 64 trellis states at once instead of
looping over them in Python. A 512-byte OFDM block decodes in about 36 milliseconds
rather than seconds, which is what makes the bench usable. The survivor rule is
unchanged — including which predecessor wins an equal-cost tie — so the MFSK
control modem decodes bit-for-bit as it did before, verified against the previous
implementation across fifty-five trials including tie-heavy pure noise.

## Installed beside G1

G2 is a separate product as far as Windows is concerned: its own installer
identity, its own program folder, its own Start-menu entry, its own executable
name, and its own data directory under `%APPDATA%\Guardian-G2`.

The data directory is split deliberately rather than for tidiness. Sharing it
would mean that launching G1 once silently strips every G2-only setting, because
G1's loader drops keys it does not recognise and then writes the whole
configuration back. On first run G2 copies an existing G1 configuration across, so
the station does not have to be set up twice.

## A bench tool

```
python tools/ofdm_bench.py                    # a whole message, with ARQ
python tools/ofdm_bench.py --single           # one burst, in detail
python tools/ofdm_bench.py --sweep            # decode rate against SNR
python tools/ofdm_bench.py --single --write-wav b.wav   # capture as audio
python tools/ofdm_bench.py --read-wav b.wav   # decode audio back to bytes
```

The WAV modes are what make this useful once there are radios: record the audio a
receiver actually hears, hand the file to `--read-wav`, and every measurement
describes the real channel instead of a simulated one. No RF is needed for any of
it.

## What is deliberately not here

Architected for, not built: the final RF bandwidth, radio-specific profiles, a
50 kHz mode, automatic MCS selection, per-subcarrier bit loading, LDPC, and a
constellation display. `docs/ofdm-vhf.md` records the forward design for each,
what is measured today, what the known limits are — an echo past the 2.67 ms guard
breaks the link, a quarter of the band notched out is past what a rate-1/2 code can
repair — and what the first two-radio test should do.

Over-the-air ARQ is the one piece that cannot be finished on a bench. The state
machine is fully exercised against a simulated duplex channel and the audio pipe
is unit-tested against a fake soundcard for keying and codec ordering, but the two
have never run together against a radio. That is the next milestone's opening
task, and it is isolated to `guardian/payload/ofdm_vhf.py`.

## The icon

The G2 application icon carries a large red **2** over the shield, drawn as
vectors like the rest of the icon so there is still no binary asset in the
repository. It stays legible at the 16-pixel tray size.
