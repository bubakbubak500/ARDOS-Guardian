# ARDOS Guardian G2 — Adaptive FEC + Adaptive Burst Length + Selective Repeat ARQ

NOTE!!!! This prompt was written in isolation, you have to check all its assumptions agains real code and review its applicable assumptions against reality!!!

We are extending the existing ARDOS Guardian G2 experimental OFDM modem.

The primary goal of this task is to substantially increase real payload throughput / goodput over a narrow approximately 2.7 kHz radio channel, while preserving the ability to fall back to robust operation on weaker or degraded links.

The current implementation already has an OFDM physical layer and currently appears to use a fixed FEC configuration, approximately rate 1/2, and a fixed payload/burst size of approximately 500 bytes.

Do NOT rewrite the modem from scratch.

First inspect the existing repository and understand the current architecture, data flow, framing, OFDM symbol generation, FEC implementation, packetization, ACK/ARQ logic, timing, state machine, GUI/configuration, tests, and logging.

Then implement the features described below incrementally and cleanly.

The two major features are:

1. Adaptive FEC / coding rate
2. Adaptive TX burst length with sub-block selective-repeat ARQ

These two systems must be designed to work together.

---

# 1. Overall design goal

Guardian G2 should no longer operate as:

fixed modulation
+
fixed FEC 1/2
+
fixed 500-byte transfer
+
ACK
+
next transfer

Instead it should evolve toward:

adaptive modulation/coding profile
+
adaptive burst length
+
multiple independently recoverable sub-blocks per burst
+
compact ACK/NACK bitmap
+
retransmission of only failed sub-blocks

The final system should be optimized for maximum useful bytes per second rather than raw PHY bitrate alone.

Important constraints:

* Radio channel usable bandwidth is approximately 2.7 kHz.
* Current practical modulation ceiling through the IC-705 path appears to be 16-QAM.
* 64-QAM is currently unreliable and is NOT part of this task.
* Do not increase RF/audio occupied bandwidth in this task.
* Existing OFDM waveform should be preserved unless a change is directly necessary.
* Backwards compatibility with existing G2 behavior should be retained where practical.
* A safe fixed legacy mode should remain available for debugging.

The implementation must be modular enough that future work can add:

* per-subcarrier adaptive modulation / bit loading
* different FEC algorithms such as LDPC
* wider-band radios
* additional MCS profiles

But do not implement those unrelated changes unless required by the architecture.

---

# 2. Inspect the existing implementation first

Before modifying code, identify and document internally:

* OFDM TX entry point
* OFDM RX entry point
* frame structure
* packet/frame header
* current payload size limit
* current 500-byte behavior
* current FEC encoder
* current FEC decoder
* exact FEC scheme currently used
* whether puncturing is already supported
* interleaving, if any
* CRC placement
* current ACK mechanism
* current retransmission mechanism
* PTT / RX-TX turnaround handling
* sequence numbering
* link/session state
* SNR estimation
* BER estimation, if present
* EVM estimation, if present
* FEC correction/error counters, if available
* packet error / CRC error statistics
* timeout handling
* transfer state machine
* configuration storage
* GUI controls related to modem parameters
* automated/unit/integration tests

Do not assume names or architecture.

Use the repository as source of truth.

Before making large structural changes, create a concise implementation note in the code or development documentation describing what existing components will be reused.

---

# 3. Adaptive FEC

The current fixed FEC configuration wastes a large amount of capacity on a clean VHF link.

We need multiple coding rates.

Target initial coding rates:

* 1/2
* 2/3
* 3/4
* 5/6
* 7/8

If the existing codec cannot reasonably support all of these directly, determine whether puncturing can be used.

Prefer reusing the current encoder/decoder if technically sound.

Do NOT replace the existing FEC algorithm merely for architectural elegance.

If the current codec is convolutional rate 1/2, investigate implementing the higher rates through puncturing.

Possible conceptual profiles:

