# Guardian OFDM VHF — the on-air tests

Four tests, in order. Nothing here needs a console, a script, or PowerShell —
everything is in the application.

*Česká verze: [OFDM_AIR_TEST.cs.md](OFDM_AIR_TEST.cs.md)*

**The modem has now transmitted.** OK7PS and OK2IPW ran tests 1–4 on
2026-08-09 with a pair of IC-705s, and what came back changed three of the
numbers in this document and one of its assumptions. The results are in
[OFDM_AIR_RESULTS_2026-08-09.md](OFDM_AIR_RESULTS_2026-08-09.md); the short
version is:

- The audio path passes to about **3 kHz** and falls off a cliff there —
  14 dB in one carrier spacing. `BENCH` fits inside it; `WIDE_5K` does not, and
  no amount of level will change that.
- The link delivered **9.7–12.0 dB** after equalisation while Guardian was
  reporting 18–20 dB. The old figure could not see distortion. It can now, and
  the dialogs show both.
- **MCS3 cannot work on that path** and MCS2 sits on its threshold. That is
  arithmetic, not a fault.

Do the tests in order anyway. If test 4 fails and you skipped 1–3, you will not
know whether the fault is the radio, the audio path, or the software.

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
96 kHz). Note where it stops decoding.

On the IC-705 pair it stopped immediately: `WIDE_5K` showed 42–53 dB of channel
spread, because everything it puts above 3.2 kHz lands 20–45 dB down. If your
radio does the same, the ladder is over and `BENCH` is your profile — it occupies
539–2977 Hz, which is very nearly exactly what that path passes.

**Then the deviation sweep, which is the part that got skipped and matters
most.** Go back to `BENCH` MCS1 and repeat it at three drive settings — normal,
clearly lower, clearly higher — and write down the **gap between the two SNRs**
each time. That gap is the distortion, it is the 8 dB standing between this link
and MCS2, and drive level is the most likely thing controlling it. Nothing else
in this document will buy as much.

### Write down, per capture

Profile · deviation · sync confidence · **link SNR** · noise-only SNR · EVM ·
CFO · channel response spread · PASS/FAIL.

### The two SNRs, and why the dialog now shows both

This is the single most important thing the first air tests taught, so read it
before reading the table.

**Link SNR (what the modem got)** is measured by re-encoding what the burst
decoded to and comparing it with what came out of the equaliser. It counts
everything between the far station's constellation and yours: noise, distortion,
channel-estimate error, the equaliser's own losses. **This is the number that
predicts whether a mode will work.**

**Noise-only SNR** comes from the two training symbols at the front of the
burst. They are identical, so subtracting one from the other leaves the random
noise — and cancels, exactly, every impairment that is the same in both. Every
deterministic distortion a radio adds is the same in both. On 2026-08-09 it read
18–20 dB on a link that was really delivering 9.7–12.0 dB.

Neither is wrong. They measure different things, and the gap between them *is*
the distortion in your path. A gap near 3 dB is the receiver's own
implementation loss and is as good as it gets. **A gap of 8 dB, which is what
those radios showed, means most of what is hurting the link is not noise** — and
turning the power up will not move it.

| Reading | Healthy | If it is not |
|---|---|---|
| no burst detected | — | Not in the file, too quiet, or wrong rate. Check the level first. |
| sync confidence | > 0.7 | Burst is close to the noise. More level, or a narrower profile. |
| **link SNR** | see the mode table below | This is the one to act on. |
| gap to noise-only SNR | ~3 dB | 8 dB means distortion, not noise. Change the drive level, not the power. |
| EVM | < 25 % | Above that only MCS0/MCS1 are safe. |
| CFO | near 0 on FM | A large offset on FM is unexpected; tell me. On SSB it is the dial difference and normal. |
| channel spread | < 10 dB | **This is the finding.** A large spread is the radio's audio filter — the edge of what it passes. `WIDE_5K` showed 42–53 dB on an IC-705, which is the filter, not the link. |

### What each mode needs

Measured by sweeping the channel simulator in 1 dB steps, eight 512-byte blocks
per step, reading the same **link SNR** the receiver reports on air. The figure
is the first step where all eight decoded.

| Mode | | Link SNR needed | On the 2026-08-09 IC-705 path (9.7–12.0 dB) |
|---|---|---|---|
| MCS0 | BPSK r=1/2 | ≤ 3 dB | comfortable |
| MCS1 | QPSK r=1/2 | ≤ 3 dB | comfortable — **the one to use** |
| MCS2 | 16-QAM r=1/2 | 10.5 dB | on the threshold; decodes, will not be reliable |
| MCS3 | 64-QAM r=1/2 | 15.5 dB | 5 dB short — never decoded, and could not have |

Guardian now says this for you: analyse any capture and the **What this SNR
carries** row names the fastest mode the link will hold, and — when the burst
failed — says how far short of the mode in use it was.

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
| Blocks fail consistently at a good link SNR | OFDM modulation (MCS) | Down — check the mode table above first |
| Answers arrive but too late, retries climb | — | Tell me; the timeout needs widening, not a setting |

**Read the "no answer" lines carefully — they now say which fault it is.**
Before 2.0.5 a sender that heard nothing just logged `no answer to block 0`,
which covered two completely different problems. It now says one of:

- `nothing heard (squelch floor -52 dBFS, opens at -42 dBFS)` — no audio ever
  rose above the squelch. A receive-level, keying or wiring fault, or the far
  station never answered. The two levels tell you which: a floor far above the
  real noise means the squelch is the problem, not the radio.
- `2 burst(s) heard, none usable: header rejected: ...` — the answer arrived and
  the modem could not read it. A signal-quality problem.

One thing that used to look like a failure and was not: if the far station shows
a message received and delivered while you show it failed, that was the final
acknowledgement being lost. The receiver now holds the channel for one reply
window after a message completes and answers a retransmission, so a lost last
ACK costs one extra burst instead of the whole transfer.

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
