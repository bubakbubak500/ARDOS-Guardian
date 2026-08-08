# Guardian OFDM VHF

An experimental native payload modem. It moves a message payload over a VHF/UHF
radio using the PC soundcard and Guardian's own PTT, with no VARA involved.

Status: **first milestone.** The physical layer, the framing, the ARQ state
machine and the Guardian integration are implemented and tested. Everything has
been measured through a simulated channel on a PC. **Nothing here has been on the
air yet**, and no figure in this document is an over-the-air result.

VARA P2P remains the default transport and is unchanged.

---

## The one thing to get right first

> **Sample rate is not bandwidth.**

The soundcard runs at 48 kHz because the rest of Guardian does. The burst
occupies about **2.4 kHz** of that, because that is how many FFT bins carry
energy. A 48 kHz sample rate does not mean a 48 kHz signal, and nothing in this
implementation treats the two as the same quantity.

The occupied bandwidth that a real VHF radio actually passes is **not yet
known**. It will come out of two-radio measurements. Until then it is a property
of a named profile (§4), and changing it is a new profile entry — not a modem
change, not a settings dialog, and not a constant buried in the DSP.

---

## 1. Architecture

```
guardian/ofdm/                  pure numpy + stdlib. No Qt, no sounddevice,
    config.py                   no Hamlib, no VARA. Testable on a PC with no
    constellation.py            audio hardware at all.
    interleaving.py
    phy.py
    sync.py
    framing.py
    metrics.py
    channel.py
    link.py
guardian/payload/ofdm_vhf.py    the ONLY bridge to hardware
guardian/payload/negotiated.py  routes a transfer to the agreed transport
tools/ofdm_bench.py             measurements without a radio
```

The dependency rule is enforced by imports and worth keeping: `guardian/ofdm/*`
may import numpy, the standard library, `guardian.modem.fec` and
`guardian.protocol.crc16`, and nothing else. That is what makes the whole
physical layer testable in 45 seconds with no radio, no Qt and no soundcard, and
it is what will let the DSP be improved later without touching the application.

`guardian/payload/ofdm_vhf.py` holds `RadioAudioPipe`, the one class that keys a
transmitter and opens a soundcard. The ARQ state machine above it cannot tell a
real radio from the simulator.

### Where it sits in Guardian

The control plane is untouched. ARDOS control bursts still run over AFSK 1200 on
FM and MFSK 16 on HF; the handshake still negotiates who, when and next-hop. Only
the payload transport is new:

```
ARDOS control plane (AFSK1200 / MFSK16)  -- unchanged
        |
   handshake / routing / HAVE_MSG / ACK_HAVE
        |
   selectable payload transport
        |
   +----+----------------+
   |                     |
VARA P2P          Guardian OFDM VHF
(default)           (experimental)
```

---

## 2. Real-valued audio: why DMT

Guardian sends ordinary real audio through a soundcard, not complex IQ into an
SDR. The transmit spectrum is therefore assembled on positive-frequency bins only
and the samples come out of `numpy.fft.irfft`, which imposes Hermitian symmetry
`X[N-k] = conj(X[k])` implicitly. Two consequences:

* The samples are real **by construction**. There is no complex waveform whose
  imaginary half gets quietly discarded.
* Subcarrier index *k* maps straight onto audio frequency *k·fs/N*, so the
  occupied band **is** the active carrier set. That is what makes the bandwidth
  honest and parameterised rather than implicit.

The alternative — complex baseband mixed up to an audio centre frequency — is
equally correct and was rejected deliberately: it adds a mixer, an image to
suppress and one more frequency parameter, and buys nothing at these bandwidths.
Recorded here so the decision does not get re-litigated.

---

## 3. Waveform and frame structure

### One burst

```
[ preamble:  2 symbols ]   detection, coarse timing, frequency offset
[ training:  2 symbols ]   channel estimate and noise measurement
[ header:    7 symbols ]   always MCS0/BPSK, versioned, CRC-protected
[ data:      n symbols ]   MCS taken from the header
[ tail guard: 0.4 s    ]   added by the transport, not the DSP
```

The preamble symbol fills only **even** active bins, which makes its two
time-domain halves identical. That repetition is the only thing the receiver has
to hold on to before it knows anything else.

The training symbols are **identical to each other**. Averaging them gives a 3 dB
better channel estimate; differencing them gives an honest noise measurement,
which is why a profile with fewer than two is rejected outright — with one there
is nothing to subtract and every SNR the receiver reported would be a guess.

