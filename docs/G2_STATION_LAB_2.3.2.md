# Guardian G2 Station Lab — 2.3.2

## Purpose

Guardian can now measure the complete path instead of guessing a useful audio
level from a local waveform. One operator starts **Operation → Station test &
AutoTune**, enters the peer callsign and selects Quick Tune or Full radio
characterizer. The peer must explicitly accept unless that callsign is in its
local auto-accept allowlist. Measuring traffic is directed and bounded; normal
ARDOS mail and control-session semantics are unchanged.

The receiver reports frame/header validity, SNR, EVM, sync confidence, audio
peak, clipping and frequency offset. Guardian stores every raw point in JSON and
CSV and scores only points that are both byte-valid and safe. It recommends the
lowest drive within 0.5 dB of the best eligible goodput, preserving headroom
instead of blindly selecting the loudest point. Nothing changes the permanent
configuration until the operator reviews the report and presses **Apply measured
profile**.

## Windows volume and FM deviation

For a dedicated radio audio endpoint, Windows output volume can be part of the
sweep because it may directly determine radio input level and therefore FM
deviation. Quick Tune deliberately compares two endpoint levels first. If the
remote peak moves by at least 2 dB, the mixer is effective and remains part of
the search. If it does not, Guardian records that the path bypasses that control
and varies only the modem's digital drive.

Before changing the endpoint Guardian snapshots endpoint and session state and
arms a recovery journal. The original state is restored after completion,
cancellation, timeout or error, and an interrupted journal is restored at the
next start when the endpoint is available. The applied endpoint value is always
visible and editable in the final report.

This is an on-air calibration aid, not a spectrum analyser. A remote demodulator
can reveal clipping and useful decoded-audio quality, but cannot certify
adjacent-channel power or legal RF occupied bandwidth. Final deviation and RF
mask still require the radio's meter, service monitor or SDR measurement.

## Fast selective repeat

Version 2.3.2 optionally places several independently framed and CRC-protected
microbursts under one PTT. The receiver scans all valid preambles, keeps every
valid block and returns one cumulative ACK. A compact sparse missing-list is
used when it is shorter than the bitmap. If the final microburst or cumulative
ACK disappears, the sender transmits a robust POLL and retransmits only blocks
still reported missing.

The configured maximum continuous train time limits long key-down periods. The
default is still one microburst per PTT, which preserves interoperability with
older Guardian G2 peers. Set two to eight only when both stations run 2.3.2.

### Reproducible model results

The seeded clean-channel comparison used SC-FTN MCS6, FEC 7/8 and identical
payload/turnaround settings:

| Payload | Train | DATA PTT | ACK PTT | Channel time | Goodput |
|---:|---:|---:|---:|---:|---:|
| 8 KiB | 1 | — | — | 10.629 s | 6,166 bit/s |
| 8 KiB | 4 | — | — | 8.359 s | 7,840 bit/s |
| 64 KiB | 1 | 8 | 8 | 61.386 s | 8,541 bit/s |
| 64 KiB | 4 requested, 2 allowed by time cap | 4 | 4 | 57.880 s | 9,058 bit/s |

The 8 KiB gain is 27.1%; the 64 KiB gain is 6.1%. The latter is smaller because
long payload bursts already amortize turnaround and the continuous-PTT cap
limits the effective train. These are deterministic software-model results.

## Truthful timing and FM model

TX metrics now distinguish lead, waveform, guard, tail, total keyed time and
wall time. RX metrics distinguish trigger wait, capture, hangover and decode.
The transfer panel reports application speed separately from PHY payload rate,
channel goodput, TX duty and PTT count.

The new end-to-end FM model is intentionally separate from the older linear
audio stress model. It provides TX/RX audio filters, optional pre/de-emphasis,
limiting and deviation, complex RF FM, RF AWGN and multipath, discriminator,
sample-clock error, AGC and receive clipping. Results identify the model as
`end-to-end-fm`; they must not be presented as on-air measurements.

## First two-radio procedure

1. Install 2.3.2 on both stations and select the same G2 waveform/profile.
2. Use a clear simplex channel and legal power/duty limits. Verify PTT lead and
   tail first; the IC-705/USB path may need roughly 300 ms.
3. Start the control channel at both ends. At one station open **Operation →
   Station test & AutoTune**, enter the other callsign and run Quick Tune.
4. Enable Windows tuning only for the dedicated radio output. Accept the offer
   at the peer, watch both directions complete, then inspect raw failed/clipped
   rows before applying anything.
5. Repeat in the reverse initiating direction and keep both JSON/CSV reports.
6. For a fair VARA FM Narrow comparison, send the same incompressible 8, 64 and
   256 KiB payloads at least five times in each direction, with matching radio,
   power, frequency and PTT/AOIC timing. Compare median and p95 wall-clock
   application goodput, completion rate, retransmitted bytes and PTT count.

## Deliberate limits

- Full Characterizer explores the implemented waveform/MCS frontier; it does
  not yet perform external RF-mask certification or adaptive per-carrier bit
  loading.
- Incremental-redundancy HARQ is not in 2.3.2. Selective repeat retransmits an
  independently protected missing microburst; adding real RV/soft-combining
  requires an explicitly versioned header and receiver state.
- Adaptive train length is not negotiated yet. Keep the default for mixed
  versions and increase it deliberately only for a matched 2.3.2 pair.
- The acceptance claim against VARA remains open until the same two radios run
  the controlled A/B matrix above.