FEC_1_2
FEC_2_3
FEC_3_4
FEC_5_6
FEC_7_8

Use explicit enums/constants rather than magic numbers.

Every encoded frame must tell the receiver which FEC rate was used.

The receiver must NEVER have to guess the coding rate.

---

# 4. FEC metadata

Extend the frame/header format in a version-safe manner.

The PHY/MAC header needs enough information to identify at least:

* protocol/frame format version
* frame type
* session/link identifier if currently used
* burst sequence number
* sub-block count
* payload length
* FEC profile/rate
* possibly modulation/MCS ID if one already exists
* retransmission indication if useful
* header CRC

The robust header itself should NOT depend on the high-rate FEC mode used for the payload.

The receiver must be able to decode enough header information to know how to decode the payload.

If the existing architecture already provides a robust base/header modulation/FEC, reuse it.

Do not make the payload coding rate implicit.

---

# 5. FEC rate mathematics

Add utility functions that explicitly calculate:

* information bits
* encoded bits
* coding overhead
* effective coding rate
* expected PHY payload throughput

Avoid using nominal assumptions when actual puncturing/padding changes the exact number of transmitted bits.

Logging should be able to show something equivalent to:

FEC 1/2
payload bits: 32768
encoded bits: 65536
effective rate: 0.500

or:

FEC 7/8
payload bits: 32768
encoded bits: XXXXX
effective rate: 0.87X

Use actual values produced by the encoder.

---

# 6. Adaptive FEC controller

Create a dedicated link adaptation component rather than scattering thresholds throughout TX/RX code.

For example conceptually:

LinkAdaptationController

It should maintain recent link statistics and select an appropriate FEC profile.

Inputs should use whatever reliable metrics Guardian already has, preferably a combination of:

* SNR
* EVM
* CRC success/failure
* FEC decoding success
* corrected-error count
* packet error rate
* retransmission rate
* ACK/NACK results
* moving goodput
* possibly RSSI only as secondary information

Do not depend on RSSI alone.

Quality history should use a moving window / EWMA so that a single packet does not cause wild oscillation.

---

# 7. FEC adaptation behavior

Use hysteresis.

The modem should move toward faster coding cautiously and toward stronger coding quickly.

Example concept:

GOOD link:
1/2 -> 2/3 -> 3/4 -> 5/6 -> 7/8

DEGRADING link:
7/8 -> 5/6 -> 3/4 -> 2/3 -> 1/2

Rules should initially be conservative.

Avoid rapid profile bouncing.

Possible strategy:

Upgrade coding rate only after:

* N consecutive successful blocks or
* a sufficiently high recent success ratio
* low retransmission rate
* sufficient quality margin

Downgrade immediately or very quickly after:

* repeated CRC failures
* high NACK ratio
* decoder failure
* strong EVM/SNR deterioration

Do not hard-code thresholds deeply inside transport logic.

Put adaptation thresholds into one configuration structure.

---

# 8. FEC mode selection API

Create a clean API such as conceptually:

select_fec_profile(link_metrics)

or:

adaptation.current_profile

and:

adaptation.report_tx_result(...)
adaptation.report_rx_result(...)

Exact implementation should follow repository style.

The TX path should ask the adaptation layer what profile to use.

The RX path should report observed results back into link statistics.

---

# 9. Fixed/debug FEC mode

Support two operating modes:

AUTO
FIXED

AUTO:
adaptive controller selects coding rate.

FIXED:
user/developer can force:
1/2
2/3
3/4
5/6
7/8

This is essential for radio testing and benchmarking.

Do not remove existing fixed behavior.

The current mode should effectively map to a legacy or fixed 1/2 profile.

---

# 10. Adaptive burst length

The current approximately 500-byte fixed burst is too small for a high-quality VHF link because repeated:

* preambles
* headers
* synchronization
* PTT switching
* TX/RX turnaround
* ACK
* RX reacquisition

consume a significant percentage of airtime.

Implement variable burst sizes.

Suggested initial payload burst profiles:

