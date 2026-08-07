# Guardian OFDM VHF — Implementation Plan for G2.0.1

Status: **ACTIVE PLANNING DOCUMENT** — this file and [OriginalPrompt.md](OriginalPrompt.md)
are the two authoritative inputs for the `ofdm_vhf` development effort on the
private **G2** line. Update this file as decisions are made; do not let it rot.

- Original task statement: [docs/OriginalPrompt.md](OriginalPrompt.md)
- Target: release **G2.0.1** (`guardian/_version.py` → `"2.0.1"`), built locally
  per [G2.md](../G2.md) (GitHub Actions is disabled on the private G2 repo).
- Branch: `g2`. Baseline: `35be86b` (three commits past the `1.0.0` common
  ancestor with the public G1 line).
- Repository survey date: 2026-08-07. All `file:line` references below were
  verified against `g2 @ 35be86b`; re-verify before relying on them after
  unrelated commits land.

---

## 1. Scope of this milestone

Deliver the **first milestone** of the OFDM effort exactly as bounded by the
original prompt:

1. A clean, pure-numpy OFDM PHY (`guardian/ofdm/`) — testable with no Qt, no
   radio, no Hamlib, no sounddevice, no VARA.
2. Constellations BPSK/QPSK/16-QAM/64-QAM, Gray-mapped, power-normalized,
   soft-output capable.
3. Burst synchronization (detection, timing, CFO, phase, pilot tracking) that
   survives a deterministic simulated channel — not just array-to-array.
4. Channel estimation + per-carrier equalization + receiver metrics
   (SNR, EVM, CFO, sync confidence, per-carrier response).
5. Framing with versioned robust header, segmentation, CRC, FEC (existing
   K=7 r=1/2 convolutional + interleaving), fixed configurable MCS.
6. Deterministic channel simulator + `tools/ofdm_bench.py` (incl. WAV in/out).
7. `OfdmVhfBackend` (`ofdm_vhf`) integrated into `make_backend`, Operations,
   StationConfig, readiness, and the settings GUI — VARA stays the default and
   fully functional.
8. Stop-and-wait ARQ designed now; **exercised in simulated duplex/loopback**,
   real-radio ARQ isolated as the explicitly remaining hardware step.
9. Tests (see §9) + `docs/ofdm-vhf.md` + release G2.0.1.

Explicitly **out of scope** (architected-for, not implemented): final RF
bandwidth, radio-specific profiles (Quansheng/IC-705), 50 kHz mode, automatic
MCS thresholds, per-subcarrier bit loading, LDPC, constellation GUI, ALE.

---

## 2. Verified current architecture — integration points

Facts below come from direct inspection of `g2 @ 35be86b`.

### 2.1 Payload backend seam

- `guardian/payload/base.py:11` — `PayloadBackend` is a plain class (not ABC):
  `name = "base"`, `start_send(msg, done)`, `start_receive(msg, done)`,
  `cancel(msg)` (a never-called no-op today). `done: Callable[[bool], None]`
  reports *local* transfer success only; end-to-end confirmation arrives as a
  `RECEIVED` control frame.
- `guardian/payload/__init__.py:23` — `make_backend(name, *, vara, on_log,
  on_qsy, on_receive_qsy, on_unqsy, on_acquire, on_release, **_legacy)`
  **ignores `name` and unconditionally returns `VaraP2PBackend`**. This is the
  dispatch point to extend.
- `guardian/payload/vara_p2p.py:91` — reference backend. Threading model:
  `start_send/start_receive` spawn a daemon thread; whole transfer under
  `self._transfer_lock`; **`done(ok)` is invoked outside the lock** after
  `on_unqsy`/`on_release` (`:232`, `:425`) because `done()` may immediately key
  the control modem. The OFDM backend must preserve this ordering.
- Hook contract (from `vara_p2p.py:96-106`): `on_acquire()` may raise (treat
  as failure); `on_release()` is called via a swallow-all wrapper; `on_qsy(msg)`
  returning exactly `False` aborts; `on_unqsy()` runs in `finally` *before*
  `on_release`.

### 2.2 Operations lifecycle

- `guardian/operations.py:926-947` — `_make_payload_backend()` passes `vara=`
  plus the five hooks. Called from `_build_net()` (`:209`) and
  `apply_network_settings()` (`:279`).
- Codec handoff: `_suspend_control()` (`:1830`) sets `_payload_active`, drains
  a CAT poll, `audio_transport.wait_tx_idle(5.0)`, `audio_transport.stop()`.
  `_resume_control()` (`:1855`) restarts the control transport and clears the
  flag in `finally`.
- PTT: `_radio_ptt(enabled)` (`:1772`, takes `_radio_lock`) is the raw keying
  primitive; `_vara_ptt` (`:1785`) adds the negotiated release delay and is
  installed as `VaraClient.on_ptt`. **There is no generic payload-PTT hook —
  one must be added for OFDM** (inject `_radio_ptt`-based callable through
  `make_backend`).
- QSY: `_payload_send_qsy` (`:2063`), `_payload_receive_qsy` (`:2079`),
  `_payload_restore_calling` (`:2090`) — reuse as-is; no QSY logic in DSP.
- Audio endpoints: `resolve_device(name, kind)` in `guardian/modem/audio.py:341`
  returns an `int` device index; `AudioControlTransport` (48 kHz default,
  `audio.py:493`) shows the house style: persistent `sd.InputStream` for RX,
  blocking `sd.play(...)`/`sd.wait()` for TX inside a TX lock, PTT lead/tail in
  `try/finally`. The OFDM backend reuses `resolve_device` — **no duplicated
  sounddevice code**.

