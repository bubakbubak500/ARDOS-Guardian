# Guardian 0.3.0

Guardian 0.3.0 makes in-app updates visible and completes the hand-off from a
verified download to installation without requiring the update dialog to be
closed.

## In-app update experience

- Adds a download progress bar with percentage, downloaded megabytes, and total
  size when the server provides `Content-Length`.
- Uses an indeterminate progress indicator when the total download size is not
  available.
- Keeps the update dialog open and prevents accidental dismissal while the
  installer is downloading and being verified.
- Gives the modal update dialog its own worker-completion timer, so the
  installation prompt appears immediately after SHA-256 verification.
- Reports a duplicate download request instead of leaving the controls in a
  disabled state.
- Closes Guardian after the operator confirms installation and the verified
  installer has started successfully.
- Reports installer launch failure in the update dialog.

## Verification

- Adds automated coverage for byte-level download progress.
- Adds a Qt dialog regression test that verifies the progress display and
  immediate installation prompt while the update dialog remains open.
- Retains SHA-256 verification before the downloaded installer is published or
  offered for launch.

## Important

The Windows package is currently not Authenticode-signed and can show an
Unknown publisher or SmartScreen warning. The updater downloads only from the
trusted GitHub hosts defined by Guardian and launches an installer only after
its SHA-256 checksum matches the signed release workflow's manifest.