* 256 B
* 512 B
* 1024 B
* 2048 B
* 4096 B
* 8192 B
* 16384 B

Do not assume the maximum profile is always appropriate.

The normal high-quality target should initially be around 4–8 kB.

16 kB should be allowed experimentally but selected only on very stable links.

---

# 11. Distinguish burst from ARQ sub-block

A large TX burst must NOT become one huge all-or-nothing CRC packet.

A burst should contain multiple independently recoverable sub-blocks.

Example:

8 kB TX burst

could contain:

16 × 512-byte sub-blocks

Each sub-block must have enough independent integrity information that the receiver can identify exactly which blocks failed.

Conceptual structure:

BURST
header
subblock 0
subblock 1
subblock 2
...
subblock N
optional burst-level trailer

Each sub-block should contain or be associated with:

* sub-block index
* valid payload length
* sequence information if needed
* CRC
* FEC protection

Avoid repeating large full headers for every sub-block.

Use compact metadata.

---

# 12. Sub-block size

Use a configurable sub-block size.

Initial recommended default:

512 bytes

Potential supported options:

256
512
1024 bytes

Start with 512 bytes unless existing frame architecture strongly favors another size.

The burst size and ARQ sub-block size must be separate concepts.

Example:

burst size = 8192 bytes
ARQ block size = 512 bytes
number of blocks = 16

---

# 13. Selective Repeat ARQ

Implement selective-repeat behavior.

After receiving a burst, the receiver should NOT simply send:

ACK whole burst

or:

NACK whole burst

Instead send a compact status indicating which sub-blocks were successfully received.

For example, a 16-block burst can use a bitmap:

1111111111111111
= all good

1111101111110111
= block(s) missing/failed

The TX side should retransmit only failed sub-blocks.

Do not retransmit already successfully delivered blocks.

---

# 14. ACK/NACK bitmap format

Design a compact ACK structure.

It should include at least:

* burst sequence ID
* number of sub-blocks represented
* ACK bitmap or NACK bitmap
* optional receiver quality metrics
* CRC

For example conceptually:

ACK {
burst_id
block_count
received_bitmap
rx_quality
crc
}

Do not over-engineer the first version.

For bursts larger than 64 blocks, bitmap length should be variable rather than fixed to one machine integer.

Current suggested profiles with 512-byte blocks fit easily:

256 B = 1 block
512 B = 1 block
1 kB = 2
2 kB = 4
4 kB = 8
8 kB = 16
16 kB = 32

---

# 15. Retransmission handling

Maintain TX state for each outstanding burst.

Conceptually:

BurstTxState:

* burst_id
* blocks[]
* block ACK state
* retry count per block
* overall retry count
* initial FEC
* current FEC
* timestamp
* timeout state

When an ACK bitmap arrives:

mark successful blocks complete.

requeue only failed blocks.

The retransmission burst may contain only failed sub-blocks.

Do not require retransmission to preserve the exact original full burst layout if the protocol can cleanly identify sub-blocks.

---

# 16. FEC on retransmission

Consider using stronger FEC when retransmitting failed sub-blocks.

Initial implementation can use:

original:
16-QAM + FEC 7/8

failed block retry:
16-QAM + FEC 5/6

second retry:
16-QAM + FEC 3/4

and eventually stronger fallback.

However, implement this only if it can be done cleanly.

At minimum, make the architecture support a retransmission FEC override.

The receiver must always know the FEC profile used for each retransmission.

---

# 17. Burst length adaptation

Burst length should be managed by the same link adaptation subsystem or a closely related component.

The system should favor longer bursts when:

* recent packet success rate is high
* NACK ratio is low
* retransmission count is low
* link quality is stable
* SNR/EVM margin is good

It should shorten bursts when:

* multiple sub-blocks fail
* channel quality fluctuates
* retransmission cost becomes large
* timeout frequency increases
* link becomes unstable

Suggested progression:

256
512
1024
2048
4096
8192
16384

