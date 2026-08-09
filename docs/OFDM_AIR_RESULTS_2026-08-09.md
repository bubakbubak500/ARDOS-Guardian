# OFDM VHF — first two-radio results, 2026-08-09

OK7PS and OK2IPW, a pair of Icom IC-705s on 145.2375 MHz FM, USB Audio CODEC
interfaces at both ends, Hamlib PTT, Guardian G2 2.0.4. Thirteen captures and
both stations' logs.

Every figure below was re-derived offline from the captures, not read off a
screen, so each one can be reproduced from the WAVs in `Recordings/`.

---

## 1. What bandwidth the radios pass — test 2's answer

Measured from the `WIDE_5K` captures, which put carriers from 562 Hz to 5390 Hz
and so measure the whole region in one transmission. Response relative to the
strongest carrier:

| Frequency | Response | Per-carrier SNR |
|---|---|---|
| 562–2062 Hz | −0.2 to −2.9 dB | 17–20 dB |
| 2250–3000 Hz | −3.5 to −6.3 dB | 14–19 dB |
| **3188 Hz** | **−20 dB** | 0.6–4 dB |
| 3375 Hz | −31 to −34 dB | −9 to −11 dB |
| 4000–5250 Hz | −37 to −45 dB | −13 to −25 dB |

**The path is flat to about 2 kHz, −6 dB by 3 kHz, and falls 14 dB in the single
carrier spacing between 3000 and 3188 Hz.** That is an audio filter, and it is
the answer test 2 exists to get.

Consequences, in order of how much they matter:

- **`BENCH` is the right profile for this radio.** It occupies 539–2977 Hz —
  which is, to within a carrier, exactly the usable band. Nothing needs
  inventing; the ladder already contained the answer.
- **`WIDE_5K` cannot work here and never will.** Half its carriers sit in the
  stop band, which shows up as 42–53 dB of channel spread and per-carrier SNRs
  down to −29 dB. Every `WIDE_5K` capture failed, most of them without even a
  readable header. This is not a level problem.
- `NARROW_1K2` is flat to 1.8–2.9 dB across its band and is the fallback it was
  designed to be, at half the throughput.

## 2. The reported SNR was overstating the link by 8 dB

The finding that explains everything else.

Guardian reported 17.6–20.6 dB on every capture. Re-encoding what each burst
decoded to and comparing against the equalised symbols — the true error vector —
gives the real figure:

| Capture | Profile / MCS | Reported | Actual | Result |
|---|---|---|---|---|
| 181007 | BENCH MCS0 | 18.7 dB | **11.1 dB** | decoded |
| 180824 | BENCH MCS1 | 18.1 dB | **9.7 dB** | decoded |
| 181122 | BENCH MCS2 | 20.6 dB | **9.7 dB** | decoded |
| 181222 | BENCH MCS3 | 18.5 dB | **10.3 dB** | payload CRC failed |
| 181523 | NARROW MCS0 | 19.5 dB | **12.0 dB** | decoded |
| 181800 | NARROW MCS1 | 20.2 dB | **12.0 dB** | decoded |
| 181912 | NARROW MCS2 | 17.6 dB | **11.3 dB** | decoded |
| 182012 | NARROW MCS3 | 17.6 dB | **9.9 dB** | payload CRC failed |
| 181426 | WIDE_5K MCS1 | 19.6 dB | **−2.7 dB** | payload CRC failed |

The cause is structural, not a coding error. The SNR came from the two training
symbols at the front of each burst, and those two symbols are *identical* — so
the estimate is built from their difference, and every impairment that is the
same in both cancels exactly. Random noise differs between them and is counted.
Every deterministic distortion is identical in both and is not.

Through the same receiver on a simulated AWGN channel the gap is a steady 3.0 dB
at every SNR from 14 to 26 dB, which is the receiver's own implementation loss:
channel-estimate error from averaging only two training symbols, plus the pilot
phase fit. On air the gap was 8 dB. **The extra 5 dB is distortion in the radio
path, and it was invisible.**

Two candidate causes were tested and rejected: transmit-path clipping (the
received bursts keep their 13 dB crest factor, and a clipped OFDM burst loses
it), and channel drift over the burst (the error is flat from the first symbol
to the last). What remains is a distortion that is flat in time, flat in
frequency on `NARROW_1K2`, and frequency-selective on `BENCH` — consistent with
the FM audio chain itself. **Whether it moves with drive level is the one thing
the tests did not try, and it is the first thing to try next.**

