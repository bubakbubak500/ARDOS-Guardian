# Guardian G2 adaptive OFDM and selective-repeat ARQ

This is the implementation note for OFDM frame format 2. The change extends the
working G2 modem; it does not replace its waveform or widen its occupied band.
It was driven by the 2026-08-09 IC-705 results: Guardian's raw PHY was close to
VARA's, but one PTT/turnaround/ACK cycle per 512-byte block made a 2471-byte
payload take roughly 25–33 seconds instead of VARA's roughly 14-second payload
phase.

The optimization target is useful application bytes per second, not a prettier
nominal bitrate.

## Reused architecture

The implementation deliberately keeps these proven components:

- `OfdmModulator` and `BurstReceiver`: DMT waveform, synchronization, training,
  pilots, equalization, and soft carrier information.
- The K=7, octal 171/133 convolutional encoder and soft Viterbi decoder in
  `guardian/modem/fec.py`.
- The 16-byte BPSK/rate-1/2 bootstrap header, interleaver, and CRC-16.
- `HalfDuplexPipe` and `RadioAudioPipe`, so simulation and real PTT/audio use the
  same link state machine.
- Message ID and global block sequence for exactly-once assembly.
- The 2.0.5 completion linger that repairs a lost final acknowledgement.

New responsibilities are isolated in `guardian/ofdm/coding.py` (FEC profiles),
`guardian/ofdm/adaptation.py` (one joint controller), `framing.py` (wire format),
and `link.py` (selective-repeat state).

## Frame format 2

The waveform preamble, training, carrier allocation, cyclic prefix, and occupied
bandwidth are unchanged.

```
[preamble + training]
[robust header: message, burst, total blocks, MCS, FEC, bytes, count]
[robust manifest: (global block number, valid length)... + CRC]
[independently FEC-coded block 0 + CRC]
[independently FEC-coded block 1 + CRC]
...
```

The header is still 16 bytes before coding:

| Field | Size | Format-2 meaning |
|---|---:|---|
| version | 1 B | `2` (`1` remains decodable) |
| frame type | 1 B | DATA, ACK, or NACK |
| message ID | 4 B | Guardian message identity |
| burst ID | 2 B | wraps modulo 65536; only one burst is outstanding |
| total blocks | 2 B | stable global block count for the message |
| scheme | 1 B | high 3 bits FEC ID, low 5 bits MCS ID |
| payload bytes | 2 B | valid application bytes in this keyed burst |
| flags | 1 B | 1–32 subblocks, retransmission marker, one reserved bit |
| header CRC | 2 B | CRC-16/CCITT-FALSE over the first 14 bytes |

The manifest is BPSK/rate-1/2 and contains one four-byte `(sequence, length)`
entry per member plus CRC-16. Each following block is separately punctured,
interleaved, decoded, and CRC checked. A failed block is discarded without
discarding its verified neighbours. A malformed header or manifest delivers no
application data.

ARQ subblocks may be 256, 512, or 1024 bytes. The default is 512. A final block
may be shorter; its valid length is in the manifest, so no padding reaches the
application. A keyed burst contains at most 32 blocks, which covers a 16 KiB
burst at the default block size.

Version-1 single-block frames remain decodable. The fixed legacy setting sends
version 1 with 512-byte stop-and-wait behavior for controlled comparison. Format
2 is explicitly versioned and an incompatible receiver rejects it rather than
guessing. Until a sub-version negotiation is added, both stations should run the
same G2 release; use legacy mode on both ends when comparing with 2.0.x.

## FEC profiles

The mother code is unchanged. Faster rates puncture its serialized output and
the receiver restores omitted positions as zero-confidence erasures before the
same soft Viterbi decoder.

| ID | Rate | Repeating transmit mask |
|---:|---:|---|
| 0 | 1/2 | `11` |
| 1 | 2/3 | `1110` |
| 2 | 3/4 | `111001` |
| 3 | 5/6 | `1110011001` |
| 4 | 7/8 | `11100110011001` |

The robust header, manifest, and ACK/control payload never depend on the selected
high-rate data FEC. The receiver always reads the FEC ID from the header; it does
not infer a rate from length or signal quality.

For 512 application bytes, including the two-byte subblock CRC and trellis
termination, the transmitted coded lengths are calculated rather than assumed.
The benchmark reports information bits, encoded bits, and the resulting exact
effective rate.

## Selective-repeat exchange

The sender splits the message once into globally numbered subblocks. It groups as
many as the selected burst target permits, keys once, and waits for one compact
bitmap. The bitmap contains total block count, received block bits, remote
post-equalization SNR in half-dB units, remote EVM, and a payload CRC protected by
the robust control path.

On a partial result the receiver keeps every CRC-verified block. The sender
removes those identities from `pending` and transmits only the missing members.
The first retry uses one stronger FEC rung, the second two stronger rungs, down to
rate 1/2. Retry limits remain bounded by `ofdm_max_retries`.