Do not change more than one or two levels at once unless severe failure occurs.

---

# 18. Interaction between FEC and burst adaptation

This is important.

Do not create two independent controllers that fight each other.

The modem should prefer preserving throughput intelligently.

Conceptual behavior:

Very good/stable link:
FEC 7/8
burst 8192 or 16384

Good link:
FEC 5/6
burst 4096 or 8192

Moderate link:
FEC 3/4
burst 2048 or 4096

Poor link:
FEC 2/3
burst 512–2048

Very poor link:
FEC 1/2
burst 256–512

These are initial conceptual values only.

Do not blindly use SNR thresholds unless measurements support them.

The main feedback should ultimately be actual delivery performance.

---

# 19. Optimize for measured goodput

Add measurement of actual useful throughput.

Raw bitrate is NOT enough.

Track at least:

PHY raw bitrate
FEC-adjusted bitrate
payload bytes transmitted
payload bytes successfully delivered
retransmitted bytes
protocol overhead bytes
ACK airtime if measurable
burst duration
TX/RX turnaround duration
successful payload bytes/sec
moving goodput

Important metric:

GOODPUT =
unique application payload bytes successfully delivered
/
wall-clock transfer time

Do not count retransmitted bytes twice.

---

# 20. Goodput-based adaptation

After basic adaptation is working, allow controller decisions to consider actual goodput.

Example:

If FEC 7/8 causes enough retries that measured goodput becomes lower than FEC 5/6, step back to 5/6.

Likewise:

If increasing burst size from 4 kB to 8 kB produces more retransmissions and lower goodput, return to 4 kB.

This should eventually make optimization empirical rather than purely based on theoretical SNR.

Implement the data collection from the beginning even if the first decision algorithm remains threshold-based.

---

# 21. TX/RX turnaround accounting

Measure or expose the real cost of:

* PTT assertion
* transmitter settling
* burst transmission
* PTT release
* receiver reacquisition
* ACK wait
* ACK transmission
* next TX acquisition

This is important because longer bursts are specifically intended to amortize turnaround overhead.

Add timestamps/logging around these transitions.

Do not invent fixed turnaround values if the existing modem can measure them.

---

# 22. Frame sequence handling

Ensure selective repeat cannot create duplicate application data.

Every sub-block must have stable identity.

Use a combination appropriate to existing protocol, such as:

session ID
burst ID
block index

Receiver should safely detect:

* duplicate retransmissions
* late ACKs
* repeated bursts
* out-of-order retransmissions
* session reset

Application layer must receive each byte exactly once.

---

# 23. Burst IDs

Burst sequence numbers should wrap safely.

Do not use ambiguous reuse while an old burst can still be outstanding.

If the current protocol has sequence numbers, reuse or extend them rather than inventing a parallel system.

---

# 24. Receiver buffering

Receiver needs a temporary assembly buffer for each active burst.

Conceptually:

RxBurstState:

* burst_id
* expected block_count
* block presence bitmap
* decoded blocks
* CRC status
* timestamps
* timeout

When all blocks are valid:

reassemble payload
deliver upward
send complete ACK if needed
release state

When some blocks fail:

retain successful blocks
request only missing blocks

Do NOT discard good blocks simply because another block in the same burst failed.

---

# 25. Timeout behavior

Implement sane timers for:

* ACK timeout
* incomplete RX burst timeout
* retransmission timeout
* stale burst cleanup

Timers should account for variable burst length.

A 16-kB burst cannot use a timeout designed around the old 500-byte frame.

Prefer duration-aware timeouts:

expected_airtime
+
turnaround margin
+
processing margin

rather than one arbitrary global timeout.

---

# 26. Maximum retries

Provide configurable retry limits.

Track retry count at sub-block level if practical.

After repeated failures:

* strengthen FEC
* reduce burst size
* potentially fall back to more robust modulation/MCS if existing architecture supports it
* eventually fail the transfer cleanly

Never create infinite retransmission loops.

