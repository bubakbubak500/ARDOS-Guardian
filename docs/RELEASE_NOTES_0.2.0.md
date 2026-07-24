# Guardian 0.2.0

Guardian 0.2.0 establishes the public Windows release and update channel and
completes the first operator-facing modernization milestone.

## Station readiness

- Adds consent-driven direct downloads for VARA FM 4.4.0 and VARA HF 4.9.0 from
  the official Winlink distribution server.
- Pins the reviewed archive URL, version, byte size, SHA-256, and expected
  installer layout.
- Requires separate confirmation before downloading and before launching the
  proprietary vendor installer.
- Keeps manual selection and the official download page available.
- Continues to offer the verified portable Hamlib setup.

## Operator interface

- Adds complete English/Czech application text and operating events.
- Fixes inactive tabs in the dark theme and improves long Czech table headings.
- Adds a detailed searchable bilingual help system.
- Restores structured ICS-213, ICS-214, IARU, and local SITREP message forms.
- Keeps blocking downloads, hardware access, and update checks off the UI
  thread.

## Distribution and updates

- Bundles Python and runtime libraries in the Windows package.
- Publishes the installer, portable ZIP, SHA-256 checksums, release manifest,
  and GitHub build-provenance attestation.
- Uses the stable GitHub Releases `latest` manifest URL for in-app updates.
- Requires confirmation for both update download and installer launch.

## Important

The Windows package is currently not Authenticode-signed and can show an
Unknown publisher or SmartScreen warning. VARA remains proprietary third-party
software maintained by its author; Guardian does not redistribute it or accept
its licence on behalf of the operator.
