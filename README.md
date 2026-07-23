# Guardian — ARDOS control & routing layer

Guardian is a Python/Windows control layer that sits **in front of VARA FM**.
It announces, negotiates and orchestrates message transfers over standard
amateur radios using short *control bursts* (metadata only), while the actual
message body travels over VARA FM.

It talks to radios through **Hamlib / `rigctld`** (hundreds of supported rigs,
Windows + Linux) with a simple serial **RTS/DTR PTT** fallback for dumb VOX
radios. The aim is no per-radio CAT reverse-engineering.

## What works today (Phase 1)

- Native PySide6 operational UI with Light, Dark and Follow system themes
- Task-oriented Home, Mail, Network and Log workspaces with native menus
- Station profile (JSON) — load/save, lives in `%APPDATA%\Guardian\config.json`
- **Control-burst protocol**: binary `ARD` frames with CRC-16, all frame types
  (`HAVE_MSG`, `ACK_HAVE`, `BUSY`, `ROUTE_QUERY`, `ROUTE_OFFER`, `START_VARA`,
  `RECEIVED`, `DELIVERED`, `CANCEL`), priorities and flags. Message composer
  builds and self-tests real bursts.
- **Configurable route table** (destination/group → preferred + backup hop)
- **Radio drivers**: Hamlib/rigctld TCP backend (freq/mode/PTT/S-meter) and a
  generic serial VOX PTT backend
- **VARA client**: TCP command/data connection with async notification reader
- **VARA FM + HF**: one-click mode switch, per-mode ports, auto-selected
  control-burst modem (AFSK 1200 for FM, MFSK-16 for HF)
- **One-click Hamlib install**: downloads + SHA256-verifies the official
  portable build; radios picked by name (curated list + live `rigctl -l`);
  optional rigctld auto-start
- **USB-serial adapter detection**: identifies the chipset (FTDI / CP210x /
  CH340 / PL2303) by VID:PID and links the official driver (no risky bundled
  kernel drivers)
- **Live handshake (Net tab)**: follow active sessions and watch the on-air channel
- **Custom application icon**: generated Guardian shield replaces the default
  Python icon
- **Winlink-like mail**: store-and-forward mailbox (Inbox / Outbox / Sent /
  Transit), text **+ attachments** in a compressed bundle, Compose/read UI with
  attachment Save/Open, route-history tracking, held-for-relay queue

## Roadmap

| Phase | Goal                          | Status |
|-------|-------------------------------|--------|
| 1     | UI, config, protocol, drivers | ✅ done |
| 2     | VARA session state-machine    | ✅ done |
| 3     | Control modem (AFSK + MFSK) + payload backends | ✅ done* |
| 4     | Smart routing / heard-stations | ✅ done |
| 5     | Multi-channel scanning / mesh | ✅ done* |

### Phases 4 & 5 — smart routing + mesh

Heard-stations registry (built from every received control frame),
**ROUTE_QUERY/ROUTE_OFFER** route discovery when no manual route exists,
learned-path memory, **multi-hop auto-relay** (TTL + loop avoidance), and a
tick-driven **channel scanner** (dwell + activity hold). The **Mesh** tab shows
heard stations and toggles auto-route / auto-relay; channel scanning lives there
too. (*) scanning needs a real radio to tune.

\*Phase 3 is software-complete: AFSK 1200 (FM) and MFSK-16 (HF, ~0 dB SNR) modems
with rate-1/2 K=7 convolutional FEC and audio device pickers. The production UI
uses the real audio control channel; deterministic loopback remains an internal
test transport. Remaining is on-air bring-up over a real radio + codec.

### Phase 3 — control modem + payload transport

* **`guardian/modem/`** — AFSK 1200 (Bell 202) modem in numpy: `modulate()`
  turns frame bytes into phase-continuous FSK audio, `demodulate()` recovers
  them (preamble + sync + length framing, phase-search bit sync, noise-tolerant
  sync). `AudioControlTransport` binds the modem to a sound device + radio PTT
  (half-duplex, dedup) and is a drop-in for the loopback bus.
* **`guardian/payload/`** — two interchangeable payload backends:
  * **`vara_p2p`** — Guardian opens VARA P2P and sends a framed payload envelope
    itself (immediate, self-contained, no internet).
  * **`winlink_manual`** — Guardian coordinates; the operator moves the message
    with their own Winlink session and confirms via a hand-off dialog.

  The production UI uses the real audio transport; the orchestrator remains
  transport-independent.

### Phase 2 — handshake state-machine

`guardian/session/` implements the full control choreography
(`HAVE_MSG → ACK_HAVE → START_VARA → payload → RECEIVED → DELIVERED`, plus
`BUSY`/`CANCEL`, ACK timeouts, retransmits and backup-hop fallback). It runs
over a pluggable control transport. In the production UI the network is idle
while the control channel is off and uses `AudioControlTransport` when the
operator starts it. `LoopbackBus` is retained for deterministic automated
tests only.

## Quick start (from source)

```powershell
# one-time, on any PC with Python 3.12:
.\setup.ps1
# run it:
.\run.ps1
```

Guardian starts in the operational Home workspace. Radio, VARA and the live
audio control channel are started explicitly from Home or the Tools menu.
Mail is composed into the Outbox and transmitted only after the live control
channel has been started by the operator.

## Build a standalone .exe

```powershell
.\build.ps1
# -> dist\Guardian\Guardian.exe
```

The build is fully local (PyInstaller), so it can be produced on each PC — no
network needed at runtime.

## Radio setup (Hamlib path)

Guardian connects to `rigctld`, not the COM port directly. You don't have to
install Hamlib by hand:

* **In-app:** Tools → **Station readiness**. Guardian downloads the
  official portable Hamlib build from github.com, verifies its SHA256, and
  unpacks it into `%APPDATA%\Guardian\hamlib`. No admin rights.
* **At setup:** `.\setup.ps1 -WithHamlib` does the same during install.

Then open **Settings → Station settings**, choose the Hamlib model and COM
port, and click **Connect radio** on Home. Guardian launches
`rigctld -m <model> -r <COM> -t <port>` for you and shuts it down on exit. If
`rigctld` is already running, it reuses it (multiple apps can share one radio).

VOX radios need nothing extra — the serial PTT driver uses pyserial, which is
bundled. Use **Check VOX / list COM ports** to see available ports.

## Project layout

```
guardian/
  config.py            station profile (JSON)
  protocol/frames.py   ARD control-burst encode/decode + CRC16
  routing/             configurable route table
  radio/               base + hamlib (rigctld) + generic_vox drivers
  vara/                VARA TCP command/data client
  services/            snapshots, structured events and safe workers
  qt/                  PySide6 operational workspaces and theme system
  operations.py        non-blocking radio, VARA and session controller
  app.py               entry point  (python -m guardian)
```