### PHY header — 16 bytes, before coding

| Field | Size | Notes |
|---|---|---|
| version | 1 B | OFDM frame format, currently 1. Independent of the ARDOS control-frame version. |
| frame_type | 1 B | DATA / ACK / NACK |
| msg_id | 4 B | Guardian message id |
| block_seq | 2 B | this block's index |
| block_count | 2 B | blocks in the message |
| mcs | 1 B | MCS of the *data* section |
| payload_len | 2 B | bytes in this block |
| flags | 1 B | reserved — a bit-loading map would announce itself here |
| header_crc | 2 B | CRC-16/CCITT-FALSE over the previous 14 bytes |

The header is always sent at MCS0, so a receiver decodes it without knowing
anything in advance — including which constellation the payload used. That is
what makes adaptive modulation possible later with no extra negotiation round.
Its length in symbols follows from the profile alone, so the receiver can count
on it. A corrupt header rejects the whole burst.

### Data section

`payload bytes ‖ crc16(payload)` → convolutional encode → interleave → QAM map →
carriers. A payload CRC failure produces a NACK; it **never** returns wrong bytes
to the caller.

One burst is deliberately not one Guardian attachment. Payloads are cut into
bounded blocks (512 B on BENCH) from the start, because a transmission that has
to restart from the beginning is not a usable link.

### FEC and interleaving

The existing rate-1/2, K=7 convolutional code in `guardian/modem/fec.py` is
reused, not reimplemented, with **soft-decision** decoding: the demapper produces
max-log LLRs weighted by each carrier's measured channel gain, so a carrier the
channel notched out is discounted instead of trusted.

That module's Viterbi decoders were vectorised over the trellis for this work — a
control burst is a few hundred coded bits where a per-state Python loop was
unnoticeable, an OFDM data block is a few thousand where it dominated everything.
A 512-byte block now decodes in about 36 ms instead of seconds. The survivor rule
is unchanged, including which predecessor wins a tie, so the MFSK control modem
decodes bit-for-bit as before.

The interleaver is a single multiplicative stride, `out[(i·s) mod n] = in[i]`,
with `s` near `n/φ` and coprime to `n`. Two properties follow from the
construction rather than from luck with one profile:

* Consecutive coded bits land about six tenths of a block apart — a different
  OFDM symbol *and* a different carrier, at every constellation order.
* A dead carrier destroys one residue class modulo the symbol length per bit it
  carries, so its damage arrives as arithmetic progressions rather than a clump.
  Measured on BENCH, the closest two destroyed code bits ever get is 44 apart at
  BPSK, 39 at QPSK and 16-QAM, and 9 at 64-QAM. The code's memory is 6 bits, so
  each is an isolated error as far as the decoder is concerned.

---

## 4. Synchronisation

Acquisition is four steps. The time-domain steps run on a copy filtered to the
occupied band, which is worth about 10 dB: they would otherwise compete with
noise from ten times the bandwidth the burst uses, and a burst at 8 dB in-band SNR
presents about −2 dB to an unfiltered detector.

1. **Detection and coarse timing.** Schmidl–Cox: correlate the signal against
   itself at a lag of `fft_size/2`. A plateau as wide as the cyclic prefix appears
   where the burst starts, and its height is a correlation coefficient — that
   number *is* the receiver's confidence.
2. **Coarse frequency offset** from the phase of the same correlation,
   unambiguous across a whole subcarrier spacing.
3. **Fine timing** by matched-filtering against the whole known burst head
   (every preamble and training symbol concatenated) — the longest known sequence
   in the burst, and unambiguous where a single training symbol is not.
4. **Fine frequency offset** in the bin domain, from the phase the second
   training symbol accumulated relative to the first.

### Two things that had to be fixed, recorded because they will look like bugs again

* **The metric must be normalised by both correlation windows, not just the
  second.** Schmidl and Cox divide by the second window's energy, which is fine
  while a burst is arriving and unbounded when one is *leaving*: the trailing edge
  puts a loud first half against a quiet second one and the metric reached **45**
  there, so the detector locked onto the end of every strong burst. Dividing by
  the product makes it a correlation coefficient that Cauchy–Schwarz bounds at 1.
