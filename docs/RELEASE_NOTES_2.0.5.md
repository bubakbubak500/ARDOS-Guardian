# Guardian G2 2.0.5 — the modem stops flattering itself

The modem has been on the air. OK7PS and OK2IPW ran the full procedure on
2026-08-09 with two IC-705s, and the captures they brought back said something
uncomfortable: **Guardian had been reporting an SNR 8 dB better than the link was
actually delivering**, and every judgement anyone made from that number was
built on it.

This release makes the number honest, fixes the two faults that turned a
delivered message into a reported failure, and writes what the radios measured
into the procedure so the next session starts from evidence.

The full analysis, with per-capture figures, is in
`docs/OFDM_AIR_RESULTS_2026-08-09.md`.

## The SNR was measuring only half the problem

Guardian estimated SNR from the two training symbols at the front of every
burst. Those two symbols are *identical*, so the estimate comes from subtracting
one from the other — which leaves the random noise and cancels, exactly,
everything that is the same in both. Random noise differs between them. Every
deterministic distortion a radio path adds does not.

The consequence, measured on nine captures: reported 17.6–20.6 dB, actually
delivering **9.7–12.0 dB**.

The receiver now also measures what it really got. Once a section has decoded and
passed its CRC the transmitted bytes are known, so re-encoding them reproduces
the exact constellation points the sender used, and comparing those with what
came out of the equaliser gives the true error vector — noise, distortion,
channel-estimate error and the equaliser's own losses, all of it.

Both figures are shown, because the gap between them is itself the measurement:

- **Link SNR (what the modem got)** — act on this one.
- **Noise-only SNR** — the old figure, kept and renamed for what it is.

About 3 dB apart is the receiver's implementation loss and as good as it gets.
Eight dB apart, which is what those radios showed, means most of what is hurting
the link is not noise, and more power will not touch it.

## The error vector was flattering the mode that was failing

64-QAM reported **18.8 %** EVM on bursts that never decoded, while QPSK reported
32.6 % on bursts that decoded every time. The failing mode looked like the
cleanest one.

That is what a decision-directed measurement does to a dense constellation: it
compares each symbol with the nearest constellation point, and when symbols are
landing on the wrong points, the wrong point is close by. Measured against the
reference, the same 64-QAM burst is **30.5 %** and the ordering is right way up.

## What each mode needs, measured

The MCS table now carries a threshold per mode, swept in 1 dB steps with eight
512-byte blocks per step, read from the same link SNR the receiver reports on
air:

| Mode | Link SNR needed |
|---|---|
| MCS0 BPSK r=1/2 | ≤ 3 dB |
| MCS1 QPSK r=1/2 | ≤ 3 dB |
| MCS2 16-QAM r=1/2 | 10.5 dB |
| MCS3 64-QAM r=1/2 | 15.5 dB |

Analyse any capture and a new row, **What this SNR carries**, names the fastest
mode the link will hold — and when a burst failed, says how far short of the mode
in use it was. On the 2026-08-09 path that reads: MCS1 and below; MCS3 needs
about 16 dB and this burst had 10.3 dB, which is why it did not decode.

That was not a modem fault. 64-QAM at rate 1/2 needs 15.5 dB and the link had 10.

## A message arrived, was acknowledged, and was reported as failed

`#270532609` reached OK2IPW intact and was shown there as delivered. OK7PS
retried five times, heard nothing, reported a failure and sent CANCEL. Two
faults, independently sufficient, both fixed.

**The squelch could be deafened by the station's own transmission.** The receive
squelch seeded its noise floor from the first block of audio it saw — and a
station only listens for a payload answer immediately after transmitting, when
the first audio back is the AGC recovering and the carrier dropping. Reproduced
in a test at −17 dBFS against a real floor near −60. The trigger sits at three
times the floor, so it ended up about 40 dB too high and the acknowledgement
could never open it. The floor now falls with a 100 ms time constant and rises
with a 10 s one, so it settles on the quiet part whatever it was seeded with.

**Nothing protected the final acknowledgement.** The receiver returned the
instant it had the whole message, gave the soundcard back to the control modem
and stopped listening. Lose that one burst and the sender retransmits into a
station that is no longer there. The receiver now holds the channel for one
reply window after completing a message and re-acknowledges a retransmission, so
a lost last ACK costs one extra burst instead of the transfer.

## "No answer" now says which fault it was

A sender that heard nothing used to log `no answer to block 0`, which covered
two problems with opposite fixes. It now distinguishes them:

- `nothing heard (squelch floor -52 dBFS, opens at -42 dBFS)` — no audio rose
  above the squelch, and the two levels say whether the squelch was the problem.
- `2 burst(s) heard, none usable: header rejected: ...` — the answer arrived and
  could not be read.

## Decoding stopped racing the far end's timeout

`decode_burst` decoded *every* detected candidate before choosing one — around
200 ms of Viterbi each, spent inside the window the far station is holding its
transmitter off waiting for a reply. It now stops at the first readable header.

## What the radios said about bandwidth

Test 2's answer, from the `WIDE_5K` captures: the IC-705 audio path is flat to
about 2 kHz, −6 dB by 3 kHz, and drops **14 dB in the single carrier spacing
between 3000 and 3188 Hz**. Below that it is 20–45 dB down.

So `BENCH` — 539–2977 Hz — is very nearly exactly the usable band, and no new
profile is needed. `WIDE_5K` puts half its carriers in the stop band, which is
why it showed 42–53 dB of channel spread and never produced a payload. That is
the filter, not the link, and no level will change it.

## The procedure

`docs/OFDM_AIR_TEST.md` and the Czech `OFDM_AIR_TEST.cs.md` now carry the
measured numbers instead of the pre-flight guesses: the two SNRs and what the
gap between them means, the per-mode thresholds, the measured passband, and the
deviation sweep — which was the one step skipped last time and is the one most
likely to move the 8 dB.
