# Guardian G2 — current status

_Updated for release 2.3.4 on 2026-08-13._

This is the canonical snapshot of what is implemented and what has actually
been verified. Open work belongs in
[`docs/DEVELOPMENT_BACKLOG.md`](docs/DEVELOPMENT_BACKLOG.md); release-specific
history belongs in `docs/RELEASE_NOTES_*.md`.

## Current release

Guardian G2 2.3.4 is the private experimental line. It retains Guardian's
ARDOS control plane, routing, store-and-forward mail and VARA FM/HF transport,
and adds two G2 facilities:

- Guardian's own experimental soundcard payload modem;
- a local, account-free phone companion for a shared or Guardian-created Wi-Fi
  network.

G1 and G2 install side by side and use separate application-data directories.
VARA remains the compatible default payload transport.

## Verification boundary

### Confirmed on radio or hardware

- AFSK FM and MFSK HF control channels;
- direct VARA FM/HF mail and attachments;
- calling/working-channel QSY and return;
- CAT, serial/no-CAT PTT, scanner and multi-hop relay;
- the original G2 OFDM BENCH/MCS1 path between two IC-705 radios on
  2026-08-09. Messages moved and the measured audio path passed about 3 kHz.

### Implemented and covered in software

- adaptive FEC/burst sizing and selective-repeat ARQ;
- SC-HS, SC-FTN, SC-FDE-FTN and SEFDM waveform families;
- width profiles from 1K2 through 20K, a wider MCS ladder, modern LDPC,
  soft HARQ combining, adaptive MCS/train and protocol-v3 superframes;
- Station Test, Quick Tune, Full Characterizer, raw WAV evidence and
  deterministic FM-model reports;
- the 2.3.4 offline phone companion: one-use pairing, revocable sessions,
  inbox/status view, safe queueing, explicitly armed emergency RF action,
  field notes and check-in timer.

### Not yet claimed as verified

- adaptive frame-v2 operation, modern LDPC/HARQ or protocol-v3 superframes on
  two real radios;
- any 5K/10K/20K profile through an IC-705 FM audio path;
- repeated wall-clock superiority over VARA FM Narrow;
- RF occupied bandwidth or adjacent-channel mask compliance for the
  experimental high-capacity modes;
- Wi-Fi Direct and the companion user journey across representative Windows,
  iOS Safari and Android Chrome hardware.

Synthetic loopback and FM-channel figures are engineering evidence, not an
on-air performance guarantee. The acceptance target is repeated, identical
payload testing on the same radios, channel, deviation and PTT timing with zero
wrong-byte delivery.

## Active documents

- [`README.md`](README.md) — product overview and installation.
- [`G2.md`](G2.md) — G1/G2 separation and repository rules.
- [`docs/G2_MODEM.md`](docs/G2_MODEM.md) — current modem operation,
  compatibility and measurement procedure.
- [`docs/DEVELOPMENT_BACKLOG.md`](docs/DEVELOPMENT_BACKLOG.md) — the only active
  development backlog.
- [`docs/RELEASING.md`](docs/RELEASING.md) — release procedure.
- [`SECURITY.md`](SECURITY.md) — security and trust boundaries.

Older release notes, dated air-test reports and benchmark data are retained as
historical evidence. They do not override this status page.