A duplicate is acknowledged again but never appended twice. Late or unrelated
ACKs are rejected by message ID, burst ID, and total-block count. After complete
assembly the receiver lingers for repeated data and answers with the complete
bitmap, preserving the lost-final-ACK repair from 2.0.5.

ACK and receive deadlines are calculated from the actual control/data waveform
duration plus measured/configured turnaround and processing margin. The optional
timeout multiplier scales that derived value; it is not a replacement fixed
timeout.

## Joint adaptation

`LinkAdaptationController` owns FEC and keyed-burst length so two independent
loops cannot fight.

- New AUTO links start at FEC 1/2 and a 2048-byte burst.
- Three completely clean burst results are required for an upgrade.
- Upgrades alternate: faster FEC, then longer burst, one rung at a time.
- Any partial/failed bitmap immediately moves toward robustness: first one
  stronger FEC rung; when already at 1/2, one shorter burst rung.
- Burst rungs are 256, 512, 1024, 2048, 4096, 8192, and 16384 bytes. A burst may
  not be smaller than one configured ARQ block.
- The controller retains EWMAs for success ratio, retry ratio, modeled goodput,
  and remote receive SNR/EVM. Delivery evidence drives the initial policy; no
  uncalibrated absolute SNR threshold is used to step up.
- FEC and burst dimensions may each independently be AUTO or FIXED.

Modulation remains fixed at the operator-selected MCS. In particular, this work
does not add or promote 64-QAM. The profile object keeps MCS beside FEC so later
measured modulation adaptation can use the same controller boundary.

## Configuration and UI

`StationConfig` stores:

```text
ofdm_adaptive_fec       true | false
ofdm_fec                1/2 | 2/3 | 3/4 | 5/6 | 7/8
ofdm_adaptive_burst     true | false
ofdm_burst_bytes        256..16384 on the burst ladder
ofdm_min_burst_bytes    256..16384 on the burst ladder
ofdm_max_burst_bytes    256..16384 on the burst ladder
ofdm_arq_block_bytes    256 | 512 | 1024
ofdm_timeout_multiplier 0.5..4.0
ofdm_legacy_mode        true | false
```

Station settings expose all of these and reject a minimum/fixed burst smaller
than one ARQ block. The live transfer panel shows selected FEC, burst and ARQ
sizes, first-pass block results, retries/retransmitted bytes, and measured
wall-clock goodput. The Modem test workspace can select AUTO or a fixed FEC/burst
for deterministic comparisons.

## Measurements and efficiency report

Status and benchmark results include unique bytes acknowledged, retransmitted
bytes, logical protocol-overhead bytes, data/ACK burst counts, data and control
airtime, turnaround time, local receive SNR/EVM, remote SNR/EVM from the ACK,
and application goodput. Retransmitted bytes are not counted as newly delivered
bytes.

The CLI and in-app benchmark share `guardian/ofdm/bench.py`. Example:

```powershell
python tools\ofdm_bench.py --profile BENCH --mcs 2 --fec 3/4 `
  --burst 8192 --arq-block 512 --bytes 16384 --snr 18

python tools\ofdm_bench.py --profile BENCH --mcs 2 --auto-fec --auto-burst `
  --min-burst 512 --max-burst 16384 --bytes 32768 --snr 18
```

The report separates constellation/data-carrier rate, FEC-adjusted rate,
protocol payload rate after data/ACK airtime, modeled application goodput after
turnaround, retransmission loss, and turnaround loss. Simulation uses modeled
channel occupancy because CPU wall time is not radio time; the real backend also
records elapsed wall time in its live status.

## Validation and first on-air tuning

Automated tests cover all five clean codec round trips, all FEC identifiers in
the robust header, three independently decoded blocks, 1024-byte ARQ blocks,
manifest and payload corruption rejection, variable ACK bitmaps, partial retry,
duplicate suppression, final-ACK loss, fixed and legacy operation, hysteresis,
timeout scaling, padding/short messages, and end-to-end byte equality.

The software cannot choose final thresholds without another two-radio run. On
the first IC-705 test of this release, hold MCS1/QPSK and compare these fixed
profiles before enabling AUTO:

1. FEC 1/2, 3/4, 5/6, and 7/8 at a fixed 4096-byte burst.
2. Fixed 2048, 4096, and 8192-byte bursts at the best reliable FEC.
3. Record first-pass bitmap success, remote link SNR/EVM, retransmitted bytes,
   total elapsed time, and the efficiency report for the same file each time.
4. Repeat with one deliberately degraded condition and confirm only missing
   512-byte blocks are repeated with stronger FEC.
5. Tune `clean_bursts_to_upgrade`, burst bounds, and (only if measured radio
   turnaround requires it) the timeout multiplier from those results.

The practical target is improvement over the 25–33 second 2471-byte baseline.
The 7–8 kbit/s aspiration in the original design prompt is not achievable on the
current BENCH/QPSK physical rate and must never be hard-coded or claimed; the
efficiency report makes the remaining physical limit explicit.
