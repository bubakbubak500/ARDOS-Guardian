# Guardian (ARDOS) — Project Status & Plan

_Last updated: 2026-06-01_

A resumable snapshot: what Guardian is, what's built, what's verified, the key
decisions and why, and what comes next. Read this first when picking the project
back up.

---

## 1. Vision

Guardian is a Python/Windows **control & routing layer that sits in front of
VARA FM/HF** for amateur-radio emergency messaging (project codename **ARDOS**).

Short **control bursts carry metadata only** (source, final destination, next
hop, message id, priority, TTL, flags + CRC). The actual message body travels
over VARA (or a manual Winlink session) once two stations have negotiated the
hop. Guardian is the fast signalling/dispatch layer; VARA is the data modem.

Radio control is via **Hamlib / rigctld** (hundreds of rigs) with a serial
RTS/DTR VOX fallback — no per-radio CAT reverse-engineering.

---

## 2. Status by phase

| Phase | Goal | Status |
|------:|------|--------|
| 1 | UI, station config, control-burst protocol, radio drivers | ✅ done |
| 2 | VARA handshake state-machine (orchestrator) | ✅ done |
| 3 | Control modem (AFSK + MFSK) + payload backends + audio channel | ✅ done* |
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
both payload backends, audio device pickers, and a loopback↔audio control-channel
selector (the audio transport starts/stops cleanly with real devices). Only
remaining (*needs hardware*):
- **On-air verification** — AFSK/MFSK over the real USB codec + PTT on a rig
  (loopback-cable test, then on-air). Audio I/O path is wired and starts, but
  not yet round-tripped through a radio.
- **Real `vara_p2p` transfer** — needs VARA running on two stations.
- MFSK bit-sync is tuned for clean audio; real fading may want a PLL/soft-decision.

---

## 3. What's verified vs. needs hardware

**Verified in software (no radio):**
- Control-frame encode/decode + CRC-16 round-trip.
- Full handshake over a loopback channel: direct delivery, relay hop, and
  no-responder retry→fail. (`HAVE_MSG→ACK_HAVE→START_VARA→RECEIVED→DELIVERED`,
  plus BUSY/CANCEL, ACK timeouts, backup-hop fallback.)
- AFSK 1200 modem loopback: clean + noisy, at 8 k / 44.1 k / 48 k, multi-burst.
- Both payload backends drive a session to DELIVERED (Winlink prompt auto-confirm
  in tests; VARA P2P envelope round-trip).
- Hamlib install + SHA256 verify; rigctld runs; live `rigctl -l` parse (312
  models); curated radio ids validated against installed Hamlib.
- UI builds/renders; tray + window icon; USB-serial detection.

**Needs real hardware/peers to verify:**
- AFSK over the actual USB audio codec + PTT keying on a rig.
- `vara_p2p` payload transfer between two live VARA stations.
- rigctld talking to a physical radio (CAT read/PTT).

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
    winlink_manual.py operator-confirmed Winlink hand-off
  install/
    hamlib_installer.py  download + SHA256-verify + unpack official Hamlib
  assets/
    icon.py           generated Guardian shield .ico / tray image (Pillow)
  ui/
    main_window.py    CustomTkinter UI (tabs below)
```

UI tabs: **Dashboard, Radio, VARA, Routing, Net, Messages, Log.**

The control flow is transport-agnostic: the orchestrator talks to a
`ControlTransport` (LoopbackBus for sim, AudioControlTransport for real RF) and
a `PayloadBackend` (vara_p2p or winlink_manual). Swapping either does not touch
the state-machine.

---

## 5. Key decisions & rationale

- **VARA is just a modem, not a messaging system.** One VARA instance = one
  command port = one master, so Guardian and Winlink Express cannot share one
  VARA. Therefore control bursts always travel **outside** the VARA data session
  on their own AFSK/MFSK modem (wake/announce/route-negotiation happen before any
  connection exists). The control modem is needed regardless of payload backend.
- **Payload backends: both `vara_p2p` and `winlink_manual`** (operator's choice,
  2026-06-01). P2P for the automated self-contained Guardian net; manual for full
  Winlink interop with zero scripting risk. (Pat HTTP-API automation is a
  possible future third backend.)
- **Modulation:** AFSK 1200 (Bell 202) for VARA **FM**; MFSK-16 + FEC planned for
  VARA **HF** (AFSK is too wide and lacks capture effect on SSB). Auto-selected
  by `vara_mode`.
- **Hamlib over rigctld (TCP), not direct CAT.** Massive rig support, Windows +
  Linux, multiple apps can share the radio.
- **Do NOT bundle USB-serial kernel drivers.** Admin-only, can brick working
  devices (esp. Prolific counterfeit lockouts), licensing grey area. Instead we
  detect the chipset and link the official driver.
- **Stack:** Python 3.12, CustomTkinter (light, compiles cleanly), PyInstaller,
  numpy, sounddevice, pyserial, pystray, Pillow.

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
```

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
2. **Live `vara_p2p`** — finish VARA connect/notification edge cases, buffer
   drain, disconnect handling; test between two stations.
3. **Channel scanning on a real radio** — verify tune/mode via rigctld; wire an
   activity threshold from the S-meter.

**Robustness / polish**
- MFSK soft-decision Viterbi + PLL bit-sync for real fading (current sync is
  tuned for clean audio).
- Heard-station signal quality from the modem (SNR) to rank relay candidates.
- Optional Pat HTTP-API payload backend (automated Winlink without Express).

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

## 8. Known issues / watch-list
- **msg_id uniqueness**: ids are currently local (store max+1). In a real
  multi-station mesh two stations could mint the same id and collide in the
  store. Make ids globally unique (source-qualified) before multi-station use.
- Taskbar/tray icon required an AppUserModelID + forced re-apply to override the
  pythonw default — verify it sticks after CustomTkinter theme changes.
- `rigctl -l` model ids change between Hamlib versions — the live "Browse all"
  picker is authoritative; curated ids were verified against Hamlib 4.7.1.
- AFSK bit-sync uses a phase search tuned for clean audio; real fading channels
  may need a PLL/transition-tracking sync (revisit during on-air bring-up).
```
