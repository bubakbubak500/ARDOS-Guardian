# Guardian OFDM VHF — the on-air tests

Four tests, in order. Nothing here needs a console, a script, or PowerShell —
everything is in the application.

*Česká verze: [OFDM_AIR_TEST.cs.md](OFDM_AIR_TEST.cs.md)*

The modem has never transmitted. Every number measured so far came from a
simulated channel, and the bandwidth a real radio passes is unknown. **Test 2 is
the one that answers that**, and it is worth more than the other three together.

Do them in order. If test 4 fails and you skipped 1–3, you will not know whether
the fault is the radio, the audio path, or the software.

---

## Before you start

- A VHF simplex channel you are licensed to use and that is quiet. Not a
  repeater, not a calling channel, not APRS.
- G2 2.0.4 installed on both stations.
- **Identify by voice before each session.** This is an experimental data
  waveform; anyone who hears it will not recognise it. Say what you are doing.
- Keep transmissions short while you are finding the levels.

Everything lives in two places:

| | |
|---|---|
| **Tools ▸ Modem test** | profiles, bench runs, transmitting a test burst, decoding any capture |
| **Home ▸ Record received audio** (or Ctrl+R) | capturing what the radio heard |

Captures and generated files go to `%APPDATA%\Guardian-G2\captures\`.

### The profile ladder

Six rungs, each roughly double the last. Every rung has the same subcarrier
spacing and the same guard interval, so moving up changes **only** the bandwidth
— frequency-offset and multipath tolerance stay put.

| Profile | Occupied | Baseband | Sampling | QPSK rate |
|---|---|---|---|---|
| `NARROW_1K2` | 1.2 kHz | 539–1758 Hz | 48 kHz | 917 b/s |
| `BENCH` | 2.4 kHz | 539–2977 Hz | 48 kHz | 1 833 b/s |
| `WIDE_5K` | 4.9 kHz | 539–5414 Hz | 48 kHz | 3 708 b/s |
| `WIDE_10K` | 9.8 kHz | 539–10289 Hz | 48 kHz | 7 417 b/s |
| `WIDE_20K` | 18.8 kHz | 539–19289 Hz | 48 kHz | 14 250 b/s |
| `WIDE_40K` | 40.0 kHz | 1102–41133 Hz | **96 kHz** | 30 500 b/s |

None is a proven air profile. They exist to be tried.

`WIDE_40K` needs a sound card that will open at 96 kHz — if yours will not, the
ladder stops at `WIDE_20K`.

**Widening is not free.** The same transmit level spread over twice the carriers
is 3 dB less per carrier. `BENCH` → `WIDE_20K` costs about 9 dB and buys about
eight times the throughput. So the widest profile that decodes is not necessarily
the one to run — the widest that decodes *with margin* is.

---

# Test 1 — Prove the software, no radio (5 minutes)

Establishes that anything you see later is the radio, not the installation.

**Tools ▸ Modem test.** Profile `BENCH`, MCS1.

1. **Run one burst.** Must say PASS, and the measured SNR must land within about
   a dB of the applied SNR.
2. **Run a transfer.** Must say PASS with 0 retries.
3. **Run a sweep.** Must end with **wrong-byte deliveries: 0**. If that number is
   ever anything but zero, stop and tell me — it is the most serious result this
   modem can produce.

Then **save a transmit test file** and **decode it straight back**. That proves
the generate-and-decode path end to end, on one PC, before a radio is involved.

If any of this fails, the problem is not your radio.

---

# Test 2 — What bandwidth does your radio actually pass?

**This is the important one.** One radio transmitting, one recording. No Guardian
receiver is involved, so nothing about the result can be confused by a software
problem at the far end.

What decides the answer is not the radio's channel spacing — it is the audio
bandwidth its receive path passes, and that differs between two taps on the same
radio.

### Do this

**Station A:**
1. Set the radio to the test channel, at your normal data deviation. Note the
   level setting. Identify by voice.
2. Tools ▸ Modem test → profile `BENCH`, MCS1 → **Transmit into the radio**,
   3 repeats. Guardian keys the radio and plays the burst through the configured
   TX device itself; it will tell you how long it is about to transmit for and
   ask you to confirm first. Three bursts from one transmission is three
   independent measurements.

   (*Save transmit file* is still there if you want the WAV for something else,
   but you do not need it for this.)

**Station B:**
3. Ctrl+R to start recording *before* A transmits. Stop after.
4. Read the verdict. **If it says silent or clipping, fix it and repeat before
   doing anything else** — everything downstream is worthless otherwise.
5. Press **Analyse**.

### Then climb the ladder

Repeat with `WIDE_5K`, `WIDE_10K`, `WIDE_20K` (and `WIDE_40K` if your card does
96 kHz). Note where it stops decoding. Then go back one rung and repeat that one
at three deviation settings — normal, clearly lower, clearly higher.

### Write down, per capture

Profile · deviation · sync confidence · measured SNR · EVM · CFO · channel
response spread · PASS/FAIL.

| Reading | Healthy | If it is not |
|---|---|---|
| no burst detected | — | Not in the file, too quiet, or wrong rate. Check the level first. |
| sync confidence | > 0.7 | Burst is close to the noise. More level, or a narrower profile. |
| measured SNR | > 10 dB | Under 7 dB MCS1 starts failing. Try MCS0 or step down a rung. |
| EVM | < 40 % | High EVM with good SNR is distortion, not noise — usually clipping. |
| CFO | near 0 on FM | A large offset on FM is unexpected; tell me. On SSB it is the dial difference and normal. |
| channel spread | < 10 dB | **This is the finding.** A large spread is the radio's audio filter — the edge of what it passes. |

The channel response spread is what a real air profile gets derived from. Send me
the captures and I will do that with you; the carrier set should come from the
measurement, not from taste.

---

# Test 3 — A live half-duplex exchange

Only now put both stations on the full path, with the profile test 2 showed is
comfortable.

Both stations: Settings ▸ Payload & data modem → *Guardian OFDM VHF
(Experimental)*, same profile, same MCS. Send a short message — a few hundred
bytes, not an attachment.

This tests the audio pipe against reality. Expect to adjust these, all in the
same settings page:

| Symptom | Change | Direction |
|---|---|---|
| Nothing decodes at all; peer never answers | Keying lead before transmit | Up — the transmitter is not up before the preamble starts |
| Bursts decode but the last block of a message fails | Keying tail after transmit | Up — the tail is being cut off |
| Blocks fail consistently at a good SNR | OFDM modulation (MCS) | Down to MCS0 |
| Answers arrive but too late, retries climb | — | Tell me; the timeout needs widening, not a setting |

Then send a message with a small attachment, so segmentation and reassembly get
exercised over more than a couple of blocks.

**Save the log for the whole session** — the `payload` and `control` lines carry
the per-block SNR, EVM, retry counts and the reason for every failure.

---

# Test 4 — End to end, and the fallback

Two parts, both quick, both proving something specific.

**4a — a real message, end to end.** Compose a message with an attachment in Mail
and send it to the other station over OFDM. Watch it arrive, be acknowledged, and
show as delivered. This is the whole stack: ARDOS control handshake, transport
negotiation, OFDM payload, receipt. Note the measured throughput and compare it
with the profile's PHY rate — the difference is the preamble, header,
acknowledgements and PTT turnaround, and it is real.

**4b — the safety property.** Set **one** station back to *Guardian VARA P2P*,
leave the other on OFDM, and send a message. It must complete **over VARA**, and
the log should say so. This proves a station is never played a waveform it is not
listening for: both peers have to claim OFDM independently or the pair falls back
before anything is transmitted.

If 4b does *not* fall back, stop — that is a protocol bug and I need to know
immediately.

---

# What to send me

## 1. The captures — worth more than everything else combined

Everything in `%APPDATA%\Guardian-G2\captures\`, plus a note of what each
timestamp was (profile, deviation, which test).

Why they matter more than any log: a capture lets me run the **entire receiver**
over exactly what your radio produced, as many times as I like, with changes. A
log tells me the outcome; a capture lets me fix it. The squelch, the burst
timing, the air profile, the equaliser — all of it can be developed against a
capture without you going back on the air.

**Do not trim, normalise, denoise, or convert to MP3.** The silence around a
burst is data — it is the noise floor the squelch is measured against — and lossy
compression destroys the phase relationships the modem depends on. Guardian
deliberately does none of those things, so a file it wrote is already right.

## 2. The Guardian log

From the Log workspace or `%APPDATA%\Guardian-G2\`. The whole session, failures
included — especially the failures.

## 3. The station facts

Radio model at each end · audio interface (AIOC, digirig, sound card, cable into
the mic socket) · how it keys (CAT/Hamlib, VOX, serial line) · frequency and mode
· channel bandwidth setting if the radio has one · deviation / mic gain / receive
level and what you changed them to · rough distance and whether line of sight.

## 4. Your table from test 2

The per-capture readings. If you only have numbers and no WAVs, send the numbers
— but the WAVs are what let me act.

## 5. What surprised you

Anything that behaved unlike this document, took longer than it should, or left
you unsure what the software was doing. That last category is the most useful and
the least often reported.

---

## Sending it

Zip the captures. If the archive is too big, send test 2's first — one clean
capture at a known level beats ten unlabelled ones.