## 3. Why MCS3 never decoded — and it is not a modem fault

Sweeping the channel simulator, eight 512-byte blocks per point, gives what each
mode needs in *actual* post-equalisation SNR:

| Mode | Needs | This path had | Verdict |
|---|---|---|---|
| MCS0 BPSK | ≤ 3 dB | 9.7–12.0 dB | works, wide margin |
| MCS1 QPSK | ≤ 3 dB | 9.7–12.0 dB | works, wide margin |
| MCS2 16-QAM | 10.5 dB | 9.7–12.0 dB | **on the threshold** |
| MCS3 64-QAM | 15.5 dB | 9.7–12.0 dB | **5 dB short** |

MCS3 failed on both profiles, at both stations, on every attempt. It was always
going to: 64-QAM needs 15.5 dB and the link had 10. MCS2 decoded in the
reference bursts and sits exactly on its threshold, which means it will decode
sometimes and not others — the worst kind of setting to run a mail link on.

The decision-directed EVM made this actively harder to see. MCS3 reported
**18.8 %** while MCS1 reported 32.6 %, so the failing mode looked like the
cleanest one. That is what a decision-directed measurement does to a dense
constellation: it slices each symbol to the nearest point, and when the symbols
are landing on the wrong points, the wrong point is nearby. Measured against the
reference, the same MCS3 burst is **30.5 %**, and the ordering comes back the
right way round.

## 4. The live exchange: a delivered message reported as failed

`#270532609`, OK7PS → OK2IPW, 373 bytes, BENCH MCS1:

```
18:24:43  OK7PS   OFDM: sending #270532609 as 1 block(s)
18:24:47  OK2IPW  OFDM: #270532609 received -- 373 B, 1 blocks accepted, SNR 22.8 dB
18:24:49  OK2IPW  TX Received / TX Delivered
18:24:49  OK7PS   OFDM: no answer to block 0, resending      (x5)
18:25:12  OK7PS   [OK7PS#270532609] failed: payload send failed
```

The message arrived intact. OK2IPW acknowledged it, went back to the control
channel and reported it delivered. OK7PS heard none of that, exhausted its
retries, reported a failure and sent CANCEL. Two stations, one transfer, two
opposite verdicts — and the message was fine.

Two independent faults, both now fixed:

**The squelch could be left deaf by the sender's own transmission.** The receive
squelch seeded its noise floor from the first block of audio it saw. A station
only ever listens for a payload answer immediately after transmitting, and the
first audio back is the AGC recovering and the carrier dropping — reproduced in
a test at −17 dBFS against a real floor near −60. The trigger sits three times
the floor, so the floor was pinned about 40 dB too high and nothing could open
it. The floor now falls with a 100 ms time constant and rises with a 10 s one,
so it converges on the quiet part whatever it was seeded with.

**Nothing protected the final acknowledgement.** The receiver returned the
instant it had the whole message, handed the soundcard back to the control modem
and stopped listening. Lose that one burst and the sender retransmits into a
station that is no longer there. The receiver now holds the channel for one
reply window after completing and re-acknowledges a retransmission.

Also fixed, though not a cause here: `decode_burst` decoded *every* detected
candidate before choosing one, at roughly 200 ms of Viterbi each, inside the
window the far end is holding its transmitter off. It now stops at the first
readable header.

The reverse direction, `#2822766593`, completed normally — which is why the
faults presented as an asymmetry between the two stations rather than as a
straightforward failure.

## 5. What to do next

In order.

1. **Sweep the drive level on `BENCH` MCS1** and record the gap between link SNR
   and noise-only SNR at each setting. Closing that 8 dB gap is worth more than
   everything else on this list; if it closes, MCS2 becomes usable and MCS3
   becomes reachable. If it does not move with level, the distortion is in the
   audio chain and the next question is which end.
2. **Re-run the live exchange on `BENCH` MCS1**, both a short message and one
   with an attachment. The two ARQ faults above should show as a working
   transfer; if a "no answer" line appears, it now says which of the two
   problems it is.
3. **Stop testing `WIDE_5K` on these radios.** The measurement is conclusive.
4. **Leave MCS3 alone** until a path measures above 15.5 dB.