### 2.3 Session / protocol

- States: `guardian/session/orchestrator.py:190-220` — incl.
  `STARTING_VARA("starting")`, `TRANSFERRING("transferring")`.
- Payload phase entry points: initiator `_start_vara()` (`:1055`) sends
  `FrameType.START_VARA` then calls `payload.start_send(...)`; responder
  `_rx_start()` (`:1149`) calls `payload.start_receive(...)`. **These are the
  only two hook points; the orchestrator never cares which backend it holds.**
- Wire compat: `guardian/protocol/frames.py:40` `VERSION = 1` — mismatch is
  fatal on decode (`:190`). Unknown frame types are fatal too (`:211`).
  `Flags` uses bits `0x01/0x02/0x04`; PTT-delay negotiation occupies
  `0x08/0x10/0x20` (`:102-127`). **Bits `0x40` and `0x80` are free** — the only
  zero-cost place for in-band capability signalling.
- `orchestrator.py:167` duplicates `_PAYLOAD_MIN_WIRE_SIZE = 256` from
  `vara_p2p.MIN_WIRE_SIZE`; `tests/test_session.py:234` pins them equal. Leave
  untouched this milestone (OFDM manages its own timeouts internally).

### 2.4 Config

- `guardian/config.py` — stdlib `@dataclass`; `load()` drops unknown keys and
  defaults missing ones (`:244-245`), so **old configs load automatically**.
- `payload_backend: str = "vara_p2p"` already exists (`:156`).
- **HARD BLOCKER:** `load()` coerces any non-`vara_p2p` value back to
  `"vara_p2p"` (`config.py:246-249`, a 0.6.26 winlink_manual migration). Must
  become an allowlist `{"vara_p2p", "ofdm_vhf"}` with fallback to default.

### 2.5 GUI

- Settings picker: `guardian/qt/settings_dialog.py:855-860` — a one-item
  `QComboBox` on the VARA tab (`_build_vara()`, `:841`; tab title
  `tr("settings.vara")`), row label `dual("Payload workflow", "Způsob
  přenosu")` (`:919`), write-back `cfg.payload_backend =
  ...currentData()` (`:1121`). It hard-sets index 0 and never restores from
  config — needs `findData()` restore.
  **Per the operator's screenshot decision (2026-08-07): this tab/row is the
  chosen home for the new transport selector.** The comment at `:855` already
  anticipates it.
- i18n: `guardian/i18n.py` — `tr(key)` catalog of `(en, cs)` tuples + inline
  `dual(en, cs)`. No Qt translator machinery.
- Hard-coded `== "vara_p2p"`: `guardian/qt/shell.py:576-577` (spectrum gating;
  pinned by `tests/test_qt_shell.py:174` with sentinel
  `"some_future_transport"`) and `shell.py:590-593` (header label falls through
  to literal `"Winlink"` — will mis-render for `ofdm_vhf`).

### 2.6 Readiness

No single `is_ready()`. Three surfaces: `guardian/install/dependencies.py`
(`inspect_dependencies()` `:89` returns Hamlib + VARA FM + VARA HF rows),
`guardian/qt/readiness_dialog.py`, and the home readiness table
(`guardian/qt/shell.py:708-745`, incl. a `VARA {mode}` row). VARA is only
*hard*-required at `Operations.connect_vara()` (`operations.py:1196-1202`).
For `ofdm_vhf` the dependency rows and home table must swap VARA rows for:
RX device resolvable, TX device resolvable, PTT/radio configured, OFDM profile
valid.

### 2.7 FEC and CRC available for reuse

- `guardian/modem/fec.py` — rate 1/2, K=7, polys `0o171/0o133`:
  `conv_encode(bits)`, `viterbi_decode(coded)` (hard),
  `viterbi_decode_soft(soft)` (float confidences, **positive ⇒ bit 1**).
  Pure-Python 64-state inner loop — see §11 (performance risk).
- `crc16` (CRC-16/CCITT-FALSE) in `guardian/protocol/frames.py:130`,
  canonical import `from ..protocol import crc16`; packing `struct.Struct(">H")`.

### 2.8 Dependencies / tests / release

- numpy `>=1.26,<3` **is** a runtime dep. **scipy is absent** — DSP must be
  numpy-only (`numpy.fft.rfft/irfft` cover everything needed). Qt binding is
  `PySide6-Essentials` (no Charts/Multimedia). WAV I/O: stdlib `wave` module.
- Tests: flat `tests/` (38 files), `python -m pytest` (`addopts = "-q"`);
  local runs need `--basetemp` per `build.ps1:43` convention. `conftest.py`
  redirects `APPDATA` at import time.
- Release: `_version.py` is the single source of truth; release notes file
  `docs/RELEASE_NOTES_<version>.md` is mandatory; on G2 the build is local
  (`build.ps1`, `build_installer.ps1`), tags pushed deliberately to the `g2`
  remote. Test workflow enforces `^\d+\.\d+\.\d+$` — `2.0.1` fits.

### 2.9 Blocker summary

