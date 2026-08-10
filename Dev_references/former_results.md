❯ side question, how is the MSC2 more faster in kbps than MSC1 it seem si si always split to the same 5 block for example in the 5k attachment, only theoretically, are we missing something we would in mean time test the load...

Good question, and there is something worth seeing here. Let me compute it exactly rather than reason from the constellation ratio:

  Ran 1 shell command

Nothing is broken — but you've spotted the real limit, and it isn't the constellation.

The block count never depends on MCS. It's ceil(wire_bytes / 512), full stop. MCS changes how long each block takes on air, not how many there are. So a 5 kB attachment is 10 blocks at MCS0, MCS1, MCS2 and MCS3 alike. (Your "5 blocks" is the compressed wire size — the log showed a 3986 B attachment going out as 1619 bytes, 4 blocks.)

MCS2 really does halve the data section. What it can't touch is everything wrapped around it:

┌──────┬──────────────┬────────┬───────────┬──────────────────┬──────────┐
│      │ data symbols │ burst  │ per block │    delivered     │ PHY rate │
├──────┼──────────────┼────────┼───────────┼──────────────────┼──────────┤
│ MCS0 │ 188          │ 4.78 s │ 7.82 s    │ 524 bps          │ 917 bps  │
├──────┼──────────────┼────────┼───────────┼──────────────────┼──────────┤
│ MCS1 │ 94           │ 2.52 s │ 5.56 s    │ 736 bps          │ 1833 bps │
├──────┼──────────────┼────────┼───────────┼──────────────────┼──────────┤
│ MCS2 │ 47           │ 1.39 s │ 4.44 s    │ 923 bps (1.25×)  │ 3667 bps │
├──────┼──────────────┼────────┼───────────┼──────────────────┼──────────┤
│ MCS3 │ 32           │ 1.03 s │ 4.08 s    │ 1005 bps (1.37×) │ 5500 bps │
└──────┴──────────────┴────────┴───────────┴──────────────────┴──────────┘

3.04 seconds of every block is fixed overhead that no MCS touches: preamble + training (4 symbols), the header (7 symbols, always sent at MCS0 by design so a receiver can read it without knowing the payload modulation), PTT keying 0.8 s on each of the two transmissions, 0.59 s turnaround each way, and the 0.264 s ACK.

So at MCS1 you're 55% overhead; at MCS3 the data section is 1.03 s against 3.04 s of overhead — 75%. Tripling the raw bit rate from MCS1 to MCS3 buys 1.37×. Above MCS1 this link is overhead-bound, not modulation-bound.

Two things worth knowing before you load-test

MCS2 would probably be slower than MCS1 on your path. Break-even is 80% first-try success. Your measured link SNR is 9.7–12.0 dB against MCS2's 10.5 dB threshold — right on it, so first-try success will be well under 80%, and every retransmission costs a full 4.44 s. A block that needs two attempts at MCS2 is slower than one attempt at MCS1.

The lever you're actually looking for is block size, not MCS — and unlike a faster constellation it costs no SNR at all:

┌─────────────┬──────────┬──────────┬───────────┐
│ block_size  │   MCS1   │   MCS2   │ MCS2 gain │
├─────────────┼──────────┼──────────┼───────────┤
│ 512 B (now) │ 736 bps  │ 923 bps  │ 1.25×     │
├─────────────┼──────────┼──────────┼───────────┤
│ 1024 B      │ 1051 bps │ 1472 bps │ 1.40×     │
├─────────────┼──────────┼──────────┼───────────┤
│ 2048 B      │ 1336 bps │ 2102 bps │ 1.57×     │
├─────────────┼──────────┼──────────┼───────────┤
│ 4096 B      │ 1545 bps │ 2673 bps │ 1.73×     │
└─────────────┴──────────┴──────────┴───────────┘

2048-byte blocks would make MCS1 alone 1.8× faster than it is today, at the SNR you already have. The catch is the obvious one: a failed block costs four times as much to redo, so there's an optimum that depends on your block error rate — which is exactly what the load test will measure. It's a profile parameter, so both stations would have to run the same build.

Two caveats on the table: the keying and turnaround figures are the configured defaults (300 ms lead, 100 ms tail, 400 ms guard), so your real numbers will differ — the log's own est_bitrate_bps measures the truth. And this is airtime only; it doesn't include the AFSK control handshake either side of the transfer.

✻ Crunched for 1m 31s

❯ how is vara than achieving almost 9k on same radio using MSC2?! theoretically...

Worth pulling apart, because the comparison isn't like-for-like and the residue is genuinely informative.

  Ran 1 shell command

Three separate factors, and the leftover is the interesting part.

First, that's not a like-for-like number

VARA's "9k" is almost certainly its reported speed level — a PHY rate. The 923 bps I quoted you is Guardian delivered, after every byte of overhead. Guardian's PHY at MCS2 is 3667 bps.

