# Guardian (ARDOS) — Project Status & Plan

_Last updated: 2026-07-31_

A resumable snapshot: what Guardian is, what's built, what's verified, the key
decisions and why, and what comes next. Read this first when picking the project
back up.

---

## 1. Vision

Guardian is a Python/Windows **control & routing layer that sits in front of
VARA FM/HF** for amateur-radio emergency messaging (project codename **ARDOS**).

Short **control bursts carry metadata only** (source, final destination, next
hop, message id, priority, TTL, flags + CRC). The actual message body travels
over VARA once two stations have negotiated the hop. Guardian is the fast signalling/dispatch layer; VARA is the data modem.

Radio control is via **Hamlib / rigctld** (hundreds of rigs) with a serial
RTS/DTR VOX fallback — no per-radio CAT reverse-engineering.

---

## 2. Status by phase

| Phase | Goal | Status |
|------:|------|--------|
| 1 | UI, station config, control-burst protocol, radio drivers | ✅ done |
| 2 | VARA handshake state-machine (orchestrator) | ✅ done |
| 3 | Control modem (AFSK + MFSK) + payload backend + audio channel | ✅ done* |
| 4 | Smart routing / heard-stations | ✅ done |
| 5 | Multi-channel scanning / mesh | ✅ done* |
| 6 | Mail layer: store-and-forward + attachments | ✅ done |

**Phases 4 & 5 done in software.** Heard-stations registry (populated from every
RX frame), ROUTE_QUERY/ROUTE_OFFER route discovery, learned-path memory,
multi-hop auto-relay (TTL decrement + loop avoidance), and tick-driven channel
scanning (dwell + activity hold). Tested over the loopback bus: A discovers a
route to C with no manual entry; A→B→C relay chain delivers end-to-end; TTL=1
correctly stops relaying. (*) Channel scanning needs a real radio to tune.

**Phase 3 done in software.** Built + tested: AFSK 1200 modem, MFSK-16 HF modem
(decodes to ~0 dB SNR in loopback), rate-1/2 K=7 convolutional FEC + Viterbi,
the VARA P2P payload backend, audio device pickers, and a loopback↔audio control-channel
selector (the audio transport starts/stops cleanly with real devices). Only
remaining (*needs hardware*):
- **On-air verification** — AFSK/MFSK over the real USB codec + PTT on a rig
  (loopback-cable test, then on-air). Audio I/O path is wired and starts, but
  not yet round-tripped through a radio.
- MFSK bit-sync is tuned for clean audio; real fading may want a PLL/soft-decision.

---

## 3. What's verified vs. needs hardware

**Verified on air, OK7PS ↔ OK2IPW, 2026-07-28** (145.2375 / 145.300 MHz FM,
IC-705 both ends, Hamlib + USB codec, host PTT via CAT):

- The full control handshake over real AFSK 1200:
  `HAVE_MSG → ACK_HAVE → START_VARA → RECEIVED → DELIVERED`, both directions.
- `vara_p2p` payload transfer end-to-end, both directions, over the primary
  `BUFFER`-drain path. A 370-byte message in 14 s; a 34 KB attachment in
  4 min 04 s with 48 `BUFFER` reports and no data-socket reopens.
- Auto-QSY per station (route `freq_hz`) before connecting, and the control
  channel handing the shared codec to VARA and taking it back.
- Attachments survive the bundle and open from the reader.
- Occasional lost control bursts are normal and handled: `RX bad frame: bad
  magic` appears in most runs and the retry logic covers it.

Two things bit us on the way and are worth remembering:

1. Guardian sent `LISTEN OFF` immediately before `CONNECT`. The reference warns
   that either `LISTEN` command "will cause a disconnection if it is received
   in the middle of a VARA connection". The RF link came up but the port 8301
   bridge never attached: no `BUFFER` ever arrived and VARA idled at a fixed
   1.87 s keying cycle with nothing to send. Fixed in 0.6.20.
2. **`BUFFER` is the fastest diagnostic there is.** It is "sent when VARA adds
   data to queue" — before anything is transmitted. `buffer_reports: 0` in the
   diagnostics therefore proves the payload never reached VARA and rules out
   every RF, PTT-timing and airtime explanation in one step.

Speed levels sat at 566/1188 bps because the stations were unregistered; VARA
FM caps the unregistered rate. OK7PS is licensed as of 2026-07-28, so the
ceiling should lift. `UNREGISTERED_FM_BPS` in `payload/vara_p2p.py` is only a
fallback for when VARA reports no `BITRATE`; the real rate is parsed and used.