---

# 27. Link adaptation state

Maintain separate distinction between:

current selected TX parameters

and

measured remote RX quality

if the protocol provides metrics from the remote side.

For a two-way radio link, local receive quality is not automatically equal to remote receive quality.

If possible, include a small quality report in ACK frames.

Potential metrics:

* decoded SNR
* EVM
* failed blocks
* corrected errors
* received block count

Keep ACK compact.

---

# 28. Header robustness

The burst header and ACK frames are critical control information.

Do NOT encode them using the most aggressive payload profile.

Use a robust control profile.

For example, retain an existing robust control modulation/FEC if present.

The control path should remain decodable even if the payload profile proves too aggressive.

---

# 29. Legacy mode

Add a compatibility/debug mode equivalent to current behavior:

LEGACY:

* current FEC
* approximately 500-byte burst
* current ACK behavior if needed

This should help compare old and new modes on identical radios.

Do not delete the old path until new implementation is proven.

If practical, route old behavior through the new abstractions rather than keeping duplicate protocol stacks.

---

# 30. Suggested profile abstraction

Introduce a data structure representing a transmission profile.

Conceptually:

TxProfile:
modulation
fec_rate
burst_bytes
arq_block_bytes
control_profile
retry_policy

Since modulation adaptation is outside this immediate task, modulation may initially remain fixed at current 16-QAM/QPSK behavior.

Do not bake FEC and burst selection directly into OFDM sample generation.

---

# 31. Configuration

Expose configuration fields for development/testing:

Adaptive FEC:

* enabled
* forced rate

Adaptive burst:

* enabled
* forced burst size
* min burst
* max burst

ARQ:

* block size
* max retries
* timeout multiplier

Optional:

* adaptation aggressiveness

Defaults should preserve safe operation.

---

# 32. GUI

If Guardian G2 already has a modem/status panel, add concise diagnostics.

Show at least:

FEC:
AUTO / 5/6

Burst:
8192 B

ARQ block:
512 B

Last burst:
16 blocks
15 first-pass OK
1 retransmitted

Goodput:
6.8 kb/s

Retries:
x %

Do not clutter the main UI.

If there is already a debug/advanced panel, put detailed statistics there.

---

# 33. Logging

Add structured logs suitable for comparing radio tests.

Example:

TX BURST #184
payload=8192B
blocks=16
block_size=512B
fec=7/8
encoded=XXXXB
airtime=4.82s

RX ACK #184
ok=15/16
missing=[6]
snr=XX.X
evm=XX.X

RETX #184
blocks=[6]
fec=5/6

COMPLETE #184
unique_payload=8192B
tx_bytes=8704B
retransmitted=512B
elapsed=5.42s
goodput=12.09kbps

Use actual measured values.

---

# 34. Benchmark tooling

Add or extend a local modem benchmark/test tool.

We need to be able to test without repeatedly transferring manual files.

Support generating a deterministic payload, for example:

1 MB pseudo-random buffer

Measure:

* transfer completion time
* unique bytes delivered
* raw transmitted bytes
* retransmitted bytes
* block failures
* FEC profile usage
* burst profile usage
* goodput

Output a concise summary.

If the repository has loopback or simulated channel infrastructure, use it.

---

# 35. Channel impairment testing

If existing tests can simulate channel impairment, add scenarios with:

* no errors
* random isolated bit errors
* burst errors
* packet/sub-block loss
* increasing noise
* intermittent quality changes

Test that:

* stronger FEC reduces failures
* selective ARQ only retransmits failed blocks
* adaptation downgrades appropriately
* adaptation upgrades only after stability
* no application bytes are duplicated or lost

Do not build a giant radio channel simulator if none exists.

Simple deterministic fault injection is sufficient.

---

# 36. Unit tests

Add tests for at least:

FEC profile serialization/deserialization
FEC encode/decode per supported rate
puncturing/depuncturing if used
burst segmentation
burst reassembly
ACK bitmap generation
ACK bitmap parsing
partial retransmission
duplicate retransmission handling
burst sequence handling
timeout calculations
adaptive FEC transitions
adaptive burst transitions
goodput accounting

Tests should be deterministic.

---

# 37. Integration tests

Create an end-to-end test:

TX application buffer
-> segmentation
-> FEC encoding
-> OFDM path or closest available abstraction
-> RX decoding
-> deliberate corruption of selected sub-block
-> ACK bitmap
-> retransmission
-> final reassembly

Verify byte-for-byte equality with original input.

---

# 38. No silent corruption

CRC must protect delivered data.

A sub-block must never be marked successful solely because FEC decoder returned bytes.

CRC validation is mandatory unless an equivalent integrity mechanism already exists.

Application data must only be delivered when integrity is proven.

---

# 39. Padding

Handle final partial sub-block correctly.

Example:

burst target = 4096 B
remaining application payload = 2800 B
block size = 512 B

Final block may be shorter.

Header must communicate real valid length.

Do not leak padding into delivered application data.

---

# 40. Empty/short messages

Ensure protocol still handles small messages efficiently.

Do not force a 100-byte payload into a nominal 8-kB transfer with pointless padding.

Burst target is a maximum/aggregation target, not mandatory fill size.

---

# 41. Streaming/file transfers

Where possible, allow the TX layer to aggregate queued application data until:

* target burst size is reached
  or
* aggregation timeout expires
  or
* application requests flush

Do not introduce large interactive latency for tiny control messages.

A file transfer can wait briefly to aggregate.

An interactive/control message should be sent promptly.

---

# 42. Control traffic priority

ACK/control frames must not get stuck behind large queued data bursts.

Preserve or implement control priority.

ARQ responses should be generated quickly after RX turnaround.

---

# 43. State machine safety

Because radio is half-duplex, explicitly review:

TX data
-> release PTT
-> switch RX
-> wait ACK
-> process ACK
-> schedule retransmission or next burst

Avoid race conditions where both sides transmit simultaneously.

Respect whatever link master/slave/session direction mechanism currently exists.

Do not redesign link establishment unless necessary.

---

# 44. Adaptive burst growth

Initial conservative policy suggestion:

Start at 512 B or current 500 B equivalent.

After several clean bursts:
512 -> 1024

then:
1024 -> 2048
2048 -> 4096
4096 -> 8192

Move to 16384 only after prolonged clean operation.

A single small failure should not necessarily collapse the burst size.

Multiple failed blocks or repeated retransmissions should reduce it.

Use hysteresis and rolling history.

---

# 45. Adaptive FEC growth

Likewise:

Start new/unknown link robustly.

Suggested initial profile:
FEC 1/2 or current known-safe profile.

Then upgrade progressively:

1/2
2/3
3/4
5/6
7/8

Upgrade only when demonstrated stable.

A retry storm should immediately move toward stronger coding.

---

# 46. Joint adaptation strategy

Avoid changing both parameters on every packet.

Example conservative logic:

If link is clean for a sustained period:
first increase FEC rate.

If still clean:
increase burst length.

If failures appear:
first reduce FEC rate.

If failures continue:
reduce burst length.

Or choose another well-reasoned strategy after inspecting existing behavior.

Document the chosen policy.

The important requirement is preventing oscillation.

---

# 47. Future MCS compatibility

Even though 64-QAM is explicitly NOT part of this task, design adaptation structures so that future profiles can include modulation.

Conceptual future MCS table:

MCS0 QPSK 1/2
MCS1 QPSK 3/4
MCS2 16QAM 1/2
MCS3 16QAM 2/3
MCS4 16QAM 3/4
MCS5 16QAM 5/6
MCS6 16QAM 7/8

Do not implement a new modulation stack just to satisfy this table.

Simply avoid architectures where coding rate cannot later be paired with modulation.

---

# 48. Performance target

The immediate purpose is to remove protocol/FEC bottlenecks.