* **A normalised metric is blind to amplitude, so it needs an energy gate.**
  Band-limiting leaves the silence around a burst holding a faint filter tail
  which, being dominated by a couple of spectral components, is very nearly a sine
  wave — and correlates with itself beautifully. It scored **0.94** on a guard
  interval 40 dB quieter than the burst beside it. A candidate must now carry a
  quarter of the buffer's loudest window energy.

### Channel estimation and tracking

Least-squares estimate `Ĥ[k] = Y[k]·X*[k]` per active carrier from the training
block, averaged; noise power from the difference of the two training symbols; the
signal power then de-biased by the estimator's own noise before any SNR is
reported. Zero-forcing equalisation per carrier.

Per data symbol, a straight line is fitted through the pilot phases. That removes
two things at once: the constant term is the common phase error a residual
frequency offset leaves behind, and the **slope** is a timing or sample-clock
offset — which is what two independent soundcards will produce on a real link.

A timing error inside the cyclic prefix is only a phase ramp across the carriers,
which the slope fit removes. That is what the guard is for, and it is why the
receiver tolerates being a few samples out.

### Detection threshold

`detection_threshold = 0.40`, chosen from measurement rather than taste. A burst
produces roughly `(SNR/(1+SNR))²` in-band: 0.38 at 2 dB, 0.58 at 5 dB, 0.75 at
8 dB. Over forty 3-second buffers of band-limited noise the largest peak was 0.34
and the median 0.25 — so a threshold of 0.25 accepted noise as a burst in **40 %**
of buffers, while 0.35 accepted none. 0.40 keeps a margin above that and still
detects about 2.5 dB below the point where the decoder gives up anyway.

A false alarm is not fatal: the header CRC rejects it, and `decode_burst` works up
to four candidate positions in a buffer before giving up, so a false alarm sitting
*earlier* than the real burst cannot lose the exchange.

---

## 5. The BENCH profile

**This is a simulation and bench profile.** Its numbers were chosen to make tests
deterministic and conservative, not from any measurement of what a VHF radio
passes. Nothing outside `guardian/ofdm/config.py` depends on them.

| Parameter | Value | Derived |
|---|---|---|
| sample rate | 48 000 Hz | |
| FFT size | 1024 | subcarrier spacing 46.875 Hz |
| cyclic prefix | 128 samples | 2.667 ms of guard |
| symbol duration | 1152 samples | 24.0 ms |
| active bins | 12 … 63 (52) | |
| occupied band | ≈ 539 – 2977 Hz | ≈ 2.44 kHz — fits a stock FM voice channel |
| pilots | every 7th (8) | 44 data carriers |
| preamble / training | 2 + 2 symbols | |
| header | 7 symbols at MCS0 | |
| block size | 512 bytes | |
| transmit level | 0.15 RMS | ≈ 12 dB crest factor, so peaks stay clear of full scale |

Bin 0 (DC) and the Nyquist bin are never active. Everything guard-related is
expressed by which bins are *absent* from the active range, so there is no
separate guard-carrier count to keep consistent with anything.

### MCS table

Every rate is 1/2 because that is what `fec.py` provides. `code_rate` is stored
explicitly anyway, so a punctured or LDPC rate joins the table without changing
its shape.

| MCS | Modulation | Coded bits/symbol | Role |
|---|---|---|---|
| MCS0 | BPSK | 44 | bootstrap: the header and every ACK/NACK |
| MCS1 | QPSK | 88 | **default for data** |
| MCS2 | 16-QAM | 176 | implemented and tested in simulation, not a default |
| MCS3 | 64-QAM | 264 | implemented and tested in simulation, not a default |

---

## 6. Measured simulation performance

All figures below are **simulation results on a PC**, through the deterministic
channel simulator. They are not on-air performance and no over-the-air rate is
claimed.

SNR is stated **in-band**: the ratio of signal power to the noise power landing on
the active carriers. That is what the receiver measures from its training symbols
and what Eb/N0 theory is written in, so a stated 8 dB and a measured 8 dB mean the
same thing. White noise at that density also fills the rest of the audio band, so
the *wideband* SNR of the same audio is about 9.9 dB lower for BENCH. Quoting the
wideband figure would make this modem look roughly 10 dB better than it is.

### Decode rate against SNR — MCS1 QPSK, 512-byte blocks, 20 seeded runs per point