**Verified in software (no radio):**
- Control-frame encode/decode + CRC-16 round-trip.
- Full handshake over a loopback channel: direct delivery, relay hop, and
  no-responder retry→fail. (`HAVE_MSG→ACK_HAVE→START_VARA→RECEIVED→DELIVERED`,
  plus BUSY/CANCEL, ACK timeouts, backup-hop fallback.)
- AFSK 1200 modem loopback: clean + noisy, at 8 k / 44.1 k / 48 k, multi-burst.
- The VARA P2P backend drives a session to DELIVERED (envelope round-trip).
- Hamlib install + SHA256 verify; rigctld runs; live `rigctl -l` parse (312
  models); curated radio ids validated against installed Hamlib.
- UI builds/renders; tray + window icon; USB-serial detection.

**Confirmed working on air 2026-07-29 (both bands):** VARA FM and VARA HF
carry mail end-to-end; the MFSK-16 HF control channel closes the handshake on
21 MHz USB; the presence beacon and per-station auto-QSY are verified in live
operation. Tactical 2750 Hz HF bandwidth exposed one last defect — the setting
never reached VARA unless it happened to be set before connecting — fixed in
0.6.33.

**Confirmed working on air 2026-07-30:** net alerts (0.6.34) on both control
modems — MFSK-16 on HF and AFSK-1200 on FM. One open observation, see the
watch-list: an occasional `RX bad frame: bad magic` alongside an alert.

**Built but not yet flown (0.6.35–0.6.44):** the alert frequency sweep (an
alert is repeated on every other frequency in the route table, then the radio
goes back), the heard-stations S/N estimate + channel column, and the
negotiated VARA FM slow-keying gap. **Confirmed working in the field:** Test PTT and no-CAT keying via Hamlib
dummy + serial PTT (AIOC, 0.6.38, 2026-07-30); audio devices on Windows on
ARM (0.6.42) and the AFSK control channel there, handshake both ways at
39–48 dB (2026-07-31); the negotiated VARA FM slow-keying tail (0.6.40) —
"funguje výborně".