For a clean approximately 2.7-kHz link using 16-QAM, we eventually want to approach the practical physical limit as closely as possible.

A useful target is:

approximately 7–8 kbps of real application goodput on a very clean link,

subject to the actual current OFDM symbol structure and radio path.

Do NOT fake or hard-code this number.

Instrument the implementation so real radio testing tells us where the bottleneck remains.

---

# 49. Efficiency report

After implementation, calculate and log:

channel symbol/raw rate
minus
pilot/CP/OFDM overhead
minus
FEC overhead
minus
frame/header overhead
minus
ARQ/turnaround overhead
=======================

measured application goodput

We need to know exactly where capacity is being lost.

If convenient, create a debug command/report such as:

Modem Efficiency Report

PHY raw:             9.2 kbps
after OFDM overhead: 8.5 kbps
after FEC:           7.4 kbps
protocol payload:    7.2 kbps
measured goodput:    6.9 kbps
retransmission loss: 4.1 %
turnaround loss:     3.2 %

Use real measurements and actual implementation data.

---

# 50. Implementation stages

Do this incrementally.

Stage 1:
Refactor current fixed FEC and fixed burst values behind abstractions without changing behavior.

Existing modem must still work.

Stage 2:
Add multiple FEC rates and fixed manual selection.

Prove encode/decode correctness.

Stage 3:
Implement variable burst segmentation and reassembly.

Still use whole-burst ACK if necessary during this intermediate stage.

Stage 4:
Implement sub-block CRC and ACK/NACK bitmap.

Stage 5:
Implement selective retransmission.

Stage 6:
Add automatic FEC adaptation.

Stage 7:
Add automatic burst-size adaptation.

Stage 8:
Add joint adaptation, statistics and goodput optimization.

Stage 9:
Add GUI/debug reporting and benchmark tooling.

Do not attempt to land all protocol changes as one opaque rewrite.

---

# 51. Protocol versioning

Because frame format will likely change, bump or introduce an explicit protocol/frame version.

A node should reject incompatible frames cleanly instead of decoding them incorrectly.

If a negotiation mechanism already exists, use it.

If not, keep version handling simple.

---

# 52. Documentation

Update repository documentation with:

* supported FEC profiles
* what coding rate means
* burst sizes
* ARQ block size
* selective-repeat behavior
* AUTO versus FIXED mode
* adaptation logic
* debug statistics
* benchmark procedure

Include a short protocol/frame layout.

---

# 53. What NOT to do

Do NOT:

* widen the occupied channel
* rewrite OFDM from scratch
* replace working FEC unnecessarily
* remove legacy/current mode
* hide all adaptation behind unexplained magic thresholds
* retransmit an entire long burst because one 512-byte block failed
* use RSSI as the sole quality metric
* allow silent payload corruption
* let adaptation oscillate rapidly
* optimize only theoretical bitrate while ignoring actual goodput
* introduce unbounded retransmission queues
* create giant monolithic TX/RX functions

---

# 54. Code quality

Prefer:

small explicit data structures
clear enums
typed interfaces where the repository uses typing
isolated protocol serialization
isolated adaptation logic
unit-testable state machines
structured logging

Avoid touching unrelated Guardian functionality.

---

# 55. Deliverables

At the end, provide:

1. Summary of current G2 modem architecture discovered.
2. Exact files modified.
3. Description of new frame/burst format.
4. Supported FEC rates and implementation method.
5. Description of selective-repeat ARQ.
6. Adaptation algorithm.
7. New configuration options.
8. New diagnostics/logging.
9. Tests added.
10. Benchmark results available locally/simulated.
11. Any remaining blockers for real IC-705 testing.
12. Specific parameters that should be tuned during first over-the-air test.

Most importantly:

Do not merely scaffold interfaces.

Implement the actual working encode/decode, segmentation, ACK bitmap, retransmission, state handling, statistics, and tests wherever the existing repository architecture makes that possible.

Keep the modem operational after every major implementation stage.
