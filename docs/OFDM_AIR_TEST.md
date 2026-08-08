# Guardian OFDM VHF — the first on-air test

A field sheet. Print it or keep it open beside the radio.

The modem has never transmitted. Everything measured so far was measured through
a simulated channel, and the occupied bandwidth a real radio passes is unknown —
that is the number this session exists to find.

Work through the steps in order. **Steps 1 and 2 need only one radio and a
recorder, and they are the ones that produce the useful data.** Do not skip
ahead to a live exchange: if step 4 fails you will not know whether the problem
is the radio, the audio path or the software, and steps 1–2 tell you which.

---

## What you need

- Two stations. (Strictly, one station and any recorder will do for step 1, but
  G2 records for you now — see below.)
- A VHF simplex channel you are licensed to use and that is quiet. Do not do
  this on a repeater, a calling channel, or an APRS frequency.
- G2 2.0.2 or later installed. **The recorder is built in**: no Audacity, no
  phone against the speaker, and nothing to get wrong about the file format.

## The built-in recorder

Guardian records the audio it receives straight to a file the offline tools can
read. It writes mono 16-bit PCM at the audio path's own rate, which is exactly
what `tools\ofdm_bench.py --read-wav` expects, so there is no resampling or
exporting step where a good capture can be turned into a misleading one.

Captures land in:

```
%APPDATA%\Guardian-G2\captures\capture-YYYYmmdd-HHMMSS.wav
```

Start and stop it from the shell. While it runs you can see the elapsed time and
the peak level, and **watch that level** — it is the difference between a session
you can use and one you have to repeat. When you stop, Guardian tells you
straight away whether the capture was silent, clipping, or usable, and can decode
it there and then to show the sync confidence, SNR, EVM and frequency offset.

It does not need the control channel to be running. A station brought up purely
to record what the other end transmits is exactly step 1, and that is the case it
is built for. It will refuse to record while a payload transfer owns the sound
card, and say so.

Before transmitting, identify. This is an experimental data waveform; a station
hearing it will not recognise it, so send a voice or CW ID first and say what you
are doing.

---

## Step 0 — Prove the bench before touching RF (5 minutes, no radio)

On the PC that will transmit:

```powershell
cd C:\Users\ok7ps\Documents\Guardian
python tools\ofdm_bench.py
python tools\ofdm_bench.py --sweep --runs 20
```

Both must end `result: PASS` and the sweep must end `wrong-byte deliveries: 0`.
If they do not, stop — something is wrong with the installation, not the radio.

Then make a reference file so you have a known-good capture to compare against:

```powershell
python tools\ofdm_bench.py --single --bytes 512 --write-wav reference.wav
python tools\ofdm_bench.py --read-wav reference.wav
```

**Save `reference.wav`.**

---

## Step 1 — One transmission, recorded, decoded offline

This is the measurement that matters most, and it involves no Guardian receiver
at all — which is exactly why it is first. Nothing about it can be confused by a
software problem at the far end.

**Station A (transmit):**

1. Settings ▸ Payload & data modem → *Guardian OFDM VHF (Experimental)*.
   Leave the profile as BENCH and the MCS as MCS1 QPSK.
2. Settings ▸ Audio → set the TX device to the interface feeding the radio.
3. Set the radio to the test channel. Set deviation/mic gain to whatever you
   normally use for a data mode; note the setting down.
4. Identify by voice.
5. Transmit one burst. The simplest way is the bench tool with the audio routed
   to the radio, or send a short message to a station that will not answer.

**Station B (receive):**

6. Start recording in Guardian *before* A transmits, and stop after. Read the
   verdict it gives you: if it says silent or clipping, fix that and repeat
   before doing anything else.
7. Decode it — either with the Analyse button on the dialog Guardian shows, or
   from the command line, which prints more:

```powershell
python tools\ofdm_bench.py --read-wav %APPDATA%\Guardian-G2\captures\capture-....wav
```

**Write down, for every capture:** sync confidence, measured SNR, EVM, CFO
estimate, and whether the result was PASS.

Guardian's own captures are always at the right rate. If you record with
something else and the rate is wrong the tool will say so and stop — record again
rather than resampling, because a wrong rate looks exactly like a broken modem.

### What the numbers mean

| Reading | Good | If it is wrong |
|---|---|---|
| `no burst detected` | — | The burst is not in the file, is too quiet, or the rate is wrong. Check the recording level first. |
| sync confidence | > 0.7 | Below that, the burst is close to the noise. Try more transmit level or a stronger signal path. |
| measured SNR | > 10 dB | Below 7 dB, MCS1 will start failing. Try MCS0. |
| EVM | < 40 % | High EVM with a good SNR means distortion, not noise — usually clipping. |
| CFO estimate | near 0 on FM | A large offset on FM is unexpected and worth reporting. On SSB it is the dial difference and is normal. |
| RX audio crest | 10–13 dB | Much lower means the path is clipping the peaks. Reduce transmit or receive level. |
| channel response spread | < 10 dB | A large spread is the radio's audio filter, and it is the finding this session is for. |

**Save every capture WAV.** They are the raw material for everything that
follows; the printed numbers are a summary of them, not a substitute.

---

## Step 2 — Find the real bandwidth (the point of the exercise)

