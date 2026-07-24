# Guardian modernization result

Completed: 2026-07-24

## Delivered

- PySide6 is the only production UI. Home, Mail, Network and Log are active,
  task-oriented workspaces using the same domain objects as the original app.
- Radio, rigctld, VARA, the live audio control channel and ARDOS sessions are
  owned by a UI-independent controller. Blocking radio, network, dependency and
  update work runs through the bounded worker pool.
- The existing `vara_p2p` and `winlink_manual` payload implementations and the
  protocol/session state machines were not modified.
- Settings are grouped into Station, Radio control, VARA & payload, Network
  behavior and Appearance. The complete operator interface and activity
  messages can be switched between English and Czech.
- Mail composition provides interoperable plaintext forms for ICS-213,
  ICS-214 and IARU emergency traffic, plus a clearly identified local SITREP
  template. The built-in bilingual help contains ten searchable operator
  topics.
- First-run readiness detects Hamlib, VARA FM and VARA HF. Hamlib installation
  remains verified and consent-based; separately licensed VARA software opens
  its official source.
- Update checks use an HTTPS GitHub manifest. Download and launch each require
  explicit consent, and a downloaded installer is accepted only after SHA-256
  verification.
- CustomTkinter, pystray, the legacy UI monolith and visible bench/demo/control
  burst screens were removed.
- PyInstaller bundles Python and all runtime libraries. Inno Setup produces a
  per-user installer with Czech and English setup, Start menu integration,
  upgrade-in-place and preservation of `%APPDATA%\Guardian`.

## Verification

- Automated tests: **43 passed**
- Python bytecode compilation: passed
- Frozen `Guardian.exe` smoke: remained running and responsive
- Native Windows visual smoke: Light and Dark at 1366×768 passed
- Installer: Inno Setup 6.7.3 compiled successfully
- Installer size: 39,911,384 bytes
- Installer SHA-256:
  `e3fcb7024dc5ad1f820ae01ccc57c86754558960e065773057cf148545e0301e`
- Signature status: unsigned (expected for this development build)

The hardware-safe smoke test verifies that startup and background ticks do not
start the audio transport, key PTT or transmit queued mail. A message remains
queued until the operator explicitly starts the live control channel and sends
it. Physical on-air validation still depends on the operator's connected radio,
codec and licensed VARA installation; the automated suite covers both unchanged
payload backends and deterministic protocol behavior.

## Local artifact

`release/Guardian-0.1.0-setup-win-x64.exe`

The installer is intentionally excluded from Git. The release manifest is
versioned, while the GitHub workflow will rebuild and publish binaries only when
a version tag is deliberately pushed.
