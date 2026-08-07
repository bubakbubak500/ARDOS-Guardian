We are going to start implementing a new experimental native VHF OFDM payload modem in the existing ARDOS Guardian project.

Repository:
https://github.com/bubakbubak500/ARDOS-Guardian

IMPORTANT: First inspect the CURRENT repository and architecture before changing anything. Do not rely on an old mental model of Guardian.

This is an implementation task, not just an architecture proposal. Work incrementally, keep the current VARA FM/HF path fully functional, add tests, and leave the repository in a runnable state.

# GOAL

Add a new experimental payload transport:

```
Guardian OFDM VHF
```

config/backend identifier:

```
ofdm_vhf
```

This will eventually be a fully adaptive OFDM modem for VHF/UHF radio using the PC sound interface and Guardian-controlled PTT.

Long-term target:

* native Guardian DSP modem
* no VARA dependency for this payload mode
* OFDM
* BPSK / QPSK / 16-QAM / 64-QAM
* adaptive modulation and coding according to link quality
* FEC
* interleaving
* ARQ
* SNR / EVM / PER measurements
* channel estimation
* frequency-selective interference handling
* eventually optional per-subcarrier adaptive bit loading

BUT DO NOT attempt all of this at once.

The first milestone is a technically clean OFDM PHY foundation plus Guardian integration and deterministic simulation/loopback testing.

# EXISTING GUARDIAN ARCHITECTURE

Inspect this yourself and verify it against current main, but currently the relevant architecture includes approximately:

* guardian/payload/base.py

  * PayloadBackend
  * start_send()
  * start_receive()
  * cancel()

* guardian/payload/vara_p2p.py

  * existing VARA P2P payload implementation

