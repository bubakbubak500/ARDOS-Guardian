# Guardian — resilient ARDOS radio messaging

Guardian is a Windows desktop application for composing, routing, relaying, and
tracking store-and-forward messages over amateur radio. Short ARDOS control
frames negotiate each transfer while VARA FM or VARA HF carries the message
bundle. The native English/Czech interface combines a mailbox, routes, heard
stations, station readiness, diagnostics, updates, and detailed operator help.

Guardian controls radios through **Hamlib / `rigctld`** with a serial RTS/DTR
PTT fallback. VARA remains separately licensed third-party software and is never
bundled or silently installed.

## Download

Download the current Windows installer from
[GitHub Releases](https://github.com/bubakbubak500/ARDOS-Guardian/releases/latest).
Python and all required Python libraries are included.

The application checks this same release channel for updates. It downloads an
installer only after confirmation, validates its SHA-256 against the release
manifest, and asks again before launching it.

> [!WARNING]
> Current development releases are not Authenticode-signed. Windows can display
> **Unknown publisher** or a Microsoft Defender SmartScreen warning. Download
> only from this repository's Releases page and compare against
> `SHA256SUMS.txt`. GitHub build-provenance attestations accompany each release.

## Product capabilities

- Native PySide6 operational UI with Light, Dark and Follow system themes
- Complete English/Czech interface and searchable operator help
- Task-oriented Home, Mail, Network and Log workspaces with native menus
- Station profile (JSON) — load/save, lives in `%APPDATA%\Guardian\config.json`
- **Control-burst protocol**: binary `ARD` frames with CRC-16, all frame types
  (`HAVE_MSG`, `ACK_HAVE`, `BUSY`, `ROUTE_QUERY`, `ROUTE_OFFER`,
  `MULTIHOP_RREQ`, `MULTIHOP_RREP`, `START_VARA`,
  `WORKING_OFFER`, `WORKING_ACK`, `RECEIVED`, `DELIVERED`, `CANCEL`), priorities and flags. Message composer
  builds and self-tests real bursts.
- **Configurable route table** (destination/group → preferred + backup hop)
- **Shared network topology builder**: import one link CSV for the whole net or
  add directed/costed links in a three-step wizard. Each PC derives its own
  next-hop table from its configured callsign; manual routes remain overrides.
- **Assisted multi-hop discovery**: bounded RREQ/RREP flooding, directed reverse
  replies, expiring live routes, trust lists and airtime limits. Two positions
  only — off, or assisted with operator approval before a learned route carries
  payload.
- **Radio drivers**: Hamlib/rigctld TCP backend (freq/mode/PTT/S-meter) and a
  generic serial VOX PTT backend
- **VARA client**: TCP command/data connection with async notification reader
- **VARA FM + HF**: one-click mode switch, per-mode ports, auto-selected
  control-burst modem (AFSK 1200 for FM, MFSK-16 for HF)
- **VARA spectrum monitor**: a separate P2P window with live RX FFT and
  waterfall, CAT-derived receive/transmit frequency, PTT indication, link
  state, FM/HF passband scaling, pause/clear controls, and remembered geometry;
  FFT work runs outside the UI thread so typing and navigation stay responsive
- **Explicit radio audio setup**: a dedicated Audio settings page lists and
  refreshes RX inputs and TX outputs with complete endpoint names, filters
  Windows mapper aliases, preserves a temporarily disconnected saved choice,
  and verifies the exact PortAudio endpoint opened by the active channel
- **Direct-route QSY**: with automatic QSY enabled, a direct route is tuned to
  its configured working frequency before the first control announcement and
  the previous frequency is restored after the session. A Hamlib Dummy / no-CAT
  radio instead shows an operator-maintained current frequency and requires an
  explicit tune-and-confirm step; Cancel leaves the message unsent.
- **Optional calling/working split**: disabled by default, so existing
  single-channel stations behave exactly as before. Enabling it under Network
  behavior reveals separate VARA working-channel fields. Two CAT-controlled
  peers prove that they configured the same channel, finish `START_VARA` on
  the calling channel, move only for the payload and return before control
  confirmations resume. A mismatch or an older peer never triggers QSY.
- **Operational station map**: cached ČÚZK topographic tiles, high-contrast
  own/heard markers, mail-activity links labelled with distance and azimuth,
  and one-click message composition addressed to a heard station. Own position
  can be detected once through Windows Location Service after explicit consent,
  picked on the map, or entered as a Maidenhead locator. Detection previews the
  reported source and accuracy before saving and never stores exact
  coordinates. Optional 4/6-character locator grids, geodesic 50/100/200 km
  rings, two-point distance/bearing measurement and a reachability legend turn
  it into an operational planning view. The visible ČÚZK area can be saved into
  a bounded 512 MB offline cache with a cancellable zoom plan, and the rendered
  situation can be exported to PNG. The map is an independent window and does
  not stay above the main application.
- **Consent-driven VARA setup**: Station readiness can download the exact
  reviewed VARA FM/HF archive from the official Winlink distribution server,
  enforce the version, size and SHA-256 pinned in this Guardian release, then
  separately ask before launching the vendor installer
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
  attachment Save/Open, route-history tracking, held-for-relay queue, persistent
  retry next hops and a directed end-to-end delivery receipt across relays
- **Structured traffic**: interoperable plaintext ICS-213, ICS-214 and IARU
  emergency-message templates, plus a clearly identified local SITREP template

See [Product description](PRODUCT.md) for intended use and boundaries and
[Security policy](SECURITY.md) before distributing a build.

## Roadmap

| Phase | Goal                          | Status |
|-------|-------------------------------|--------|
| 1     | UI, config, protocol, drivers | ✅ done |
| 2     | VARA session state-machine    | ✅ done |
| 3     | Control modem (AFSK + MFSK) + payload backends | ✅ done |
| 4     | Smart routing / heard-stations | ✅ done |
| 5     | Multi-channel scanning / mesh | ✅ done |

### Phases 4 & 5 — smart routing + mesh

Heard-stations registry (built from every received control frame),
**ROUTE_QUERY/ROUTE_OFFER** route discovery when no manual route exists,
learned-path memory and **multi-hop auto-relay** (TTL + loop avoidance) are
built and tested. Dynamic relay offers are ranked by direct reach, measured
S/N, freshness and a deterministic callsign tie-break. The Network workspace
now uses the former scanner page for a shared **network builder**: one topology
derives a different local route table at every station. The scanner engine
remains compatible with generated route channels but is no longer a primary
operator workflow. Guardian 0.6.57 adds bounded multi-hop RREQ/RREP and 0.6.58
`LINK_ADVERT` live-topology regeneration. Guardian 0.6.59 reduces discovery to
**off or assisted**, retiring the receive-only monitor position that could
neither answer a query nor produce a usable route, flattens the Network
workspace into five pages, and states on the page why an action cannot run.
`LINK_ADVERT` remains the one experiment, on the last page. Dynamic evidence
expires and always stays separate from the planned topology; see
[the multi-hop discovery design](docs/MULTIHOP_DISCOVERY.md).

Phase 3 is complete: AFSK 1200 (FM) and MFSK-16 (HF, ~0 dB SNR) modems
with rate-1/2 K=7 convolutional FEC and audio device pickers. The production UI
uses the real audio control channel; deterministic loopback remains an internal
test transport. Both control modems and VARA FM/HF payloads have been confirmed
on air.

The current prioritized work list is maintained in
[Development backlog](docs/DEVELOPMENT_BACKLOG.md).

### Phase 3 — control modem + payload transport

* **`guardian/modem/`** — AFSK 1200 (Bell 202) modem in numpy: `modulate()`
  turns frame bytes into phase-continuous FSK audio, `demodulate()` recovers
  them (preamble-aided clock recovery, normalized dual-tone detection, strict
  noise rejection and CRC framing). `AudioControlTransport` binds the modem to
  a sound device + radio PTT
  (half-duplex, dedup) and is a drop-in for the loopback bus.
* **`guardian/payload/`** — the payload backend behind a swappable interface:
  * **`vara_p2p`** — Guardian opens VARA P2P and sends a framed payload envelope
    itself (immediate, self-contained, no internet).

  An operator-driven `winlink_manual` hand-off existed until 0.6.26 as a
  fallback while `vara_p2p` was unproven on air; once two-station transfers
  worked it only offered a slower manual workflow.

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

## Quick start

1. Install Guardian from the latest GitHub Release.
2. Open **Operation → Station readiness**.
3. Set the station callsign and radio control in **Settings → Station settings**.
4. Locate or install Hamlib and the selected VARA FM/HF product.
5. Connect the radio and VARA, then explicitly start the control channel.
6. Compose mail into Outbox and send it only when the station is ready for RF.

For development from source:

```powershell
# one-time, on any PC with Python 3.11 or later:
.\setup.ps1
# run it:
.\run.ps1
```

Guardian starts in the operational Home workspace. Radio, VARA and the live
audio control channel are started explicitly from Home or the Tools menu.
With the VARA P2P payload workflow selected, the input-only spectrum window
opens alongside Guardian. It monitors only the radio RX input explicitly
selected in Station settings and never opens an output device or keys PTT.
Mail is composed into the Outbox and transmitted only after the live control
channel has been started by the operator.

## Build a standalone .exe

```powershell
.\build.ps1
# -> dist\Guardian\Guardian.exe
```

PyInstaller bundles the interpreter and libraries. Normal operation needs no
separate Python installation; network access is used only for actions such as
updates or explicitly requested external-tool downloads.

## Radio setup (Hamlib path)

Guardian connects to `rigctld`, not the COM port directly. You don't have to
install Hamlib by hand:

* **In-app:** Tools → **Station readiness**. Guardian downloads the
  official portable Hamlib build from github.com, verifies its SHA256, and
  unpacks it into `%APPDATA%\Guardian\hamlib`. No admin rights.
* **At setup:** `.\setup.ps1 -WithHamlib` does the same during install.

VARA FM/HF can be downloaded from **Operation → Station readiness**. Guardian
pins the reviewed official archive hash; the proprietary vendor installer and
its licence remain under the operator's control.

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