Repeat step 1 while changing one thing at a time. Guardian names each capture by
timestamp, so keep a note as you go — timestamp, then what you changed. Renaming
the files afterwards works too; what must not happen is ending up with a folder
of captures and no record of the conditions.

Vary, in this order:

1. **Transmit level / deviation.** Three settings: what you normally use, clearly
   lower, clearly higher. Watch the receive crest factor and EVM. Somewhere there
   is a level above which the path clips; find it.
2. **MCS.** MCS0 (most robust), MCS1, MCS2. Note which still decode.
3. **Where the audio is taken from**, if you have a choice — discriminator versus
   speaker output, for instance. These have very different frequency responses.

The reading to collect above all others is the **channel response spread**, and
better still the per-carrier response itself. It says which parts of the
2.4 kHz band the radio actually passes flat. That is what a real air profile is
derived from, and it cannot be guessed from a datasheet.

---

## Step 3 — Derive an air profile (at the desk, not at the radio)

From the step 2 captures, decide which subcarriers the radio passes cleanly, then
add a new entry beside `BENCH` in `guardian/ofdm/config.py` — do not edit BENCH,
and do not edit anything else. Every test will still pass, because nothing
outside that file depends on BENCH's numbers.

Send me the captures and I will do this with you; the choice of `first_carrier`
and `num_carriers` should come from the measured response, not from taste.

---

## Step 4 — A live half-duplex exchange

Only now put both stations on the full path. Both must be set to
*Guardian OFDM VHF*, or the handshake will fall back to VARA and you will be
testing VARA (which is correct behaviour, and worth confirming once
deliberately).

Send a short message — a few hundred bytes, not an attachment.

What is actually being tested here is the audio pipe against reality, and these
are the four settings that will need adjusting. All are in Settings ▸ Payload &
data modem:

| Symptom | Setting | Direction |
|---|---|---|
| Peer never answers; nothing decodes at all | *Keying lead before transmit* | Increase. The transmitter is not up before the preamble starts. |
| Bursts decode but the last block of a message fails | *Keying tail after transmit* | Increase. The tail of the burst is being cut off. |
| Answers arrive but arrive late; retries climb | *Retransmissions per block* | Leave it; the timeout is the real problem — tell me and I will widen it. |
| Blocks fail consistently at a good SNR | *OFDM modulation (MCS)* | Drop to MCS0. |

Then try a message with a small attachment, so segmentation and reassembly get
exercised over more than a couple of blocks.

**Save the Guardian log for the whole session.** The `payload` and `control`
source lines are the ones that matter, and they carry the per-block SNR, EVM,
retry counts and the reason for every failure.

---

## Step 5 — A deliberate fallback check

Set one station back to *Guardian VARA P2P* and leave the other on OFDM, then
send a message. It must complete over VARA, and the log should say so. This
proves the safety property that lets the two coexist: a station is never played a
waveform it is not listening for.

---

# What to send me

The more of this the better, but the first two items are worth more than
everything else combined.

## 1. The WAV captures — the most valuable thing by far

Everything in `%APPDATA%\Guardian-G2\captures\` from steps 1 and 2, plus the
reference file from step 0. Guardian names them by timestamp, so send a note
saying what each timestamp was — level, MCS, where the audio came from.

Why they matter more than any log: a capture lets me run the *entire receiver*
over exactly what your radio produced, as many times as I like, with changes.
A log tells me the outcome; a WAV lets me fix it. Almost every improvement worth
making — the squelch, the timing, the profile, the equaliser — can be developed
against a capture without you touching the radio again.

Do not trim, normalise, denoise, or convert them to MP3. The silence before and
after the burst is data: the noise floor is what the squelch is measured against,
and lossy compression destroys the phase relationships the modem depends on.
Guardian deliberately does none of those things on the way out, so a file it
wrote is already in the state I want it in — just send it as it is.

## 2. The Guardian log

Copy the Log workspace out, or send the file from
`%APPDATA%\Guardian-G2\`. Include the whole session, failures included —
especially the failures.

## 3. The station facts

- Radio model, at each end.
- Audio interface (AIOC, digirig, sound card, cable into the mic socket…).
- How the radio is keyed: CAT/Hamlib, VOX, or a serial line.
- Frequency and mode (FM, NFM, packet-FM…), and the channel bandwidth if the
  radio has a setting for it.
- Deviation / mic gain / receive level settings, and what you changed them to.
- Roughly how far apart the stations were, and whether it was line of sight.

## 4. The numbers you wrote down

The table from step 1 for each capture. If you only have the numbers and not the
WAVs, send the numbers — but the WAVs are what let me act.

## 5. What surprised you

Anything that behaved unlike the documentation, took longer than it should have,
or made you uncertain what the software was doing. That last category is the most
useful and the least often reported.

---

## How to send it

WAV files are large. Zip the captures together; if the archive is too big for
whatever channel you are using, send step 1's captures first — one clean capture
at a known level is worth more than ten unlabelled ones.

---

## One thing to be careful about

This waveform occupies about 2.4 kHz continuously for up to five seconds per
burst, and a station that hears it will not know what it is. Keep the channel
choice conservative, identify before each session, and keep the transmissions
short while you are finding the levels.
