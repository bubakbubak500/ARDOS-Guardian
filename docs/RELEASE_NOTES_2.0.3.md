# Guardian G2 2.0.3 — the bench is in the application, and there is a ladder to climb

Two things an operator asked for, and both were fair criticisms of 2.0.2.

The first: the bench was a Python script. Measuring the modem meant a terminal and
a command line, which is not a reasonable thing to ask of anyone standing at a
radio. **It is now in the application**, under Tools ▸ Modem test, and nothing
about testing this modem needs a console any more.

The second: there was exactly one waveform profile, labelled a bench profile, and
no way to try a wider one. **There are now six**, selectable in Settings, forming
a ladder from 1.2 kHz to 40 kHz.

## Tools ▸ Modem test

Everything the old script did, in the app:

- **Pick a profile and an MCS** and see immediately what that combination means —
  occupied band, sampling rate, FFT size, guard, carrier counts, header cost, PHY
  rate, full-block airtime.
- **Run one burst** through the channel simulator and read every measurement:
  measured against applied SNR, EVM, frequency offset, sync confidence, channel
  spread, crest factor.
- **Run a transfer** with ARQ and read the retries, packet error rate and measured
  throughput, with the log streaming as it goes.
- **Run a sweep** of decode rate against SNR, filling in as it goes, cancellable,
  with **wrong-byte deliveries** called out — that number must be zero, and if it
  is ever not, it is the most serious result this modem can produce.
- **Save a transmit test file**: a clean burst, no channel applied, which is what
  gets played through a radio. Several repeats with gaps means one transmission
  yields several independent measurements of the same settings.
- **Decode any WAV**, not just captures Guardian recorded.

The measurement code moved into the package as `guardian/ofdm/bench.py` and
returns data rather than printing it. `tools/ofdm_bench.py` still exists for a
developer who wants the numbers in a terminal or in a diff, but it is now a thin
front end over the same engine — so the figures on screen and the figures in a
terminal are produced by one implementation and cannot drift apart. That was the
real reason for the move: `tools/` is a directory of scripts, not an importable
module, and a frozen build has no `tools/` at all.

## The profile ladder

| Profile | Occupied | Baseband | Sampling | QPSK rate |
|---|---|---|---|---|
| `NARROW_1K2` | 1.2 kHz | 539–1758 Hz | 48 kHz | 917 b/s |
| `BENCH` | 2.4 kHz | 539–2977 Hz | 48 kHz | 1 833 b/s |
| `WIDE_5K` | 4.9 kHz | 539–5414 Hz | 48 kHz | 3 708 b/s |
| `WIDE_10K` | 9.8 kHz | 539–10289 Hz | 48 kHz | 7 417 b/s |
| `WIDE_20K` | 18.8 kHz | 539–19289 Hz | 48 kHz | 14 250 b/s |
| `WIDE_40K` | 40.0 kHz | 1102–41133 Hz | 96 kHz | 30 500 b/s |

Every rung shares **one subcarrier spacing (46.875 Hz) and one guard interval
(2.67 ms)**. That is deliberate: frequency-offset tolerance is set by the spacing
and multipath tolerance by the guard, so climbing the ladder changes the bandwidth
and nothing else. A burst that survives a given dial error or a given echo on one
rung survives it on all of them.

Every rung is tested through the simulator at every MCS. Widening genuinely is a
profile entry and not a code change — that was the design intent from the start,
and it is now verified rather than asserted.

**Widening is not free**, and the notes say so wherever the choice is offered: the
same transmit level spread over twice the carriers is 3 dB less per carrier.
Measured across the ladder, `BENCH` → `WIDE_20K` costs about 9 dB and buys about
eight times the throughput. The widest profile that decodes is not necessarily the
one to run; the widest that decodes *with margin* is.

None of these is a proven air profile. They exist to be tried, in order, until one
stops working — and what decides that is not the radio's channel spacing but the
audio bandwidth its receive path passes, which differs between two taps on the
same radio.

`WIDE_40K` needs a sound card that will open at **96 kHz**, and says so where it
is chosen, because that is a prerequisite worth anticipating rather than debugging.

## A gap that would have wasted a session

The recorder took its sample rate from the control modem, which is always 48 kHz.
A capture of a 96 kHz profile would have been recorded at 48 kHz and aliased. It
now takes the rate from the payload waveform, so a `WIDE_40K` capture is recorded
at 96 kHz. The bench would have caught the mismatch and refused the file rather
than reporting nonsense — but the capture, and the transmission that produced it,
would already have been wasted.

## The on-air procedure, rewritten

`docs/OFDM_AIR_TEST.md` is now **four numbered tests** rather than a five-step
narrative, each with a stated purpose:

1. **Prove the software, no radio** — five minutes in Tools ▸ Modem test, so that
   anything seen later is the radio rather than the installation.
2. **What bandwidth does your radio actually pass?** — one radio transmitting, one
   recording, climbing the ladder. No Guardian receiver involved, so nothing can
   be confused by a software problem at the far end. This is the test that matters.
3. **A live half-duplex exchange** — the audio pipe against reality, with a table
   of which symptom means which setting.
4. **End to end, and the fallback** — a real message with an attachment over OFDM,
   then deliberately mismatching the two stations to prove the pair falls back to
   VARA before anything is transmitted.

No step in any of them needs a console.

**It is also in Czech**, at `docs/OFDM_AIR_TEST.cs.md`. This is the document that
gets used at a radio, by whoever is at the radio, and the two versions carry the
same four tests and the same tables.

Nothing about the modem itself changed in this release, and nothing has been on
the air yet.