| In-band SNR | Decoded | Measured SNR | EVM | Wrong bytes delivered |
|---|---|---|---|---|
| 24 dB | 20/20 | 24.1 dB | 8.3 % | 0 |
| 20 dB | 20/20 | 20.1 dB | 13.1 % | 0 |
| 16 dB | 20/20 | 16.1 dB | 20.8 % | 0 |
| 14 dB | 20/20 | 14.1 dB | 26.2 % | 0 |
| 12 dB | 20/20 | 12.1 dB | 33.1 % | 0 |
| 10 dB | 20/20 | 10.1 dB | 41.8 % | 0 |
| 8 dB | 20/20 | 8.1 dB | 52.5 % | 0 |
| 6 dB | 18/20 | 6.1 dB | 65.9 % | 0 |
| 5 dB | 8/20 | 5.1 dB | 74.7 % | 0 |
| 4 dB | 0/20 | 4.1 dB | 86.4 % | 0 |
| 2 dB | 0/20 | 2.1 dB | 122.8 % | 0 |

The measured SNR tracks the applied SNR to within about 0.2 dB across the whole
range, which is the check that the chain's normalisation is right. **Zero wrong
bytes at every SNR** is the property that matters most: below the cliff, blocks
are rejected, never silently delivered corrupted.

### Where each mode gives up

| MCS | Reliable from | Nothing decodes below |
|---|---|---|
| MCS0 BPSK | 4 dB | 2 dB |
| MCS1 QPSK | 7 dB | 4 dB |
| MCS2 16-QAM | 12 dB | 8 dB |
| MCS3 64-QAM | 18 dB | 12 dB |

("Reliable from" is the lowest tested SNR with 10/10 decodes.) The ~5 dB steps
between modes are what a future adaptation controller would step across.

### Other impairments, all at 15 dB in-band unless stated

| Impairment | Result |
|---|---|
| Arbitrary delay (0 … 48 000 samples) | located to within ±2 samples, payload exact |
| Frequency offset ±10 Hz | decodes; estimate within 0.2 Hz on a clean channel |
| Fixed phase rotation | absorbed by the channel estimate |
| Gain ×0.02 … ×3 | decodes; measured SNR unchanged (equalisation cancels level) |
| Clipping at 0.45 of peak | decodes |
| Sample-clock offset ±20 ppm | decodes; appears as the pilot phase slope |
| Echo at 1 ms, 0.4 amplitude | decodes; ripple visible in the channel response |
| Echo at 2.67 ms (the full guard), 0.8 | 8/8 decodes |
| Echo at 5.33 ms (twice the guard), 0.8 | collapses — see limitations |
| 3 carriers notched −30 dB | decodes; the notch is measurable in `channel_response` |
| 12 carriers notched −40 dB | rejected, never wrong bytes |
| Everything at once | decodes |

### Measured throughput

Over a full 4096-byte ARQ transfer at 12 dB with one forced retransmission:
**1247 bit/s end to end**, against 1833 bit/s of MCS1 payload carriers. The
difference is the preamble, the training block, the header, every acknowledgement
and the PTT turnaround — all of which are real.

Throughput is measured from **channel occupancy** (airtime in both directions plus
a turnaround per change of direction), not from wall-clock. Wall-clock is the
right measure on air and meaningless in simulation, where a burst is handed over
instantly; airtime is the same quantity in both and is what the radios really
spend.

---

## 7. ARQ

Stop-and-wait, deliberately. There is no window, no congestion control and no
connection — on a half-duplex channel where turning the transmitter around costs a
fraction of a second, having nothing in flight to reason about is a feature.

```
sender:    DATA(seq) -> wait for ACK/NACK -> next block, or send it again
           more than ofdm_max_retries tries => the transfer fails
receiver:  DATA decoded and CRC ok  => ACK(seq), keep the bytes
           already have that seq    => ACK(seq) again, drop the payload
           header ok, payload bad   => NACK(seq)
           no readable header       => stay silent; the sender's timeout handles it
```

Timeouts are derived, not chosen: an ACK's own airtime, plus two PTT turnarounds,
plus a margin. PTT turnaround is an explicit named parameter rather than slack
hidden inside a number, because it is what actually dominates how long an answer
takes.

A duplicate block means the receiver's previous acknowledgement was lost, not that
the sender has anything new to say — so it is acknowledged again and the payload
dropped. Staying silent there would strand the sender.

---

## 8. Guardian integration

### Configuration

`StationConfig` gains only what an operator should touch:

```
payload_backend  = "vara_p2p" | "ofdm_vhf"    (default "vara_p2p")
ofdm_profile     = "BENCH"
ofdm_mcs         = 1
ofdm_tx_lead_ms  = 300
ofdm_tx_tail_ms  = 100
ofdm_max_retries = 4
```

FFT size, cyclic prefix, carrier set and sample rate are **not** here. They belong
to the named profile, because the occupied bandwidth is still to be measured and
changing it must be a new profile entry rather than a settings dialog.

`load()` now allowlists `payload_backend` instead of comparing it against one
name. It previously rewrote anything that was not `"vara_p2p"`, which would have
silently returned an OFDM station to VARA on every restart. A stored
`winlink_manual`, a typo, or a transport this build does not have all still fall
back to VARA — a station must never be left with no transport at all.

### Transport negotiation — the safety property

`START_VARA` keeps its numeric identity on the wire and now means "begin the
negotiated payload phase". That is only sound if there is no way for one station
to play OFDM at a peer that is listening for VARA, so both stations have to say
so. `Flags.OFDM_PAYLOAD` (bit 6 of the existing flags byte, alongside the
slow-keying field) carries the claim:

* the initiator sets it in `HAVE_MSG`;
* the responder sets it in `ACK_HAVE` **only if** it is independently configured
  the same way;
* OFDM is used only when both did. Either side alone leaves the pair on VARA.

A release that predates this never sets the bit, keeps unknown flag bits intact on
decode and simply echoes them back — so a mixed pair degrades to VARA **before
`START_VARA` is ever sent**, on the control channel, where both ends can see it.
The wire format is untouched, `VERSION` stays 1, and bit 7 remains free.

A relay announces its own configuration on the next leg rather than forwarding
what the previous hop was running, exactly as it does for the keying delay.

The agreed transport is recorded on the message and acted on by
`payload/negotiated.py`, which routes each transfer to the backend that hop
settled on. A station configured for VARA never sets the bit, so it never gets
wrapped — the path that has been on air for releases is untouched.

### Operations, PTT and the codec

`_make_payload_backend()` hands every transport the same dependency bag and each
takes what it needs, so there is no `if backend == ...` sprawl. VARA ignores the
audio devices and the PTT callable; OFDM ignores the VARA client.

`_payload_ptt` is a new sibling of `_vara_ptt`: same negotiated slow-keying tail,
because the cable and the handheld do not care which modem produced the burst. The
transport adds its own lead and tail around it, since it knows how long its
waveform is.

Keying discipline in `RadioAudioPipe.send`:

```
ptt(True) -> sleep(lead) -> play -> wait
finally:  sleep(tail) -> ptt(False)
```

The `finally` is the point. Tests cover the transmitter being released on success,
on a playback exception, on a refused QSY, on a failing codec handoff, and on a
pipe that cannot open at all. A transfer that fails costs one message; a
transmitter left keyed costs the band.

`done(ok)` runs **after** the codec has gone back to the control modem, matching
VARA hook for hook — `done()` may immediately key the radio to send a RECEIVED
frame over AFSK, and if the payload transport still owned the soundcard that frame
would never go out.

### Readiness

With `payload_backend == "ofdm_vhf"`, a VARA executable is not a blocker. What is
checked instead: the RX audio device resolves, the TX audio device resolves, the
radio/PTT is configured, and the OFDM profile is valid. The VARA installer and
readiness flow are unchanged for VARA stations.

---

## 9. What is deliberately NOT implemented

Architected for, not built:

* **The final RF bandwidth.** It comes from hardware measurement. Until then
  BENCH is a bench profile and says so everywhere.
* **Radio-specific profiles** (Quansheng, IC-705) and a 50 kHz mode. New profile
  entries when there are measurements to base them on.
* **Automatic MCS selection.** The measurements an adaptation controller needs are
  all collected (§10); nothing decides anything yet. Inventing thresholds before
  there is on-air data to fit them to would only encode a guess.
* **Per-subcarrier bit loading.** The header reserves a flags bit for announcing a
  map, and `AdaptationState` already accumulates the per-carrier channel power a
  map would be computed from.
* **LDPC.** The FEC boundary is a bit-in / LLR-out interface, so it can be
  replaced or supplemented without touching the framing.
* **A constellation/spectrum window.** `LinkMetrics.channel_response` and the
  equalised symbols are available; the drawing is not. It would have to render via
  QPainter like `spectrum_window.py` does, because the Qt binding is
  `PySide6-Essentials` with no Charts module.