| # | Where | What must change |
|---|-------|------------------|
| 1 | `config.py:246-249` | `payload_backend` coercion → allowlist |
| 2 | `payload/__init__.py:31` | real dispatch on `name` + new kwargs (audio devices, sample rate, PTT, OFDM profile) |
| 3 | `operations.py:926` | pass PTT callable + resolved audio devices + OFDM config to the factory |
| 4 | `frames.py` flags | use free bit `0x40` for OFDM capability negotiation (§8.4) |
| 5 | `settings_dialog.py:855` | second combo item + `findData()` restore + conditional VARA-section enable/disable |
| 6 | `tests/test_stage4_dialogs.py:60` | asserts combo `count() == 1` — must be updated (legitimately, not weakened) |
| 7 | `shell.py:576, 590` | spectrum gating + header label for `ofdm_vhf` |
| 8 | readiness (3 surfaces) | per-backend requirement sets |

---

## 3. OFDM module architecture

```
guardian/ofdm/
    __init__.py        # public API re-exports
    config.py          # OfdmProfile, McsTable, validation
    constellation.py   # Gray map/demap, normalization, LLR output
    interleaving.py    # block interleaver (bit-level, symbol-spanning)
    framing.py         # PhyHeader, block segmentation, CRC, envelope
    phy.py             # OfdmModulator / OfdmDemodulator (pure numpy)
    sync.py            # preamble gen, burst detect, timing, CFO, tracking
    metrics.py         # LinkMetrics, EVM/SNR estimators, AdaptationState
    channel.py         # deterministic channel simulator (test + bench)
    link.py            # stop-and-wait ARQ over an abstract half-duplex pipe
guardian/payload/ofdm_vhf.py   # OfdmVhfBackend (PayloadBackend impl)
tools/ofdm_bench.py            # CLI bench, WAV read/write
docs/ofdm-vhf.md               # user/developer documentation
```

Dependency rule (enforced by imports, checked in review):
`guardian/ofdm/*` imports **only** `numpy`, stdlib, `guardian.modem.fec`,
`guardian.protocol.crc16`. Never Qt, sounddevice, vara, operations.
`payload/ofdm_vhf.py` is the only bridge to hardware concerns.

### 3.1 Real-audio OFDM approach — decision

**Chosen: DMT-style Hermitian-symmetric OFDM (real IFFT output).**

The transmit spectrum is assembled on positive-frequency bins only and the
time-domain signal is produced with `numpy.fft.irfft`, which enforces
`X[N−k] = X*[k]` implicitly. The receiver uses `numpy.fft.rfft`. This:

- produces mathematically real samples by construction (no discarded
  imaginary part),
- maps subcarrier index *k* directly to audio frequency `k·Δf` — the active
  carrier set **is** the occupied audio band, making the bandwidth
  parameterization trivial and honest,
- needs no digital up/down-conversion stage, no analytic-signal filtering,
  and no scipy.

Rejected alternative: complex baseband + cosine up-conversion to an audio
center frequency. It is equally correct but adds a mixer, an image problem,
and one more frequency parameter for no benefit at these bandwidths.
(Documented here so the decision is not re-litigated.)

Consequence: "CFO" through an FM audio path is small (FM discriminators do
not translate audio), but SSB paths translate the whole audio spectrum and
soundcard clocks differ (sample-rate offset appears as a slowly growing
timing/phase slope). Sync therefore handles: fractional-bin CFO, common phase
error, and pilot-phase-slope drift tracking (§5).

### 3.2 OfdmProfile — the parameter object (no magic constants)

```python
@dataclass(frozen=True)
class OfdmProfile:
    name: str                  # "BENCH", later "VHF_NARROW", ...
    sample_rate: int           # Hz (48000 default; != RF bandwidth!)
    fft_size: int              # N
    cp_length: int             # samples
    first_carrier: int         # lowest active bin index (DC null implied: bin 0 unused)
    num_carriers: int          # contiguous active bins  [first, first+num)
    pilot_spacing: int         # every P-th active carrier is a pilot
    preamble_symbols: int      # sync symbols count
    training_symbols: int      # channel-estimation symbols count
```

Derived (properties, never stored): `subcarrier_spacing = sample_rate/fft_size`,
`occupied_bandwidth = (first+num−first)·Δf` reported as
`[first·Δf, (first+num)·Δf]`, `symbol_duration = (N+CP)/fs`,
`data_carriers = num_carriers − pilots`. Everything guard/DC-related is
expressed by *which bins are absent from the active set* — bins below
`first_carrier` and above the set are the guard region; bin 0 (DC) and the
Nyquist bin are never active.

Validation (`config.py`): fft power of two; active set within `(0, N/2)`;
`cp_length < fft_size`; pilots ≥ 2. Profiles are registered in a dict; the
StationConfig stores only the profile *name* + MCS + timing knobs, so a future
bandwidth change is a new profile entry, not a modem rewrite.

### 3.3 MCS abstraction

```python
@dataclass(frozen=True)
class Mcs:
    index: int
    modulation: str        # "bpsk" | "qpsk" | "qam16" | "qam64"
    bits_per_symbol: int   # 1, 2, 4, 6
    code_rate: Fraction    # Fraction(1, 2) for all of Phase 1
```

Phase-1 table (rates all 1/2 because that is what `fec.py` provides):

| MCS | Modulation | Coded bits/carrier | Role |
|-----|-----------|--------------------|------|
| MCS0 | BPSK  | 1 | bootstrap/header + ACK/NACK, most robust |
| MCS1 | QPSK  | 2 | default data mode |
| MCS2 | 16-QAM | 4 | implemented + tested in sim, not default |
| MCS3 | 64-QAM | 6 | implemented + tested in sim, not default |

No thresholds, no rate claims. The PHY header (always MCS0) carries the data
MCS index, so the receiver never needs prior knowledge of the payload
modulation. Future FEC-rate variants and per-subcarrier maps extend this table
without changing the header format (header reserves 8 bits for MCS).