So the honest PHY comparison is 9000 vs 3667 — about 2.5×, not 10×.

Where the 2.5× goes: Guardian is locked at rate 1/2

Same waveform, same 2.4 kHz, just varying the code rate:

┌────────┬──────────────┬───────┬───────┐
│        │    r=1/2     │ r=3/4 │ r=7/8 │
├────────┼──────────────┼───────┼───────┤
│ QPSK   │ 1833         │ 2750  │ 3208  │
├────────┼──────────────┼───────┼───────┤
│ 16-QAM │ 3667 ← today │ 5500  │ 6417  │
├────────┼──────────────┼───────┼───────┤
│ 64-QAM │ 5500         │ 8250  │ 9625  │
└────────┴──────────────┴───────┴───────┘

Guardian only has the left-hand column. MCS_TABLE is four constellations at a single fixed rate, because guardian.modem.fec provides one rate — that's called out in the code as a Phase 1 limitation. The same BENCH waveform at 64-QAM r=3/4 is 8250 bps PHY with no extra bandwidth. The waveform is not what's holding you at 3667.

Where the other 4× goes: one PTT cycle per 512 bytes

Guardian keys the transmitter, sends one block, unkeys, waits for an ACK, and repeats. 3.04 s of that cycle is fixed. VARA keys once and streams many frames under a windowed ACK, so it pays keying and turnaround once per over instead of once per block.

That's a deliberate Guardian choice — link.py says so explicitly: no window, nothing in flight to reason about when a burst is lost. It's the right call for robustness and it costs three quarters of the throughput.

The part you should actually care about

9000 bps in roughly 2.7 kHz is 3.3 bit/s/Hz. Shannon puts the floor for that at ~9.6 dB, and a real modem with strong FEC runs a few dB above it — call it 12–15 dB on that path.

Guardian measured 9.7–12.0 dB on the same radios, the same USB audio path, the same afternoon.

If VARA is genuinely sustaining 9k there, the path is several dB better than Guardian is managing to extract from it, and a real chunk of that 8 dB gap is ours, not the IC-705's. Plausible places we're losing it, all of which VARA has solved and we haven't:

- Channel estimated once from two training symbols and used unchanged for up to 4 seconds. VARA tracks continuously.
- Zero-forcing equalisation, which amplifies noise on weak carriers — and BENCH showed 6–9 dB of spread across the band. MMSE would recover part of this.
- No per-subcarrier bit loading. Every carrier runs the same constellation, so your worst carrier sets the block error rate. With 6–9 dB of spread that is expensive, and it's exactly what bit loading exists for. docs/ofdm-vhf.md already lists it as future work.
- Two training symbols means the channel estimate itself carries about 1.8 dB of noise into every decision.

What I don't know, and the test that settles it

I can't tell from here whether your 9k is a reported speed level or measured bytes-per-second, or whether VARA FM is running narrow or wide — those change the arithmetic materially. And VARA is closed-source, so all of the above is inference from spectral efficiency, not knowledge of its internals.

The measurement that would settle it: run a real VARA transfer and record both its reported speed level and the actual file size ÷ wall clock. Then, in the same session on the same path, run Guardian's modem test and note the link SNR. If VARA is delivering ~3 bit/s/Hz while Guardian reads 10 dB, we have a receiver problem worth chasing — and MMSE plus bit loading is where I'd start. If VARA's delivered throughput turns out to be much closer to 2–3 kbps, then the two modems agree about the channel and the honest answer is that we need the streaming ARQ and higher code rates, not a better receiver.

That's a cheap test and it decides which of two quite different work programmes is worth doing.

✻ Worked for 1m 57s

※ recap: Goal was analysing your OFDM air-test results and shipping fixes: that's done, committed and pushed to the G2 repo as 2.0.5, installer at release\Guardian-G2-2.0.5-setup-win-x64.exe. Next: install it on both stations and run the drive-level sweep. (disable recaps in /config)

❯ check the C:\Users\user\Documents\Guardian\Recordings, same file over guardian and vara... alsomst 7k on VARA, only theoretically now, dont change any diles we might do it later... i have test analysis recordings of MSC2/MSC3 and others on bench as well.

  Ran 2 shell commands

This is a much better data point than the earlier session. Let me read the Guardian side of the same file.

  Listed 1 directory, ran 3 shell commands

The captures are still there. Let me test something specific — whether that error is actually noise:

  Ran 1 shell command

Gain error is only ~1 dB of it, so that's not the answer. Let me measure the thing that would actually explain it — the channel's impulse response length against the guard interval:

  Ran 1 shell command

Delay spread is mostly inside the guard, so that's not it either. One more — the distribution across carriers is where I saw a huge spread earlier:

  Ran 2 shell commands

Your logs settle it, and they overturn part of what I told you earlier. Everything below is from your four files plus the captures still sitting in %APPDATA%\Guardian-G2\captures\. No files touched.