* **Automatic ALE frequency selection.**
* **Cancellation of an in-flight transfer.** `cancel()` is a no-op, as it is for
  VARA. The session layer never calls it.

---

## 10. Limitations, honestly

* **Never been on the air.** Every number in this document is from simulation.
* **Over-the-air ARQ is untested.** `OfdmLink` is fully exercised against a
  simulated duplex channel and `RadioAudioPipe` is unit-tested against a fake
  sounddevice for keying and codec ordering, but the two have never run together
  against a real radio. This is the isolated remaining hardware step (§11).
* **An echo beyond the cyclic prefix breaks the link.** The guard is 2.67 ms.
  Measured with a strong 0.8 echo at 25 dB: everything up to the full guard
  decodes reliably, twice the guard essentially never does. (Three times the guard
  happens to decode again — that is geometry, not robustness, and not something to
  rely on.) Failure is always a rejection, never wrong bytes.
* **A wide notch eventually wins.** Three carriers of 52 lost to a −30 dB notch
  are recovered by the code and the interleaver. Twelve carriers at −40 dB — about
  a quarter of the band — are past what a rate-1/2 code can make up, and the block
  is rejected.
* **Sample-rate offset between two stations is designed for but barely tested.**
  The pilot phase-slope tracker is what handles it, and the simulator's ppm
  impairment exercises it up to ±20 ppm on a single burst. What is *not* tested is
  cumulative drift across a long multi-block transfer between two independent
  soundcards, because that needs two soundcards.
* **FM deviation and soundcard AGC are unknown.** They will set the usable dynamic
  range and the crest-factor budget, and they are unknowable until hardware tests.
  This is exactly why the transmit level and occupied bandwidth are profile-driven.
* **The receive squelch is a first cut.** `RadioAudioPipe` triggers on the level
  rising above a slowly tracked noise floor. It has never met a real FM squelch
  tail, a repeater's courtesy tone, or a station transmitting on the same channel.
* **EVM is decision-directed**, so it flatters a bad link: once decisions start
  being wrong, the error to the *wrong* constellation point is small. The reported
  SNR comes from the known training symbols instead, and
  `LinkMetrics.evm_source` records which method produced the EVM figure.
* **No sample-rate conversion.** A profile's sample rate must match the device's.

---

## 11. The first two-radio VHF test

Everything below can be done today; nothing else in this feature is blocking it.

**[docs/OFDM_AIR_TEST.md](OFDM_AIR_TEST.md) is the same procedure as a field sheet**,
with the readings to record, what each one means, and what to keep afterwards.
Take that one to the radio; this section is the reasoning behind it.

**Before any RF.** On one PC, confirm the bench passes and produce a reference
capture:

```powershell
python tools\ofdm_bench.py                       # a whole message, with ARQ
python tools\ofdm_bench.py --single              # one burst, in detail
python tools\ofdm_bench.py --sweep --runs 20     # decode rate against SNR
python tools\ofdm_bench.py --single --bytes 512 --write-wav reference.wav
python tools\ofdm_bench.py --read-wav reference.wav
```