* guardian/payload/**init**.py

  * make_backend()

* guardian/modem/

  * afsk.py
  * mfsk.py
  * fec.py
  * audio.py

* guardian/operations.py

  * creates the payload backend
  * owns radio/PTT/audio lifecycle
  * currently creates VaraClient
  * suspends the control audio modem while payload owns the codec
  * handles QSY and restoration

* guardian/qt/settings_dialog.py

  * already contains a "Payload workflow" picker
  * currently only exposes "Guardian VARA P2P"
  * the comment explicitly anticipates another transport

Guardian currently uses AFSK/MFSK for ARDOS control bursts and VARA for payload.

DO NOT replace AFSK/MFSK.

For now the architecture shall remain:

```
ARDOS control plane
    AFSK1200 on FM / MFSK16 on HF
          |
          v
handshake / routing / HAVE_MSG / ACK...
          |
          v
selectable payload backend
          |
    +-----+----------------+
    |                      |
VARA P2P             Guardian OFDM VHF
existing                  new
```

This separation is important.

# IMPORTANT PROTOCOL CONSTRAINT

The current session state machine and some wire/UI naming are still VARA-specific, e.g. START_VARA, STARTING_VARA, TRANSFERRING.

DO NOT casually rename the existing on-air protocol in this first implementation because that could break compatibility with existing Guardian stations.

Before making protocol changes:

1. inspect guardian/protocol and guardian/session/orchestrator.py;
2. document what would need to change for explicit payload-backend negotiation;
3. preserve compatibility with existing VARA stations.

For this milestone it is acceptable for internal legacy START_VARA semantics to temporarily mean "start negotiated payload phase", PROVIDED both peers are explicitly configured for the OFDM experimental backend and there is no unsafe chance that a VARA peer silently treats OFDM as VARA.

If backend compatibility cannot be guaranteed with the existing frame format, implement a small backward-compatible capability/transport negotiation rather than silently mixing transports.

Do not break existing on-air compatibility merely for naming cleanliness.

# BANDWIDTH: VERY IMPORTANT

Do NOT decide or hard-code the final occupied RF/audio bandwidth.

We will experimentally determine usable bandwidth later on real VHF radios.

The OFDM implementation must therefore be parameterized from the beginning.

Separate concepts clearly:

* soundcard sample rate
* FFT size
* subcarrier spacing
* number/set of active carriers
* occupied baseband bandwidth
* guard/DC carriers
* cyclic prefix
* RF radio mode/bandwidth

Use 48 kHz audio as the initial default SAMPLE RATE because Guardian already uses it, unless current architecture provides a better reason.

However:

```
48 kHz sample rate != 48 kHz RF bandwidth.
```

Do not confuse these.

Create an OFDM configuration/profile object so that we can later change the usable occupied bandwidth without rewriting the modem.

A temporary conservative BENCH profile may be used for unit tests, but clearly label it as a simulation/bench profile and do not present it as the final VHF air profile.

# PHASE 1 — OFDM DSP CORE

Create a clean OFDM PHY module, preferably something like:

```
guardian/ofdm/
    __init__.py
    config.py
    constellation.py
    phy.py
    framing.py
    metrics.py
```

or another similarly clean structure after inspecting project conventions.

Do not bury the OFDM DSP inside Qt or Operations.

Implement the DSP as pure numpy code wherever practical.

The runtime currently already depends on numpy. Avoid adding a large dependency such as GNU Radio.

SciPy should not be required unless there is a compelling reason that cannot reasonably be implemented with numpy.

The DSP must be independently testable without:

* Qt
* radio
* Hamlib
* sounddevice
* VARA

# CONSTELLATIONS

Implement reusable constellation mapping/demapping for:

* BPSK
* QPSK
* 16-QAM
* 64-QAM

Use Gray mapping.

Normalize average constellation power consistently.

Add tests verifying mapping/demapping round trips.

Design the API so soft metrics / LLR-style confidence can be added or used by FEC later.

# OFDM SYMBOL ENGINE

Implement:

* configurable FFT/IFFT size
* explicit active subcarrier map
* DC null
* guard carriers
* configurable pilot carriers
* cyclic prefix
* serial bits -> QAM symbols -> subcarriers -> IFFT -> CP
* CP removal -> FFT -> carrier extraction -> equalization -> demapping

Keep positive/negative frequency handling correct for a REAL audio waveform.

Remember that Guardian sends ordinary real-valued audio through a sound device, not complex IQ samples directly to an SDR.

Choose and document an appropriate real-audio OFDM approach, e.g. Hermitian symmetry or another technically correct architecture.

Do not accidentally generate complex samples and throw away the imaginary component.

# SYNCHRONIZATION

The modem must not assume perfectly aligned arrays.

Design and implement an initial burst frame with:

```
TX lead/settling audio
preamble
synchronization/training symbols
robust PHY header
OFDM data symbols
optional tail/guard
```

Implement enough synchronization for simulated and audio-loopback reception:

* burst detection
* coarse timing
* OFDM symbol boundary acquisition
* coarse/fine carrier-frequency-offset estimation where relevant
* phase correction
* pilot-based residual tracking

The radio/audio chain can introduce:

* arbitrary delay
* gain changes
* clipping
* frequency offset
* phase rotation
* frequency response distortion

The architecture must not rely on a laboratory-perfect waveform.

# CHANNEL ESTIMATION

Include known training/pilot symbols.

Estimate the complex channel response per used subcarrier and equalize each carrier.

Expose useful receiver measurements:

* estimated SNR
* EVM
* RSSI/audio RMS if useful
* synchronization confidence
* channel response per carrier
* packet/frame error
* CFO estimate

These measurements are critical because they will later drive adaptive modulation.

# MCS ARCHITECTURE

Create an explicit Modulation and Coding Scheme abstraction now, even though Phase 1 may operate at one fixed robust MCS.

For example conceptually:

```
MCS0 = BPSK + strong FEC
MCS1 = QPSK + strong FEC
MCS2 = QPSK + lighter FEC
MCS3 = 16-QAM
...
MCSn = 64-QAM
```

Do not invent final thresholds yet.

Do not claim specific over-the-air data rates until the usable bandwidth is known.

The sender/receiver architecture should be able to identify the MCS used by a frame from a robust PHY header.

Start reception using a robust bootstrap/header mode that does not require the receiver to already know the payload modulation.

# FEC

Guardian already contains a rate-1/2 K=7 convolutional encoder/Viterbi implementation in guardian/modem/fec.py.

Inspect whether it can be cleanly reused or generalized.

For the first OFDM milestone it is acceptable to use this existing convolutional FEC, preferably with soft-decision demodulation if practical.

Do not copy/paste a second incompatible implementation unnecessarily.

Design the OFDM FEC boundary so that LDPC can replace or supplement this later.

Add interleaving so a frequency-selective fade or damaged OFDM symbol does not destroy adjacent coded bits.

# FRAMING

Create an OFDM payload frame format with a versioned header.

The robust PHY/link header should contain at least enough information for the receiver to determine:

* protocol/version
* frame type
* message/block identifier
* MCS
* payload length
* sequence/block number as needed
* integrity protection

Use Guardian's existing CRC utilities where sensible.

Do not assume one OFDM burst must contain an entire Guardian attachment.

Support segmentation into bounded blocks from the beginning.

# ARQ ARCHITECTURE

We ultimately need reliable half-duplex transfer.

Design the link layer for:

* DATA blocks
* ACK
* NACK or selective retransmission if useful
* timeout
* retry limit
* duplicate suppression

Do NOT use TCP semantics or VARA internals.

This is a radio link layer.

For the first milestone, a simple stop-and-wait ARQ is acceptable and preferable to an overcomplicated protocol.

ACK/NACK should use a robust OFDM mode.

Keep PTT turnaround explicitly represented in the design.

If real-radio ARQ would expand the first implementation too much, implement and thoroughly test it in a simulated duplex/audio-loopback transport first, then clearly isolate the remaining hardware integration.

# ADAPTIVE MODULATION — PREPARE NOW, ACTIVATE LATER

Build the measurements and API necessary for future link adaptation.

Long-term algorithm will consider approximately:

* SNR
* EVM
* packet error rate
* retransmission rate
* per-subcarrier channel quality
* narrowband interference

Later it will automatically step:

```
BPSK
  ↓↑
QPSK
  ↓↑
16-QAM
  ↓↑
64-QAM
```

and change FEC rate.

Do NOT implement aggressive adaptation thresholds based on guesses in this milestone.

Initially use a fixed configurable MCS and log the measurements that an adaptation controller would consume.

Create a clean interface such as a LinkMetrics / AdaptationState object so adding adaptation later is straightforward.

# PER-SUBCARRIER ADAPTATION

The eventual goal includes different modulation orders on different OFDM subcarriers simultaneously.

Example:

```
carrier 1 -> BPSK
carrier 2 -> QPSK
carrier 3 -> disabled because of interference
carrier 4 -> 16-QAM
carrier 5 -> 64-QAM
```

BUT THIS IS NOT REQUIRED IN PHASE 1.

Do not make the initial PHY unnecessarily complex.

However, do not design the frame format/API in a way that makes future bit-loading impossible.

A single MCS across all data carriers is correct for Phase 1.

# CHANNEL SIMULATOR / TEST HARNESS

This is a major requirement.

Before trying real RF, create deterministic tests that can run entirely on the PC.

Implement a channel/test harness capable of applying at least:

* AWGN
* adjustable SNR
* gain
* arbitrary sample delay
* small frequency offset
* phase offset
* clipping
* simple multipath
* frequency-selective attenuation/notch

Tests should prove that a generated binary payload survives:

```
bytes
  -> framing/FEC
  -> OFDM modulation
  -> simulated channel
  -> OFDM demodulation
  -> FEC/framing
  -> identical bytes
```

Include seeded deterministic random tests.

Also include failure tests where the modem correctly rejects a corrupted frame rather than returning incorrect payload bytes.

# BENCH TOOL

Add a developer tool, for example:

```
tools/ofdm_bench.py
```

or equivalent.

It should allow us to run something like:

```
python tools/ofdm_bench.py
```

and obtain useful measurements such as:

* profile
* sample rate
* FFT size
* occupied baseband bandwidth
* number of data carriers
* modulation
* coded/uncoded bits per OFDM symbol
* generated waveform duration
* measured SNR
* EVM
* BER/PER
* decode success/failure

Optionally allow generation of a WAV file and decoding a WAV file.

This will be extremely useful later when we capture real VHF radio audio.

Do not require RF transmission for this tool.

# GUARDIAN PAYLOAD BACKEND

Once the pure DSP path works, add:

```
guardian/payload/ofdm_vhf.py
```

implementing PayloadBackend.

Backend name:

```
ofdm_vhf
```

The backend must eventually own the soundcard during payload transfer just as VARA currently requires exclusive access to a shared codec.

Reuse Guardian concepts where possible:

* on_acquire
* on_release
* on_qsy
* on_receive_qsy
* on_unqsy

Do not duplicate radio/QSY logic inside the OFDM DSP.

The OFDM payload backend will need:

* configured RX audio endpoint
* configured TX audio endpoint
* Guardian PTT callback
* radio/audio lifecycle supplied by Operations

Refactor cleanly if necessary so both control audio and OFDM payload audio can use the existing sounddevice endpoint-resolution code without duplicating it.

PTT must ALWAYS be released in finally/error paths.

Never allow an exception to leave the radio keyed.

# AUDIO I/O

Use Guardian's existing sounddevice/PortAudio approach and device-resolution functions.

Do not rely on Windows default audio devices.

Use the exact RX/TX devices configured in Guardian.

For payload TX:

```
acquire codec
QSY if required
key PTT
wait configurable TX lead
transmit OFDM waveform
TX tail/guard
unkey PTT
listen for peer response
...
restore calling frequency if required
release codec
```

RX must be continuous enough during an OFDM payload session to detect the peer's burst/ACK.

DSP processing must not freeze the Qt UI thread.

# CONFIGURATION

Extend StationConfig with an OFDM configuration section/fields.

At minimum anticipate:

```
payload_backend = "vara_p2p" | "ofdm_vhf"

ofdm_sample_rate
ofdm_profile
ofdm_mcs
ofdm_fft_size
ofdm_cp_length
ofdm_active_carriers / occupied bandwidth representation
ofdm_tx_lead_ms
ofdm_tx_tail_ms
ofdm_max_retries
```

But prefer a maintainable profile/config object rather than dozens of unrelated globals if consistent with Guardian's configuration architecture.

CRITICAL:

The final radio bandwidth WILL BE SUPPLIED LATER after hardware tests.

Make this easy to change.

No hidden magic constants describing a supposed final VHF bandwidth.

# GUI

Add a second Payload workflow option:

```
Guardian OFDM VHF (Experimental)
```

with data:

```
ofdm_vhf
```

When OFDM is selected:

* VARA should no longer be treated as required for payload transfer.
* Hide/disable VARA-specific settings that are irrelevant.
* expose a small experimental OFDM section.
* initially show useful values such as:

  * profile
  * MCS
  * sample rate
  * occupied baseband bandwidth
  * FFT/carriers if appropriate
* clearly label the mode Experimental.

Do not expose twenty DSP knobs to a normal operator.

Keep advanced PHY parameters behind a profile or advanced/experimental UI.

Update bilingual English/Czech text in the same style as the existing application.

The existing settings tab is VARA-specific. Refactor its wording carefully toward a general "Payload / Data modem" concept if appropriate, while preserving existing functionality.

# READINESS / VARA DEPENDENCY

Inspect Guardian readiness logic.

When payload_backend == "vara_p2p":

```
current VARA readiness rules continue unchanged.
```

When payload_backend == "ofdm_vhf":

```
VARA executable/ports must NOT be required.
```

Instead verify:

* RX audio device available
* TX audio device available
* radio/PTT configuration available
* OFDM profile valid

Do not break the VARA installer/readiness workflow for VARA users.

# OPERATIONS

Currently Operations constructs VaraClient and make_backend() receives VARA and lifecycle callbacks.

Generalize this cleanly.

make_backend("vara_p2p") must behave exactly as before.

make_backend("ofdm_vhf") should receive the dependencies it actually needs.

Avoid giant `if backend == ...` blocks spread throughout the application.

Prefer clear backend boundaries.

# TELEMETRY / UI STATUS

Create an OFDM status/metrics model suitable for later UI display.

At minimum make available:

* state: idle / synchronizing / receiving / transmitting / waiting_ack / failed
* current MCS
* measured SNR
* EVM
* retries
* TX/RX bytes
* last block result
* estimated link bitrate if measurable

Do not fake metrics.

If a value is unavailable, represent it as unavailable.

We will build a richer OFDM spectrum/constellation window later.

# IMPORTANT: VARA MUST KEEP WORKING

Regression requirement:

Existing VARA P2P behavior must remain operational.

Existing:

```
Guardian VARA P2P
```

must remain the default backend.

Existing configs must continue loading.

Do not force users to configure OFDM.

Do not change existing radio behavior unless required by a clean abstraction.

# TESTS

Add focused test modules, for example:

```
tests/test_ofdm_constellation.py
tests/test_ofdm_phy.py
tests/test_ofdm_channel.py
tests/test_ofdm_payload.py
```

Also update:

```
tests/test_config.py
tests/test_payloads.py
tests/test_operations.py
tests/test_qt_shell.py / settings tests
```

where required.

Required tests include:

1. BPSK round trip
2. QPSK round trip
3. 16-QAM round trip
4. 64-QAM round trip
5. OFDM noiseless waveform round trip
6. delayed waveform synchronization
7. moderate AWGN round trip using robust MCS
8. small CFO round trip
9. simple multipath/channel equalization
10. narrow frequency notch test
11. CRC rejects corrupted payload
12. segmentation/reassembly
13. backend selection:
    vara_p2p -> VaraP2PBackend
    ofdm_vhf -> OfdmVhfBackend
14. old config without OFDM fields still loads
15. VARA remains default
16. OFDM mode does not require VARA
17. PTT is released after TX success
18. PTT is released after TX exception/failure
19. audio codec is returned to control modem before ARDOS confirmation
20. deterministic seeded simulation.

Do not weaken existing tests to make new code pass.

Run the full test suite.

# DOCUMENTATION

Create:

```
docs/ofdm-vhf.md
```

Document:

* architecture
* what is implemented
* what is intentionally NOT implemented yet
* waveform/frame structure
* current bench profile
* configuration parameters
* measured simulation performance
* limitations
* future adaptive MCS design
* future per-subcarrier bit loading
* planned real-radio test procedure

Clearly state that sample rate and occupied RF bandwidth are different concepts.

Do not advertise speculative throughput as achieved performance.

# DEVELOPMENT ORDER

Work in this order:

1. Inspect current repository.
2. Summarize the exact integration points you found.
3. Implement pure constellation mapping/tests.
4. Implement configurable pure OFDM PHY.
5. Implement synchronization/training/channel estimation.
6. Implement framing/FEC/interleaving.
7. Add deterministic channel simulation and bench tool.
8. Make clean binary waveform loopback pass.
9. Add the OfdmVhfBackend.
10. Integrate backend factory/config.
11. Integrate Operations/audio/PTT lifecycle.
12. Add GUI selector and OFDM settings.
13. Adjust readiness so OFDM does not require VARA.
14. Add/update tests.
15. Run the full test suite.
16. Update docs.

Do not start by redesigning the entire Guardian application.

# FIRST DELIVERABLE / ACCEPTANCE CRITERIA

For this first pass I want to be able to run a PC-only test and see something equivalent to:

```
Guardian OFDM VHF bench
-----------------------
sample rate:         48000
profile:             BENCH
MCS:                 MCS1 QPSK ...
FFT:                 ...
data carriers:       ...
occupied bandwidth:  ... [profile-derived, not final RF claim]

TX payload:          4096 bytes
simulated SNR:       ...
measured SNR:        ...
EVM:                 ...
retries:             ...
result:              PASS
RX payload identical: YES
```

and Guardian's settings must contain:

```
Payload workflow:
    Guardian VARA P2P
    Guardian OFDM VHF (Experimental)
```

with VARA remaining the default.

The waveform must also survive a reasonable deterministic simulated channel rather than only a perfect array-to-array round trip.

# DO NOT DO YET

Do not implement these as mandatory parts of the first milestone:

* final RF bandwidth
* final Quansheng-specific profile
* final IC-705 profile
* 50 kHz mode
* automatic MCS thresholds
* adaptive per-subcarrier QAM
* LDPC
* elaborate constellation GUI
* compatibility with every radio
* automatic ALE frequency selection

Architect for them, but do not let them delay the basic working OFDM transport.

# ENGINEERING PRINCIPLES

Prefer:

* measurable behavior
* tests before RF claims
* pure DSP separated from hardware
* deterministic simulation
* explicit state machines
* configurable waveform parameters
* backwards compatibility
* graceful failure
* PTT safety
* useful diagnostics

Avoid:

* magic numbers
* guessed RF bandwidth
* speculative bitrate claims
* blocking the Qt thread
* duplicated sounddevice code
* coupling DSP to GUI
* coupling DSP to VARA
* rewriting stable Guardian subsystems unnecessarily

At the end, report:

1. files added/changed;
2. architecture implemented;
3. exact OFDM waveform parameters of the BENCH profile;
4. tests added;
5. complete test-suite result;
6. what can now be tested without radio;
7. what is ready for the first two-radio VHF test;
8. the next recommended step for adaptive MCS.

Start by inspecting current main and then implement the first milestone.