---

## 4. Waveform and frame design

### 4.1 Burst structure (one transmission)

```
[ TX lead silence (config, ms) ]           # PTT settle, not part of DSP frame
[ preamble: 2 Schmidl–Cox symbols ]        # detection, coarse timing, frac-CFO
[ training: 1–2 known full symbols ]       # LS channel estimate per carrier
[ header symbols: MCS0, fixed count ]      # versioned PHY/link header
[ data symbols: MCS from header ]
[ tail guard silence (config, ms) ]
```

### 4.2 PHY/link header (robust, versioned) — 16 bytes before coding

| Field | Size | Notes |
|-------|------|-------|
| version | 1 B | OFDM frame format version, starts at 1 |
| frame_type | 1 B | DATA / ACK / NACK / (reserved) |
| msg_id | 4 B | Guardian message id |
| block_seq | 2 B | this block's index |
| block_count | 2 B | total blocks in message |
| mcs | 1 B | MCS index of the data section |
| payload_len | 2 B | bytes in this block (bounded, §4.3) |
| flags | 1 B | reserved (future: bit-loading map present, etc.) |
| header_crc | 2 B | `crc16` over the previous 14 bytes |

Header is conv-encoded, interleaved, and sent as MCS0/BPSK — decodable at the
lowest SNR the link supports. A corrupt header ⇒ whole burst rejected.

### 4.3 Data section

`payload_len ≤ BLOCK_SIZE` (profile-level constant, e.g. 512 B for BENCH —
bounded blocks from day one; one Guardian attachment spans many
bursts/blocks). Data section = `payload bytes ∥ crc16(payload)` →
conv_encode → block interleave → QAM map → carriers. Payload CRC failure ⇒
NACK-able block, never returns wrong bytes.

### 4.4 Interleaving

Bit-level block interleaver over each coded section. Write row-wise into an
`R × C` matrix, read column-wise, with `C = coded bits per OFDM symbol`
(so adjacent coded bits land in different OFDM symbols **and** spread across
carriers): interleaved index `j = (i mod R)·C + (i div R)`. Deinterleave is
the transpose. This turns one destroyed symbol or a carrier notch into
isolated bit errors that the K=7 code can absorb. Depth `R` = number of data
symbols in the section (computed, not stored).

### 4.5 ARQ (link.py) — designed now, simulated-duplex this milestone

Stop-and-wait over an abstract `HalfDuplexPipe` interface
(`send(waveform) / receive(timeout) → samples`):

```
sender:  send DATA(seq) → wait ACK/NACK(timeout) → next / retransmit
         retries > ofdm_max_retries ⇒ fail
receiver: decode DATA → CRC ok ⇒ ACK(seq), dup(seq) ⇒ re-ACK, drop
          header ok + payload CRC bad ⇒ NACK(seq)
```