Same file, same radios, 27 minutes apart

┌───────────────┬────────────┬─────────────────────┬────────────┬───────────┐
│               │ wire bytes │    payload phase    │ end-to-end │ delivered │
├───────────────┼────────────┼─────────────────────┼────────────┼───────────┤
│ Guardian MCS0 │ 2457       │ 36 s                │ —          │ 546 bps   │
├───────────────┼────────────┼─────────────────────┼────────────┼───────────┤
│ Guardian MCS1 │ 2458       │ 25 s                │ 56 s       │ 787 bps   │
├───────────────┼────────────┼─────────────────────┼────────────┼───────────┤
│ Guardian MCS2 │ 2464       │ 33 s (1 retransmit) │ 55 s       │ 597 bps   │
├───────────────┼────────────┼─────────────────────┼────────────┼───────────┤
│ VARA          │ 2471       │ 14 s                │ 21 s       │ 1412 bps  │
└───────────────┴────────────┴─────────────────────┴────────────┴───────────┘

MCS2 came out slower than MCS1 — 33 s against 25 s, with one retransmission out of six attempts. That's the 80% break-even I predicted last message, landing almost exactly on it.

The surprise: VARA's PHY is barely faster than yours

VARA's data went out in one 5-second over at speed level 7. 2471 bytes in 5 s = 3954 bps of net payload through the air.

Guardian's MCS2 PHY is 3667 bps.

Those are the same number. The two modems are extracting essentially the same thing from that channel. The 7087 bps label is VARA's speed level before its own framing and FEC — its net rate is 3954, and yours would be comparable if you could keep the transmitter running.

So the answer to "how is VARA doing 7k" is: it isn't. It's doing ~4k net, and so could you.

Where the 2.7× actually goes: one PTT cycle per 512 bytes

VARA sent 2471 bytes in one transmission. Guardian sent 2464 bytes in five, each carrying its own preamble, training block, MCS0 header, 0.8 s of keying, a turnaround, and an ACK burst coming back.

Measured from your logs: Guardian spent 7 s of its 33 s actually transmitting data (21%). VARA spent 5 s of its 14 s link (36%), and none of Guardian's five turnarounds.

Essentially the whole gap is the stop-and-wait ARQ, not the physical layer.

Two hypotheses of mine that your data kills

Bit loading would buy almost nothing. I suggested it last message; I was wrong. Your per-carrier SNRs run 16.2 dB at best to 3.5 dB at worst — a 12 dB spread, which looks like a textbook case for it. But computing the water-filled capacity against a uniform constellation at the same aggregate gives 0.95×. The interleaver and the rate-1/2 Viterbi already average across carriers, so Guardian is not paced by its worst carrier the way an uncoded system would be. Bit loading solves a problem you don't have.

The distortion isn't a wall in the radio. VARA's own SN readings were 17.4–24.4 dB — which agrees with Guardian's noise-only figure (17.6–22.6 dB) and not with its post-equalisation 10.9–11.1 dB. Two independent modems agree the channel is ~18–22 dB. Guardian's demapper only sees 11.

I tested and rejected three explanations for that 7–11 dB: a systematic gain error (real, but only ~1 dB — the equaliser runs 5–14% hot and 2–3° rotated), excess delay spread (96% of channel energy is inside the 2.67 ms guard), and clipping. What's left is zero-forcing on a sloped channel: 6–9 dB of amplitude spread across the band becomes 12 dB of post-equalisation SNR spread, because ZF divides by the channel and multiplies the noise on every weak carrier. MMSE doesn't do that. That's where the headroom is, and it's a contained change to _equalise.

What this says the work is, in order

1. Send more than one block per over. Everything else is a rounding error next to this. Five blocks in one transmission with one aggregated ACK would take Guardian's 25 s to roughly 15 s with no SNR required and no new modulation.
2. Bigger blocks — same lever, cheaper to implement, and the numbers I gave you last message still stand (2048 B ≈ 1.8× at MCS1 alone).
3. MMSE instead of zero-forcing. The one receiver-side change the data actually supports.
4. Rate 3/4 coding. 16-QAM r=3/4 is 5500 bps PHY, which your measured aggregate would roughly carry.
5. Not bit loading. Not MCS3.

Worth noting: 2.0.5 earned its keep in these logs

All four Guardian transfers completed, including the 5 kB attachment that failed outright this morning. Specifically, at 20:18:53 the sender logged no answer to block 0 (1 burst(s) heard, none usable: header rejected: header CRC 0x0d11...) — the new diagnostic, telling you the ACK was heard and didn't decode, so it wasn't the squelch. And at the far end, block 0 is a duplicate, re-acking — the linger catching exactly the failure that lost you #270532609. That transfer then finished 5/5 instead of failing.

✻ Baked for 6m 40s