**Step 1 — one radio, one receiver, no ARQ.** Set both stations to
`payload_backend = ofdm_vhf`, profile BENCH, MCS1. Transmit a burst from station A
on a VHF simplex channel. Record the receive audio at station B *as a file* (any
recorder — this step does not involve Guardian's receiver at all) and decode it
offline:

```powershell
python tools\ofdm_bench.py --read-wav capture.wav
```

This is the measurement that matters most, and it is worth doing before anything
else because it is the one that cannot be confused by a software problem. What to
record: sync confidence, measured SNR, EVM, CFO estimate, and the per-carrier
channel response. The channel response across the band is what will decide the
real occupied bandwidth.

**Step 2 — vary the waveform against the radio.** Repeat step 1 with different
transmit levels (deviation), with MCS0 and MCS2, and with the audio taken from
different points in the chain. Watch for: clipping (visible as a low crest factor
on receive), a channel response that rolls off at one end (the radio's audio
filter, which is the real bandwidth limit), and any frequency offset (should be
near zero on FM, non-zero on SSB).

**Step 3 — derive an air profile.** From the step 2 channel responses, choose
`first_carrier` and `num_carriers` to cover only the band the radio actually
passes flat, and add a new entry to `PROFILES` in `guardian/ofdm/config.py`. Do
not change BENCH; add beside it. The tests will pass unchanged because nothing
outside that file depends on BENCH's numbers.

**Step 4 — a live half-duplex exchange.** Only now enable the full path on both
stations and send a real message. What is being tested here is `RadioAudioPipe`
against reality: whether the squelch triggers on a real FM signal, whether
`ofdm_tx_lead_ms` is long enough for the transmitter to come up, whether
`ofdm_tx_tail_ms` and the tail guard cover what the USB audio path buffers, and
whether the ARQ timeouts survive a real PTT turnaround. Expect to tune the lead,
tail and turnaround; they are settings for exactly this reason.

**Step 5 — measure, then decide about adaptation.** With per-block SNR, EVM, PER
and retransmission rate logged from a real link, the MCS thresholds stop being
guesses. That is the next milestone.

---

## 12. Forward design

### Adaptive MCS

`LinkMetrics` (per burst) and `AdaptationState` (per transfer) already collect
everything the decision needs: per-carrier SNR, EVM, packet error rate,
retransmission rate, and the per-carrier channel power accumulated as a running
mean. Nothing consumes them yet.

The shape a controller should take, when there is data to fit it to:

* Decide on the **accumulated** state, not on one burst. A single bad burst moving
  the rate is the failure mode of every naive rate-control loop.
* Step **down** on retransmission rate, which is measured and unambiguous. Step
  **up** on sustained SNR margin above the next mode's requirement, and only after
  a run of clean blocks — the cost of stepping up too early is a retransmission,
  the cost of stepping down too late is a failed transfer.
* Use asymmetric hysteresis: quick to retreat, slow to advance.
* The ~5 dB steps in §6 are the starting point for the thresholds, but they are
  simulation figures. Real ones will differ, and the table is the only thing that
  should need changing.

Nothing in the frame format has to change: the header already carries the data
MCS, so the transmitter can change mode on any burst and the receiver follows.

### Per-subcarrier bit loading

The eventual goal is different modulation orders on different carriers
simultaneously, with interference-hit carriers disabled. What is already in place:
`AdaptationState.carrier_power` and `worst_carriers` are the input a map would be
computed from, and the header reserves a flags bit to announce that a map is
present. What a map needs in addition: a compact encoding in the burst (a few bits
per carrier, itself sent at MCS0), and a per-carrier variant of `encode_section` /
`decode_section`. The interleaver and the FEC boundary need no changes.

### Fuller transport negotiation

One flag bit is enough while there are exactly two transports. Negotiating
several, or their parameters, wants the `WORKING_OFFER` / `WORKING_ACK` token-frame
pattern that the working-channel negotiation already uses — those frame types are
rejected by older releases, so they create a safe capability gap by construction.

---

## 13. Running it

```powershell
# The whole physical layer, no radio and no Qt involved. About 45 seconds.
python -m pytest tests\test_ofdm_constellation.py tests\test_ofdm_phy.py `
                 tests\test_ofdm_sync.py tests\test_ofdm_channel.py `
                 tests\test_ofdm_link.py tests\test_ofdm_payload.py -q

# Measurements
python tools\ofdm_bench.py --help
```

`tools/ofdm_bench.py` reports the profile, sample rate, FFT size, occupied
bandwidth, data carriers, modulation, coded and information bits per symbol,
waveform duration, crest factor, measured SNR, EVM, CFO, pilot phase slope,
channel-response spread, the uncoded theory curve for comparison, retries, and
whether the payload came back identical.

`--write-wav` and `--read-wav` are what make it useful once there are radios:
record what a receiver actually hears, hand the file to `--read-wav`, and every
measurement describes the real channel instead of a simulated one.

**Guardian records those files itself** (`guardian/modem/recorder.py`), so an
on-air session needs nothing but Guardian and a radio. Captures land in
`config_dir()/captures/` as mono 16-bit PCM at the audio path's own rate, which is
exactly what `--read-wav` expects — there is no export or resample step in which a
good capture can be turned into a misleading one. When the control channel is open
its receive stream is tapped, so the capture is exactly the audio the modem is
working from; when it is closed, a stream of its own is opened. Nothing is
normalised, trimmed or filtered on the way out, because the silence around a burst
is the noise floor a squelch is measured against and the absolute level is how a
clipping radio is told apart from a quiet one.