Timeout = airtime(ACK) + 2·PTT turnaround + margin — PTT turnaround is an
explicit named parameter, not folded into a magic number. ACK/NACK are
header-only bursts at MCS0. Duplicate suppression by `(msg_id, block_seq)`.
The same `link.py` state machine later runs over the real radio pipe; only the
pipe implementation changes (this is the "isolated remaining hardware
integration" required by the prompt).

---

## 5. Synchronization and channel estimation — the math

### 5.1 Modulation (TX)

Bin assembly `X[k]` for active carriers, then
`x[n] = irfft(X, n=N)` implements
`x[n] = (1/N) Σₖ X[k] e^{j2πkn/N}` with Hermitian symmetry ⇒ real `x`.
Cyclic prefix: prepend last `N_cp` samples. Per-symbol scaling to a target
RMS; report crest factor. Subcarrier spacing `Δf = fs/N`; symbol time
`T = (N + N_cp)/fs`.

### 5.2 Preamble & timing (Schmidl–Cox)

Preamble symbol uses only even-indexed active bins ⇒ time-domain halves
repeat: `x[n] = x[n + N/2]`. Detection metric with `L = N/2`:

```
P(d) = Σ_{m=0}^{L−1} r*[d+m] · r[d+m+L]
R(d) = Σ_{m=0}^{L−1} |r[d+m+L]|²
M(d) = |P(d)|² / R(d)²
```

`M` plateaus over the CP; take the plateau centroid (not the raw argmax) for
coarse timing, then refine to the symbol boundary by correlating against the
known preamble. `max M` is the **sync confidence** metric (0..1).
Detection threshold and plateau handling are profile constants with rationale
comments, validated by the delay/AWGN tests.

### 5.3 Carrier frequency offset

Fractional CFO from the preamble phase:
`ε̂ = angle(P(d̂)) / π` (in units of `Δf`, unambiguous for `|ε| < 1`).
Correct by multiplying `r[n] · e^{−j2πε̂n/N}` — note: the RX conveniently does
this on the analytic form of the real signal segment (obtained via `rfft`
processing per symbol; residual handled by pilots). Audio paths keep
`|CFO| ≪ Δf` (46.875 Hz for BENCH), so integer-bin CFO search is *designed*
(training symbol is bin-shift-detectable) but not needed for Phase-1 tests
beyond ±10 Hz.

### 5.4 Channel estimation & equalization

Training symbol with known `X_t[k]` on every active carrier:
LS estimate `Ĥ[k] = Y_t[k] / X_t[k]` (optionally averaged over 2 training
symbols: 3 dB estimator gain). Zero-forcing equalization
`X̂[k] = Y[k] / Ĥ[k]`; carriers with `|Ĥ[k]|` below a floor are flagged (feeds
future bit-loading; today they just produce low-confidence LLRs).
Per-carrier `|Ĥ[k]|²` is exported in `LinkMetrics.channel_response`.

### 5.5 Pilot tracking (residual CFO / phase / clock drift)

Pilots (every `pilot_spacing`-th active carrier, known BPSK values) per data
symbol: common phase error `θ̂ = angle( Σ_p Y[p]·Ĥ*[p]·X*[p] )`, applied to all
carriers; the *slope* of pilot phase vs. carrier index estimates residual
timing/sample-clock drift and is tracked cumulatively across symbols (first-
order loop). This is what makes two independent soundcards workable later.

### 5.6 Demapping, LLR, and metrics

- Normalization: `E[|s|²] = 1` ⇒ scale 1 (BPSK), `1/√2` (QPSK), `1/√10`
  (16-QAM), `1/√42` (64-QAM). Gray mapping per I/Q axis for square QAM.
- Soft output, max-log LLR per bit b:
  `LLR(b) = ( min_{s∈S₀(b)} |y−s|² − min_{s∈S₁(b)} |y−s|² ) / σ²`
  — sign convention **positive ⇒ bit 1** to match
  `viterbi_decode_soft` (`fec.py:52`). For BPSK/QPSK this reduces to scaled
  I/Q components (fast path).
- EVM (on equalized symbols vs. nearest/known constellation points):
  `EVM = sqrt( E[|y−ŝ|²] / E[|ŝ|²] )`; `SNR_est[dB] ≈ −20·log₁₀(EVM)`.
  Both training-based (known symbols, honest) and decision-directed (data)
  variants; metrics record which one produced the number.
- Theoretical anchors used by tests (§9): BPSK/QPSK coherent
  `P_b = Q(√(2·E_b/N₀))`; square M-QAM
  `P_b ≈ (4/log₂M)(1−1/√M)·Q(√(3·log₂M/(M−1)·E_b/N₀))`,
  `Q(x) = 0.5·erfc(x/√2)` — implement `erfc` via `math.erfc` (stdlib, no scipy).

### 5.7 Channel simulator (`channel.py`)

Deterministic, seeded (`numpy.random.default_rng(seed)`), composable ops on a
real sample vector:

| Impairment | Model |
|------------|-------|
| AWGN @ SNR | `σ = rms(x)·10^(−SNR_dB/20)`; SNR defined vs. in-band signal power |
| gain | scalar multiply (incl. < 1 and > 1) |
| delay | prepend `d` zero/noise samples, arbitrary `d` |
| frequency offset | `Hilbert-free`: `x·cos(2πf₀n/fs) ∓ x̂·sin(...)` via analytic signal from `rfft` zero-negative-bins trick (numpy-only), or equivalently complex-rotate in the demod test path; small `f₀` ≤ ±10 Hz |
| phase offset | applied with the frequency-offset op (`f₀=0, φ≠0`) |
| clipping | `clip(x, −c, c)` at configurable ratio of peak |
| multipath | FIR taps, e.g. `[1.0, 0.4·e^{jφ} @ τ]` (real projection), `np.convolve` |
| notch | zero/attenuate a band via `rfft` masking of the whole vector |
| resample drift (stretch goal) | linear-interp fractional resampling to emulate soundcard ppm offset |

The BER-vs-SNR sweep in the bench tool doubles as a sanity check against the
§5.6 theory curves (coded BER must sit left of uncoded).

---

## 6. BENCH profile (simulation/bench ONLY — not a VHF air claim)

All numbers below exist to make tests deterministic and conservative. The
real air profile will be derived from hardware measurements later; nothing
outside `config.py` may depend on these values.

| Parameter | Value | Derived |
|-----------|-------|---------|
| sample_rate | 48 000 Hz | |
| fft_size N | 1024 | Δf = 46.875 Hz |
| cp_length | 128 | 2.667 ms guard (≫ any audio-path delay spread) |
| symbol duration | (1024+128)/48000 | 24.0 ms |
| first_carrier | 12 | 562.5 Hz lower edge |
| num_carriers | 52 | bins 12–63 → upper edge 2953.1 Hz |
| occupied baseband band | ~562–2995 Hz | ≈ 2.43 kHz — fits a stock FM voice channel |
| pilot_spacing | 7 | 8 pilots, 44 data carriers |
| preamble / training | 2 + 2 symbols | |
| BLOCK_SIZE | 512 B | |
| MCS1 (QPSK r=1/2) net PHY rate | 44·2·(1/2)/0.024 | ≈ 1833 bit/s (bench figure, **not** an air claim) |

4096-byte acceptance payload = 8 blocks; per-block DATA burst ≈ 8 data-section
symbols + overhead — bench run completes in seconds of simulated audio.

---

## 7. Metrics / telemetry model

`guardian/ofdm/metrics.py`:

```python
@dataclass
class LinkMetrics:            # produced by the demodulator per burst
    sync_confidence: float | None
    cfo_hz: float | None
    snr_db: float | None      # None = unavailable, never faked
    evm_rms: float | None
    channel_response: np.ndarray | None   # complex, per active carrier
    audio_rms: float | None
    frame_ok: bool
    mcs: int | None

@dataclass
class OfdmStatus:             # published by the backend for UI polling
    state: str                # idle|synchronizing|receiving|transmitting|waiting_ack|failed
    mcs: int
    snr_db: float | None
    evm_rms: float | None
    retries: int
    tx_bytes: int
    rx_bytes: int
    last_block_ok: bool | None
    est_bitrate_bps: float | None   # measured from acked bytes/time, else None
```

`AdaptationState` (same file) accumulates per-transfer history
(PER, retransmit rate, per-carrier SNR) — the future adaptation controller's
input. Phase 1 only *fills* it and logs it; no decisions.

UI parity note: the VARA transfer panel reads `VaraSnapshot`
(`services/snapshots.py:26`); an equivalent lightweight snapshot for OFDM
keeps `transfer_progress.py` meaningful. Minimum viable this milestone:
byte counters + state so the progress panel is not blank; the rich
spectrum/constellation window is explicitly later.

---

## 8. Guardian integration design

### 8.1 Backend construction (no `if backend ==` sprawl)

`payload/__init__.py` becomes a real two-entry dispatch:

```python
_BACKENDS = {"vara_p2p": _make_vara, "ofdm_vhf": _make_ofdm}

def make_backend(name="vara_p2p", **deps):
    return _BACKENDS.get(name, _make_vara)(**deps)   # unknown → VARA (legacy safety)
```

`Operations._make_payload_backend()` grows only: `ptt=self._payload_ptt`
(new thin wrapper around `_radio_ptt` honoring the negotiated delay, sibling
of `_vara_ptt`), `audio_input=self.config.audio_input`,
`audio_output=self.config.audio_output`, `ofdm=self.config.ofdm_settings()`.
VARA path ignores the extras via `**_legacy` exactly as today, so
`make_backend("vara_p2p")` behaves bit-for-bit as before.

### 8.2 OfdmVhfBackend TX/RX lifecycle

Mirrors `vara_p2p.py` structure (worker thread, `_transfer_lock`, `done`
outside the lock):

```
_send(msg):
  on_acquire()                      # may raise → done(False)
  try:
      if on_qsy(msg) is False: fail
      link = OfdmLink(pipe=RadioAudioPipe(rx_dev, tx_dev, ptt, profile))
      ok = link.send_message(msg.msg_id, msg.payload_bytes)  # ARQ loop
  finally:
      on_unqsy(); on_release()      # exact VARA ordering
  done(ok)
```

`RadioAudioPipe` is the *only* hardware-touching class: resolves devices via
`modem.audio.resolve_device`, persistent `sd.InputStream` for RX (ring
buffer), blocking `sd.play/wait` for TX, and PTT strictly as:

```
ptt(True) → sleep(tx_lead_ms) → play(waveform) → wait() → sleep(tx_tail_ms)
finally: ptt(False)                # NEVER left keyed — tests 17/18 (§9)
```

Phase-1 reality check: over-the-air ARQ needs live turnaround; the milestone
ships `OfdmLink` fully tested over `SimulatedDuplexPipe` (channel simulator in
both directions) and `RadioAudioPipe` implemented + unit-tested for PTT/codec
ordering with a fake sounddevice; the first two-radio session is the *next*
milestone's opening task, not hidden scope here.

### 8.3 Configuration

`StationConfig` additions (flat fields per house style, §2.4):

```python
payload_backend: str = "vara_p2p"          # existing; allowlist fix in load()
ofdm_profile: str = "BENCH"
ofdm_mcs: int = 1                          # MCS1 QPSK r=1/2
ofdm_tx_lead_ms: int = 300
ofdm_tx_tail_ms: int = 100
ofdm_max_retries: int = 4
```

FFT size / CP / carriers / sample rate live in the **profile registry**, not
in StationConfig — operators pick a profile, developers define profiles. A
`config.ofdm_settings()` helper returns the resolved `OfdmProfile` + knobs.
Migration precedent to copy: `discovery_mode` rename (`config.py:261-269`).

### 8.4 Protocol / negotiation (compat-safe)

Wire protocol stays VERSION 1; `START_VARA` keeps its numeric identity and
internally means "start negotiated payload phase". Safety negotiation via the
free flags bits (`frames.py`, §2.3):

- `Flags.OFDM_PAYLOAD = 0x40`. Initiator configured `ofdm_vhf` sets it in
  `HAVE_MSG`; responder sets it in `ACK_HAVE` **iff** it is also configured
  `ofdm_vhf`. Payload phase uses OFDM only when both sides flagged; otherwise
  both silently fall back to VARA for that transfer (logged).
- Legacy stations never set `0x40` and ignore unknown flag bits on decode ⇒
  a VARA peer can never be tricked into listening for VARA while OFDM plays —
  the mixed pairing degrades to VARA before `START_VARA` is ever sent.
- Follow the PTT-delay precedent (`encode_ptt_delay`/`decode_ptt_delay`,
  `frames.py:102-127`): add `Flags` member + orchestrator merge in
  `_rx_have_msg`/`_rx_ack`, and carry the result in `Message` (new field
  `payload_transport: str`), consumed by Operations when constructing/looking
  up the backend for that transfer.
- Documented for later (in `docs/ofdm-vhf.md`): full transport negotiation
  (multiple transports, parameters) would use a `WORKING_OFFER`-style token
  frame pair; not needed while there are exactly two transports and one bit.

State names `STARTING_VARA`/`TRANSFERRING` are **not** renamed on the wire or
in code this milestone; only user-facing strings become transport-neutral
("starting payload transfer") where they currently say VARA unconditionally.

### 8.5 GUI (settings_dialog.py, per the operator's screenshot decision)

- The picker on the current "VARA payload" tab gains
  `addItem(dual("Guardian OFDM VHF (Experimental)", "Guardian OFDM VHF
  (Experimentální)"), "ofdm_vhf")` and a `findData(config.payload_backend)`
  restore (precedent: `control_modem` combo, `:876-884`).
- Tab retitle: `settings.vara` catalog entry → ("Payload", "Přenos dat") —
  wording generalized, widgets preserved.
- Selection toggles two group boxes on the same tab: VARA settings
  (disabled+collapsed when OFDM chosen) and a small read-only experimental
  OFDM summary: profile, MCS, sample rate, occupied baseband band,
  FFT/carriers — values rendered from the resolved `OfdmProfile`, no editable
  DSP knobs. Editable: MCS, TX lead/tail, retries only.
- `shell.py:590` header label: add `ofdm_vhf` → "OFDM VHF (exp.)" case;
  `shell.py:576` spectrum gating: keep VARA-only behavior (test at
  `test_qt_shell.py:174` continues to hold for unknown transports).

### 8.6 Readiness

`inspect_dependencies()` (`dependencies.py:89`) and the home table
(`shell.py:708`) become backend-aware:

- `vara_p2p`: rows unchanged (regression-pinned).
- `ofdm_vhf`: VARA FM/HF rows dropped or marked "not required"; added rows:
  RX device resolves to an index, TX device resolves, radio/PTT configured
  (CAT, VOX, or manual acknowledged), `OfdmProfile` validates. The VARA
  installer flow stays reachable but is never a blocker for OFDM stations.

---

## 9. Test plan — conditions that must hold

New modules: `tests/test_ofdm_constellation.py`, `test_ofdm_phy.py`,
`test_ofdm_sync.py`, `test_ofdm_channel.py`, `test_ofdm_link.py`,
`test_ofdm_payload.py`. Updated: `test_config.py`, `test_payloads.py`,
`test_operations.py`, `test_stage4_dialogs.py`, `test_qt_shell.py`,
`test_localized_ui.py` (bilingual strings). All DSP tests seeded
(`default_rng(0xA5)` unless the test's point is a seed sweep). No existing
test may be weakened; `test_stage4_dialogs.py:60` (`count() == 1`) is updated
to assert the new two-item truth *and* that VARA is item 0/default.

| # | Test | Concrete condition | Pass criterion |
|---|------|--------------------|----------------|
| 1–4 | constellation round trips | all 4 modulations, exhaustive symbol set + 10⁴ random bits | bits == demapped(mapped(bits)); `E[abs(s)²] == 1 ± 1e-9`; Gray property: adjacent points differ in 1 bit |
| 5 | noiseless waveform round trip | BENCH, each MCS, 512 B block | exact bytes, EVM < 1 %, `snr_db > 40` |
| 6 | delayed sync | delays {0, 1, 7, 4321, 48000} samples + leading noise floor | sync within ±2 samples after refine; payload exact |
| 7 | AWGN robust mode | MCS1 @ SNR 8 dB, 20 seeded runs | ≥ 19/20 blocks decode, zero *wrong-byte* deliveries; report measured SNR within ±2 dB of applied |
| 8 | small CFO | ±2 Hz, ±10 Hz @ 15 dB SNR | decode ok; `cfo_hz` estimate within ±1 Hz |
| 9 | multipath | taps `[1.0, 0.4 @ 48 samples (1 ms)]` @ 15 dB | decode ok; channel_response shows the ripple |
| 10 | notch | 3 adjacent carriers attenuated −30 dB @ 15 dB SNR | decode ok via FEC+interleaving (this is the interleaver's existence proof) |
| 11 | corruption rejection | flip bytes in payload section post-channel; corrupt header | header CRC ⇒ burst rejected; payload CRC ⇒ `frame_ok=False`, **never** wrong bytes to caller |
| 12 | segmentation | 4096 B and 4097 B payloads, BLOCK_SIZE 512 | correct block_count, reassembly exact, duplicate block re-ACKed and dropped |
| 13 | backend factory | `make_backend("vara_p2p")` / `("ofdm_vhf")` / `("unknown")` | VaraP2PBackend / OfdmVhfBackend / VaraP2PBackend |
| 14 | old config loads | 1.0.0-era JSON without OFDM keys; JSON with `winlink_manual` | loads, `payload_backend == "vara_p2p"`, OFDM fields at defaults |
| 15 | VARA default | fresh `StationConfig()` | `payload_backend == "vara_p2p"`; dialog item 0 is VARA |
| 16 | OFDM needs no VARA | config `ofdm_vhf`, no VARA exe anywhere | readiness has no VARA blocker; `_make_payload_backend()` succeeds without VaraClient interaction |
| 17 | PTT release on success | fake sounddevice + recording fake PTT | key/unkey strictly bracket playback; final state unkeyed |
| 18 | PTT release on failure | `sd.play` raises; `on_qsy` returns False; acquire raises | PTT unkeyed in every path; `done(False)`; `on_release` still called |
| 19 | codec ordering | fake hooks with event log (port of `test_payloads.py:372/:663` pattern) | order: acquire → qsy → … → unqsy → release → `done` |
| 20 | deterministic seeded sim | full ARQ over `SimulatedDuplexPipe`, seed 0xA5, SNR 12 dB, 1 forced NACK | identical bytes; retries == 1; run twice ⇒ identical metrics |

Plus: flags-negotiation tests in `test_session.py` (mixed-capability pairs
fall back to VARA; both-OFDM pairs mark `payload_transport == "ofdm_vhf"`),
and a bench-tool smoke test (runs, prints PASS block, exit code 0).

Runtime budget: full-suite delta < ~60 s (Viterbi at bench sizes is the
driver — see §11).

Run: `python -m pytest --basetemp <fresh-temp> -q` (basetemp per
`build.ps1:43`; the repaired `.venv` from 2026-08-07 is current).

---

## 10. Execution order (maps to the prompt's step list)

Each stage ends green (`pytest` passes) and committable.

| Stage | Content | Files | Exit criterion |
|-------|---------|-------|----------------|
| S1 | survey (done — §2 of this doc) | this doc | integration points documented |
| S2 | constellations + interleaver | `ofdm/constellation.py`, `interleaving.py` + tests | tests 1–4 |
| S3 | profile + modulator/demodulator, ideal channel | `ofdm/config.py`, `phy.py` + tests | test 5 |
| S4 | sync + channel estimation + metrics | `ofdm/sync.py`, `metrics.py` + tests | tests 6, 8, 9 |
| S5 | framing + FEC glue + channel simulator | `ofdm/framing.py`, `channel.py` + tests | tests 7, 10, 11, 12 |
| S6 | ARQ link over simulated duplex | `ofdm/link.py` + tests | test 20 |
| S7 | bench tool + WAV | `tools/ofdm_bench.py` | acceptance output block (§12) |
| S8 | backend + factory + config allowlist | `payload/ofdm_vhf.py`, `payload/__init__.py`, `config.py` + tests | tests 13–16 |
| S9 | Operations wiring, PTT/codec lifecycle | `operations.py` + tests w/ fakes | tests 17–19 |
| S10 | GUI + readiness + i18n | `settings_dialog.py`, `shell.py`, `readiness/deps`, `i18n.py` + tests | dialog/readiness tests; `test_localized_ui` |
| S11 | flags negotiation (0x40) | `protocol/frames.py`, `orchestrator.py` + `test_session.py` | mixed-pair fallback proven |
| S12 | docs + full suite + release | `docs/ofdm-vhf.md`, `docs/RELEASE_NOTES_2.0.1.md`, `_version.py` | §12 checklist |

Release G2.0.1 (per `docs/RELEASING.md` + `G2.md` caveats): bump
`_version.py` to `2.0.1` → write `docs/RELEASE_NOTES_2.0.1.md` (mandatory,
path-checked) → full `pytest` → local `.\build.ps1` → `.\build_installer.ps1`
→ commit on `g2` → tag `v2.0.1` pushed deliberately to the `g2` remote only
(Actions disabled; do not push the tag to the public remote — `release.yml`
still hard-codes the public `-ReleaseBaseUrl`).

---

## 11. Risks and open questions

1. **Viterbi throughput.** `fec.py`'s 64-state pure-Python loop decodes
   roughly control-burst-sized frames today. A 512 B block ⇒ ~8.3 k coded
   bits ⇒ ~0.5 M state operations — fine per block, but keep an eye on it in
   the bench; mitigation order: vectorize the trellis over states with numpy
   (drop-in, same module), only then consider anything heavier. Do **not**
   fork a second FEC implementation.
2. **Soundcard sample-rate offset** between two stations (tens of ppm) shows
   up as pilot-phase slope; the §5.5 tracker is designed for it but only the
   stretch-goal resample impairment actually tests it. Flag as known-untested
   in `docs/ofdm-vhf.md` if the stretch goal slips.
3. **FM deviation / soundcard AGC** on real radios will set the usable
   dynamic range and crest-factor budget — unknowable until hardware tests;
   this is exactly why occupied bandwidth stays profile-driven.
4. **`orchestrator.py:167` wire-size duplication** — untouched now; when OFDM
   gets its own session-level transfer timeout, factor `payload airtime` into
   a backend-provided hint instead of a third constant.
5. **Version interpretation**: "G2.0.1" is implemented as version string
   `2.0.1` (satisfies the CI `^\d+\.\d+\.\d+$` regex and the G2.md
   requirement that G1/G2 versions be unconfusable — G1 stays 1.x).
6. **PySide6-Essentials only** — no Qt Charts; the future
   spectrum/constellation window must render via QPainter like the existing
   `spectrum_window.py`.

---

## 12. Acceptance checklist for G2.0.1

- [ ] `python tools/ofdm_bench.py` prints the block from the original prompt
      (profile BENCH, 48000 Hz, MCS1 QPSK, FFT 1024, 44 data carriers,
      occupied ~0.56–3.0 kHz *profile-derived*, 4096 B payload, simulated +
      measured SNR, EVM, retries, `result: PASS`, `RX payload identical: YES`)
      — through the deterministic simulated channel, not a perfect array pass.
- [ ] Bench can write a WAV of the burst and decode a WAV back to bytes.
- [ ] Settings show `Guardian VARA P2P` (default) and
      `Guardian OFDM VHF (Experimental)`; selecting OFDM hides/disables VARA
      requirements and shows the experimental summary; EN + CS strings.
- [ ] Readiness: OFDM station with no VARA installed shows ready (audio +
      PTT + profile checks instead).
- [ ] All 20 required tests (§9) plus negotiation tests pass; full suite
      green with no weakened assertions.
- [ ] PTT can never remain keyed after any failure path (tests 17/18).
- [ ] `docs/ofdm-vhf.md` documents architecture, what is NOT implemented,
      frame structure, BENCH profile, config, measured sim performance,
      limitations, adaptive-MCS and bit-loading forward design, and the
      two-radio VHF test procedure — and states explicitly that sample rate ≠
      occupied RF bandwidth and that no over-the-air rates are claimed.
- [ ] `_version.py == "2.0.1"`, `docs/RELEASE_NOTES_2.0.1.md` exists, local
      build + installer produced from the tagged commit on `g2`.