**Needs real hardware/peers to verify:**
- **VARA HF control channel — WORKING as of 0.6.32, confirmed on air
  2026-07-29 evening** (21.189 MHz USB, IC-705 both ends). Five faults, all
  found from operator captures against known frame contents:

  1. Tone geometry followed the sample rate instead of staying fixed (0.6.29).
  2. The RX window was smaller than one frame, so nothing was attempted (0.6.30).
  3. The timing search only probed the head of the window; the preamble is now
     found anywhere in it (0.6.31).
  4. No AFC — two in-spec IC-705s differ by ~21 Hz at 21 MHz; measured
     −8.5 Hz, now corrected by fitting the tone grid (0.6.31). The grid was
     also widened to 400–2275 Hz / 125 Hz spacing on operating advice, which
     cut a HAVE_MSG from 6.3 s to 1.6 s and quartered offset sensitivity.
  5. **Stream teardown discarded ~130 ms of buffered TX audio** — the tail of
     every burst, i.e. the CRC. At 32 ms/symbol the FEC hid it as one bad bit;
     at 8 ms/symbol it was 4 bad bytes. Fixed with 400 ms of trailing guard
     silence (0.6.32). This was the decisive one.

  The lesson that found #5: demodulate operator captures **symbol by symbol
  against ground truth** (frame contents known from the peer's log). Envelope
  and CRC analysis pointed everywhere; the per-symbol margin profile said
  "signal absent after symbol 131" unambiguously.

  Previously (superseded): flown 2026-07-29 on 21.189 MHz
  USB. Three modem faults were found and fixed (tone geometry at the device
  sample rate, 0.6.29; RX window too small for a slow frame, 0.6.30), and one
  frame did decode: `RX Route Query src=OK2IPW`. The rest fail CRC as *near
  misses* — 32 of 33 bytes correct with a single bit wrong in the last byte.

  Measured from OK7PS's `last-bad-control.wav` (frame contents known from
  OK2IPW's log, so every figure below is against ground truth):

  | | |
  |---|---|
  | preamble located (full-window search) | 3.492 s, match 0.972 |
  | frequency offset | **-8.50 Hz** |
  | symbols wrong after correcting it | 4 of 157 |
  | decision margin, best/second tone | median 30.3, min 1.13 |

  Three things follow, none of them yet implemented:

  1. **The timing search only probes the first two symbols of the RX window.**
     The window is 8.8 s and a burst can start anywhere in it — this one was at
     3.5 s. It has been finding frames by luck. A full-window preamble search
     scores 0.972 and is cheap if it correlates only tones 0 and M-1.
  2. **There is no AFC.** Two IC-705s each inside a +-0.5 ppm TCXO spec differ
     by ~21 Hz at 21 MHz, against 31.25 Hz tone spacing. This is a design gap,
     not a tuning error: it cannot be fixed by aligning dials. Correcting the
     measured -8.50 Hz cuts the errors but leaves 4 symbols wrong.
  3. **Demodulation is hard-decision.** The margin profile is the giveaway:
     almost every symbol is decided 30:1, and two are near coin flips at 1.13.
     `argmax` throws that confidence away before the Viterbi ever sees it.
     Soft-decision input is the standard answer to exactly this profile and is
     the change most likely to close the gap.

  Four fixes were attempted on 2026-07-29 and **all reverted**: none made the
  capture decode, because the frame could not be located in the window and the
  offset was being measured in noise. The full-window preamble search above is
  what makes the next attempt verifiable against real audio rather than
  simulation.

- **VARA HF payload transfer.** Never run on air. The path is the same as FM, and 0.6.24 adds
  the one command the reference marks as required for peer-to-peer work
  (`P2P SESSION`, HF/SAT only — without it VARA HF keeps the 4.0 s Winlink
  gateway retry cycle). Note both HF and FM ports default to 8300/8301, so the
  two VARA flavours cannot run at once until one pair is changed.
- **Multi-hop relay on air.** Only the two-station direct case has flown; the
  A→B→C chain is loopback-tested only.
- **Presence beacon and auto-delivery.** Both were dead switches until 0.6.27
  (`send_beacon()` had no caller; `auto_deliver` had no consumer at all despite
  defaulting to on). Now wired and unit-tested, never run on air. They are the
  only two behaviours that key the radio without an operator asking, so they
  are gated on a live control channel, no session in flight and no payload
  transfer holding the codec.
- Channel scanning against a physical radio (tune/mode via rigctld).
- MFSK-16 over a real HF path (bit-sync is tuned for clean audio).

---

## 4. Architecture map

```
guardian/
  app.py              entry point (sets Windows AppUserModelID, launches UI)
  config.py           StationConfig (JSON) + VARA mode + payload backend helpers
  protocol/
    frames.py         ARD control-burst encode/decode, CRC-16, frame types
  routing/
    route_table.py    configurable dest -> preferred/backup next hop
    heard.py          heard-stations registry (Phase 4)
  radio/scanner.py    channel plan + tick-driven scanner (Phase 5)
  message/            >>> Mail layer (Winlink-like) <<<
    mail.py           MailMessage + Attachment + ZIP bundle (text + files + hops)
    store.py          persistent mailbox: inbox/outbox/sent/transit + index
  radio/
    base.py           RadioDriver interface + NullRadio
    hamlib.py         rigctld TCP backend (freq/mode/PTT/S-meter)
    generic_vox.py    serial RTS/DTR PTT fallback
    presets.py        curated radios + live `rigctl -l` catalog
    rigctld_launcher.py  start/stop rigctld for the user
    usb_serial.py     detect USB-serial chipset by VID:PID -> driver link
  vara/
    client.py         VARA command/data TCP client (+ data read/write helpers)
  session/            >>> Phase 2 <<<
    transport.py      ControlTransport interface + LoopbackBus (sim channel)
    orchestrator.py   the handshake state-machine (SessionState, Message)
  modem/              >>> Phase 3 <<<
    afsk.py           AFSK 1200 (Bell 202) modulate/demodulate (numpy)
    mfsk.py           MFSK-16 HF modem (16 Gray tones + FEC, ~0 dB SNR)
    fec.py            rate-1/2 K=7 convolutional code + Viterbi decoder
    audio.py          AudioControlTransport (modem <-> sounddevice + PTT) + device list
  payload/            >>> Phase 3 <<<
    base.py           PayloadBackend interface
    vara_p2p.py       Guardian-owns-VARA direct P2P transfer
  install/
    hamlib_installer.py  download + SHA256-verify + unpack official Hamlib
  assets/
    icon.py           generated Guardian shield .ico / tray image (Pillow)
  ui/
    main_window.py    CustomTkinter UI (tabs below)
```

UI (reorganised for a guided journey): operational tabs **Home · Mail · Net ·
Mesh · Log** up front, all configuration under **⚙ Settings** (sections:
Station, Radio, VARA, Channel, Mesh, Routing, Advanced). **Home** has an
operating-mode selector and a mode-aware
**setup checklist** that shows what to configure first (with "Go" buttons
jumping to the right settings section) plus live status cards. The sidebar
shows callsign, mode, Radio/VARA/PTT/Control-channel dots and mailbox counts.

The control flow is transport-agnostic: the production UI uses
`NullTransport` while idle and `AudioControlTransport` for real RF.
`LoopbackBus` remains available to automated tests. Payload is handled by
`vara_p2p` behind the `PayloadBackend` interface; swapping it does not touch
the state-machine.

---

## 5. Key decisions & rationale

- **VARA is just a modem, not a messaging system.** One VARA instance = one
  command port = one master, so Guardian and Winlink Express cannot share one
  VARA. Therefore control bursts always travel **outside** the VARA data session
  on their own AFSK/MFSK modem (wake/announce/route-negotiation happen before any
  connection exists). The control modem is needed regardless of payload backend.
- **Payload backend: `vara_p2p` only** (2026-07-28). A `winlink_manual`
  hand-off shipped alongside it from 2026-06-01 as insurance while P2P was
  unproven on air. Once two-station transfers worked it only offered a slower
  manual workflow, so it was removed in 0.6.26; the `PayloadBackend` interface
  and the settings picker stay for the next transport (Pat HTTP-API remains a
  candidate).
- **Modulation:** AFSK 1200 (Bell 202) for VARA **FM**; MFSK-16 + FEC planned for
  VARA **HF** (AFSK is too wide and lacks capture effect on SSB). Auto-selected
  by `vara_mode`.
- **Hamlib over rigctld (TCP), not direct CAT.** Massive rig support, Windows +
  Linux, multiple apps can share the radio.
- **Do NOT bundle USB-serial kernel drivers.** Admin-only, can brick working
  devices (esp. Prolific counterfeit lockouts), licensing grey area. Instead we
  detect the chipset and link the official driver.
- **Stack:** bundled Python 3.11+, PySide6, PyInstaller/Inno Setup, numpy,
  sounddevice, pyserial and Pillow. The production UI no longer depends on
  Tk, CustomTkinter or pystray.

---

## 6. How to run / build / resume

```powershell
# Fresh PC (Python 3.12 must be installed):
.\setup.ps1                 # venv + deps
.\setup.ps1 -WithHamlib     # also download Hamlib binaries

# Run from source:
.\run.ps1                   # == .venv\Scripts\pythonw.exe -m guardian

# Standalone exe:
.\build.ps1                 # -> dist\Guardian\Guardian.exe (custom shield icon)
.\make_shortcut.ps1         # Desktop + Start Menu shortcuts to the exe
```

The PyInstaller entry point is the top-level `guardian_launch.py` (absolute
import) — NOT `guardian/__main__.py`, whose relative import breaks the frozen
build. `dist/` and `build/` are git-ignored, so each machine builds its own exe
(or you zip and copy `dist\Guardian\`).

Per-station state lives in `%APPDATA%\Guardian\`:
`config.json`, `routes.json`, `guardian.ico`, and `hamlib\` if installed.

Dev note (this machine): venv at `.venv`, Python at
`%LOCALAPPDATA%\Programs\Python\Python312`. Headless tests run with
`PYTHONPATH` = project root and `PYTHONUTF8=1`.

### Source control
- Remote: `https://github.com/bubakbubak500/ARDOS-Guardian.git` (branch `main`).
- Commit identity (local repo config): `bubakbubak500` /
  `bubakbubak500@users.noreply.github.com` (keeps real email out of public
  history; not set globally).
- `.venv/`, `build/`, `dist/`, `config.json`, `routes.json`,
  `.claude/settings.local.json`, and the generated `guardian.ico` are
  git-ignored. `.gitattributes` normalises text to LF (PS1 stays CRLF).

---

## 7. Future plans

All five planned phases are software-complete. What remains is hardware bring-up
and polish.

**Hardware bring-up (the big remaining gap)**
1. **On-air control channel** — Net tab → audio devices + control channel
   "audio": loopback-cable test (TX into RX), then on-air with a real rig. Add
   RX level/squelch meters; confirm PTT keys via the radio driver.
2. ~~**Live `vara_p2p`**~~ — **DONE 2026-07-28.** First successful two-station
   on-air transfer, OK7PS ↔ OK2IPW on 145.2375 MHz FM, IC-705 both ends. A
   370-byte message went out in 14 s over the primary `BUFFER`-drain path
   (`RF queue drained` → `transmitted and VARA link closed` → end-to-end
   `DELIVERED`), and OK2IPW's 362-byte reply came back the same way. VARA
   stepped 566 → 1188 → 2390 bps as it moved real data.

   What had blocked it was Guardian's VARA session setup, not RF. Checked
   against *VARA Protocol Native TNC Commands* (EA5HVK, 2025-10-10): Guardian
   sent `LISTEN OFF` immediately before `CONNECT`, which the reference warns
   "will cause a disconnection if it is received in the middle of a VARA
   connection". The link came up at RF level but the port 8301 bridge never
   attached — no `BUFFER` notification ever arrived, and VARA idled at a fixed
   1.87 s keying cycle with nothing to send. Fixed in 0.6.20 together with
   `CHAT OFF` (bounds VARA's idle loops) and re-enabled `COMPRESSION TEXT`;
   the three went out at once, so the individual contribution is not isolated.

   Diagnostics gained `buffer_reports`, `ptt_keyings`, `tx_bitrate_bps`,
   `rejected_commands`, `transport_lost` and data-socket health — a zero
   `buffer_reports` is the fastest way to spot this class of fault again.
3. **Channel scanning on a real radio** — verify tune/mode via rigctld; wire an
   activity threshold from the S-meter.

**Open, deliberately not built yet** (each needs an operator decision first)
- **Topology import for mesh routing.** The 0.6.26 CSV imports *routes*, which
  are directional and therefore only correct at one station. Importing *links*
  instead and deriving each station's table locally makes one shared file
  correct everywhere. Written up in `docs/MESH_ROUTING.md`; worth building at
  four or five stations, not at two.
- **`COMPRESSION FILES` for binary attachments.** The reference marks it
  "designed for File transfers" against `TEXT`'s Huffman. Unmeasured, and a
  JPEG is already compressed, so the gain may be nil. Whether the setting is
  sender-side only or must match at both ends is **not established** — verify
  before shipping it.
- **`CLEANTXBUFFER`** — rejected 2026-07-28. Registered-user only, so it
  cannot be relied on across a net of mixed licences.

**Robustness / polish**
- MFSK soft-decision Viterbi + PLL bit-sync for real fading (current sync is
  tuned for clean audio).
- Heard-station signal quality from the modem (SNR) to rank relay candidates.
- Sanity-check an imported route table against heard stations and warn on a
  next hop this station has never heard (see `docs/MESH_ROUTING.md`).

**Cross-cutting TODO**
- Persist session history / message log to disk.
- Encryption/compression flags are defined in the frame but not yet applied.
- Config validation + first-run wizard.
- Unit tests promoted into a real `tests/` suite + CI.

---

## Mail layer (Winlink-like)

`guardian/message/` + the **Mail** UI tab give a store-and-forward mailbox:
- **MailMessage** = subject + body + attachments, serialised to a compressed
  ZIP bundle (manifest + body.txt + att/) carried over VARA P2P inside the CRC
  envelope. Route history (hops) travels in the manifest.
- **MessageStore** persists to `%APPDATA%\Guardian\mail\` with folders
  **Inbox** (for me), **Outbox** (queued), **Sent**, **Transit** (held to
  forward for others — "waiting pickup"). Lightweight `index.json` + per-message
  `.bundle` files.
- **Mail tab**: folder sidebar with counts, message list, reading pane with
  per-attachment Save/Open, Compose dialog (To/Subject/Body/Attach/Priority)
  with a size + estimated on-air-time hint. Outbound status tracks the session
  (Outbox→Sent on delivery); inbound bundles auto-file to Inbox or Transit.
  "Simulate receive (demo)" lets you exercise the receive/read/attachment UX on
  one PC without a radio.

## Mail extras (latest)
- **Globally-unique message IDs** — 12-bit station-hash prefix + 20-bit counter
  (fits uint32), so two stations almost never collide.
- **Read/unread** — incoming starts unread (● + "N new" badge), marked read on
  open. **Reply** button on Inbox messages prefills a quoted reply.
- **Presence beacon + auto-deliver** — a station can beacon "I'm here"; holders
  auto-send waiting Outbox/Transit mail when the next hop becomes *heard*
  (Mesh-tab toggles: auto-deliver, beacon). This is the "pickup when the
  recipient shows up" behaviour.
- **Forms** — ICS-213 and SITREP templates in Compose render to clean,
  fixed-layout bodies with auto subjects.

## Live monitoring & QSY (latest)
- **Signal & audio metering** — the audio control channel computes RX RMS
  level, peak and a slow **noise floor**; shown as an RX bar in the sidebar and
  a "Signal & audio" card on Home with a plain-language hint (e.g. "high noise
  floor — check local interference"). S-meter from Hamlib is shown when present.
- **Per-station frequency + auto-QSY** — route entries gained optional
  `freq_hz`/`mode`. In **VARA P2P** (only), before connecting to the next hop
  Guardian tunes the radio to that station's frequency and restores afterwards
  (`auto_qsy`, Hamlib only). Ignored for Winlink (operator tunes) and VOX
  (can't tune). Caveat: assumes the peer is on its home freq and control bursts
  share the current channel — a full calling/working-frequency split is future.

## Net alerts (0.6.34 — confirmed on air 2026-07-30, HF and FM)
Tested end to end with OK2IPW on **both** control modems: MFSK-16 on HF and
AFSK-1200 on FM. Broadcast, display and relay all behave as designed.

- **One byte, whole net.** `FrameType.ALERT` carries an alert code plus a
  short ASCII note in the *existing* control frame (`protocol/alerts.py`), so
  no station configuration changes and older builds ignore the unknown type.
  25 note bytes fit beside a 5-character callsign, 21 beside a 7-character one.
- **Code, not text** — the byte expands to a translated sentence at display
  time, so each operator reads the alert in their own language. Codes are
  permanent once used on air: add, never renumber.
- **Flood**: originator repeats 3× at 10 s; receivers display once (dedup by
  message id, remembered an hour), then relay TTL−1 after a 1–5 s jitter
  derived from the callsign, keeping the originator in `source` and their own
  call in `next_hop`.
- **UI**: red-bordered banner under the station context bar (amber for routine
  codes), and an *Alert* button beside Compose with a code picker, per-code
  note hint, character counter and a confirmation step.

## Alert frequency sweep (0.6.35 — not yet flown)
An alert only reaches whoever is listening where we are tuned, so the route
table's `freq_hz` entries are reused as an alert channel list.

- Home frequency first (the usual 3 copies at 10 s). The sweep **waits for that
  queue to drain** (bounded at 45 s), then visits each other frequency: QSY +
  mode, 0.6 s settle, **2 copies 3 s apart**, next channel. Frequency *and*
  mode are restored at the end, including when the sweep is cut short.
- **Same message id on every channel**, so a station hearing two of them still
  displays once and relays once. The wire format is untouched.
- Bounded: **10 extra channels**, duplicates collapsed, current frequency
  skipped. Each channel is attempted independently — a failed QSY costs that
  channel and is logged with its frequency. Stops early (and still restores) if
  the control channel is stopped or VARA takes the codec.
- Runs on a worker (`alert-sweep`), so the UI never blocks. In the send dialog
  a checkbox is ticked by default for EMERGENCY/PRIORITY codes only, and the
  confirmation says the radio will be retuned.
- **To verify on air:** whether 2 copies per channel is enough on HF, and
  whether the 45 s home wait feels too long before the first QSY.

## Test PTT (0.6.36, corrected in 0.6.37)
`Settings → Radio control` ends with a **Test PTT** button:
`Operations.run_ptt_test()` waits for control TX to idle, opens the radio if
needed (same rigctld path as *Connect*), keys for 2 s and **reads `get_state()`
back while keyed**, so a driver that accepts `T 1` and transmits nothing is
distinguishable from a real key. Unkey is in a `finally`; hold capped at 5 s;
refused with no radio backend, during a payload transfer, or when
`radio-control` is busy, and refused for unapplied dialog values (the live
driver still holds the old ones). No confirmation dialog — the warning sits
under the button; results go to the status line and the log.

Whether the read-back means anything is `RadioDriver.reports_ptt`, **not** the
returned flag: 0.6.36 read `VoxRadio.get_state().ptt`, which is the RTS/DTR
line Guardian had just asserted, and reported a dead cable as "the radio
reported TX". Hamlib (`t`) confirms; VOX/serial says it cannot. The
still-asserted check applies to both.

## Station positions and the map (0.6.44)
Beacons carry a **Maidenhead locator** in the address field (unused for a
broadcast, same trick as alerts; wire format untouched, old builds ignore it).
Binary lat/lon is impossible there — the field is ASCII and upper-cased, so
bytes >127 come back as `?`. Ten characters (~50 × 90 m) fit beside any
callsign Settings accepts; `beacon_locator_room()` rounds the room down to a
whole pair, because an odd truncation names a different square.

- `routing/grid.py` — encode/decode/bounds/haversine, one code path for all
  precisions via the 18/10/24/10/24 division table. Verified against six
  cities' documented squares.
- `config.station_grid` + `beacon_position` (default True, but beacons
  themselves are off by default, so nothing goes out unasked).
- **Only `FrameType.BEACON` sets `HeardStation.grid`** — a group named
  `JN89HE` parses as a locator, and reading that destination as a position
  would place the sender in Brno.
- `qt/map_window.py` — QPainter, equirectangular. **No QtWebEngine**: Leaflet
  would add ~150 MB to a 41 MB installer and want a network we exist to
  survive without. Works with **no map data at all**; an offline `.mbtiles`
  background is a later layer, never a dependency.
- Heard table gained Locator + Distance (`184 km 121°`), empty until this
  station knows its own position.

**Next, if wanted:** optional offline raster background. `.mbtiles` (SQLite,
stdlib `sqlite3`, no new dependency) in `%APPDATA%\Guardian\maps\`. Note that
bulk downloading from public OSM tile servers is against their terms — either
the operator supplies the file or we target a source that permits it.

## VARA host PTT is the default (0.6.43)
Field report: OK2IPW's VARA produced a full session (`CONNECT`, `BITRATE`,
`PTT ON/OFF`, `DISCONNECTED`) with nothing on air, and both directions failed.
Cause: `vara_host_ptt: false` **and** rigctld holding `COM3` — Guardian
ignored VARA's PTT and VARA had no port left to key through. Nothing to do
with ARM; the control channel was working the whole time.

- `config.vara_host_ptt` now defaults **True**. Existing profiles that store
  `false` are left alone (they may be keying via VARA on purpose; taking over
  could double-key).
- `Operations._warn_if_nothing_can_key_vara()` — logged at the codec handoff
  when host PTT is off and `radio_backend == hamlib` with a `cat_port` set.
- A negotiated slow-keying delay with host PTT off is now a WARNING: Guardian
  can only slow keying it performs itself.
- **Slow keying is already symmetric** (0.6.39): request in HAVE_MSG,
  `max()` result back in ACK_HAVE, both sides adopt it — an AIOC station gets
  its delay whether it initiates or answers. Older peers ignore the bits and
  simply do not slow down.

**Cosmetics fixed with it:** the S/N estimate divided by a floor that had
collapsed to digital silence behind a closed squelch (`3.8e-5` → "78.7 dB");
now clamped (`SNR_MIN_FLOOR`), capped (`SNR_MAX_DB = 40`) and suppressed for
the first `SNR_FLOOR_SETTLE_SECONDS`. And `ABORT` is only sent with a live
link — VARA answers `WRONG` otherwise, which parked a permanent
"VARA rejected: ABORT" in diagnostics.

## Windows on ARM: PortAudio DLL choice (0.6.42)
The 0.6.41 diagnostics closed the missing-audio-devices report at OK2IPW in
one line: `OSError: cannot load library ...libportaudioarm64.dll: error 0x7e`.

That station is an ARM64 machine running the **x64** build under emulation.
Windows reports `PROCESSOR_ARCHITECTURE=AMD64` (the process) and
`PROCESSOR_ARCHITEW6432=ARM64` (the machine); `platform.machine()` prefers
the latter, and sounddevice picks its bundled DLL from it — so it asked for
an ARM64 library that the x64 wheel does not ship and an emulated x64 process
could not load anyway. Bundling the ARM64 DLL is **not** the fix.

`_import_sounddevice()` overrides `platform.machine` with
`process_architecture()` for the duration of the import and restores it
straight after; it engages only when process and machine differ, so native
hosts are untouched. Diagnostics now print both architectures.

## Audio device enumeration (0.6.41)
Field report: one PC listed no audio devices in Guardian while VARA listed
them fine; none of the Windows-side checks (privacy, disabled endpoints,
AV quarantine, replug) helped.

**Root cause:** `list_audio_devices()` called `sd.query_devices()` once for
the whole list. sounddevice decodes each device name and *re-raises*
`UnicodeDecodeError` for host APIs other than MME/DirectSound/ASIO —
**WDM-KS returns the local ANSI code page**, so one endpoint with a diacritic
(a Czech/Polish Windows) aborted the entire enumeration, and the bare
`except Exception: return [], []` turned that into "no audio hardware".
Reproduced locally by poisoning one endpoint.

- `_query_devices_one_by_one()` — per-device query, unreadable endpoints
  skipped and recorded.
- `scan_audio_devices()` returns `AudioDeviceScan(inputs, outputs, error,
  reinitialised)`; `list_audio_devices()` is now a thin wrapper. The settings
  status line prints `error` verbatim.
- Refresh calls `reinitialise_audio_backend()` (`sd._terminate()` +
  `sd._initialize()`) — PortAudio otherwise serves the snapshot taken at
  process start, so a codec plugged in later never appeared. Skipped while
  `audio_transport` is open (it would kill the stream) and the UI says so.
- Cross-API fallback is now per direction (a default API with playback but
  no capture used to hide every microphone).
- `audio_backend_report()` in diagnostics: PortAudio version, host APIs, the
  **unfiltered** device list, which entries are filtered as aliases, which
  were unreadable, and what Guardian ends up showing.

## Handshake polish + negotiated slow keying (0.6.39–0.6.40)
From the first multi-hop field reports:

- **Re-ACK on repeated HAVE_MSG** — a responder in ACKED answers a repeated
  announcement again instead of ignoring it; a lost ACK_HAVE used to strand
  both sides (initiator burns 3 announces, responder waits in ACKED).
- **Blind announce budget** — discovery with no offers falls back to the
  destination directly with `Message.blind=True`: 1× ROUTE_QUERY + 2×
  HAVE_MSG total. Vouched hops keep MAX_ANNOUNCE=3.
- **Slow-keying negotiation (VARA FM only)** — `config.vara_ptt_delay_ms`
  (0–700, default 0) rides in spare flags bits (bits 3–5, 100 ms steps;
  `encode/decode_ptt_delay` in frames.py; wire format unchanged, old builds
  echo unknown bits). HAVE_MSG carries the request, ACK_HAVE the negotiated
  max. Applied as a **PTT tail** (0.6.40, corrected from 0.6.39's pre-key
  hold-off after spectrum observation: the fast unkey was cutting the tail
  off the burst): after VARA's PTT OFF the transmitter stays keyed that long
  (`Operations._vara_ptt`, value captured from `_session_event` at
  STARTING_VARA/RECEIVING, cleared at terminal). Key-up stays immediate —
  keying late clips the VARA leader. Requires vara_host_ptt. Relay legs
  re-negotiate from their own setting.
- "Wrong message sent" report: selection/send path is id-keyed and clean;
  the observed behaviour is auto_deliver announcing another waiting message
  when its hop is heard (logged, by design, can be switched off).

## No-CAT radio via Hamlib dummy + serial PTT (0.6.38)
Root cause of "AIOC handheld with Hamlib Dummy never keys": the dummy model
is a simulator and **never opens the `-r` rig device**, so
`rigctld -m 1 -r COMx` looked configured while the port was never touched
(confirmed live on rigctld 4.7.2 — it even starts with a nonexistent port and
answers `T`/`t` from the simulator). The port must be the **PTT device**:
`-P RTS|DTR -p COMx`.

- `config.ptt_type` ("RIG"/"RTS"/"DTR") + *Hamlib PTT via* in settings;
  `RigctldProcess.command()` builds args (dummy gets no `-r`), full command
  line goes to the log.
- `ensure()` restarts **our own** rigctld when its args no longer match the
  config (a changed PTT line existed only on the command line before);
  foreign instances stay untouched.
- `Operations.reconfigure_radio()` + `radio_settings()` — the driver used to
  be built once in `__init__`, so any radio settings change needed an app
  restart. Shell calls it on Save/Apply.
- `make_driver` sets `reports_ptt=False` for dummy or serial PTT (both are
  echoes of our own state), so the PTT test says "cannot confirm" instead of
  fake-passing. Hamlib RPRT codes are translated in error messages.
- AIOC note: whether RTS or DTR keys PTT depends on the AIOC firmware config;
  try the other line if the first does not key.

## Serial port picker (0.6.37)
`cat_port` is a dropdown of `radio.usb_serial.list_serial_ports()` with a
refresh button, editable so an unplugged port can still be configured; only
`port_device()` (the bare COMx) is saved.

## Heard stations: S/N estimate and channel (0.6.35)
The `Last SNR` column was dead — nothing ever passed a measurement to the
registry. Neither modem reports one, so `AudioControlTransport.window_snr()`
estimates it from the RX audio (loudest quarter of the demod window against the
tracked idle floor) and it rides with the frame through `pump()` as
`last_frame_snr`. It is **(S+N)/N of the receive audio**, not a VARA/S-meter
figure — the column says *(est.)*, and shows `-` until the floor has settled.
A `Heard on` column records the CAT frequency at reception
(`Orchestrator.channel_frequency`), which after a QSY or a sweep says which
channel the contact was on.

## 8. Known issues / watch-list
- **`RX bad frame: bad magic` seen occasionally next to an alert** (0.6.34,
  reported from the air 2026-07-30, HF and FM; the alert itself arrived and
  displayed correctly every time). Logs pending from OK7PS. Cosmetic so far,
  but worth chasing because the message means the demodulator handed
  `_handle_payload` (`modem/audio.py`) bytes that its own
  `_is_valid_control_payload` validator should already have rejected. Two
  leads: (a) alerts are the longest frames we transmit (48 B vs 34 B for a
  HAVE_MSG) and there are simply more of them on air — 3 repeats plus relays —
  so a demod window is more likely to catch a truncated burst; (b) a relay and
  a source repeat can overlap despite the jitter, and a collided burst
  decodes to garbage. Check whether the offending payload is a prefix of a
  good frame before assuming a decoder bug.
- Taskbar/tray icon required an AppUserModelID + forced re-apply to override the
  pythonw default — verify it sticks after CustomTkinter theme changes.
- `rigctl -l` model ids change between Hamlib versions — the live "Browse all"
  picker is authoritative; curated ids were verified against Hamlib 4.7.1.
- AFSK bit-sync uses a phase search tuned for clean audio; real fading channels
  may need a PLL/transition-tracking sync (revisit during on-air bring-up).
- Station-hash id prefix is 12-bit, so a rare hash collision between two
  callsigns is possible; identity is really (source, msg_id) if ever needed.
```
