# Guardian (ARDOS) — Project Status & Plan

_Last updated: 2026-07-28_

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

## 8. Known issues / watch-list
- Taskbar/tray icon required an AppUserModelID + forced re-apply to override the
  pythonw default — verify it sticks after CustomTkinter theme changes.
- `rigctl -l` model ids change between Hamlib versions — the live "Browse all"
  picker is authoritative; curated ids were verified against Hamlib 4.7.1.
- AFSK bit-sync uses a phase search tuned for clean audio; real fading channels
  may need a PLL/transition-tracking sync (revisit during on-air bring-up).
- Station-hash id prefix is 12-bit, so a rare hash collision between two
  callsigns is possible; identity is really (source, msg_id) if ever needed.
```
