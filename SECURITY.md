# Security policy

## Supported version

Only the latest published Guardian release is supported with security fixes.

## Reporting a vulnerability

Do not publish credentials, private message data, or an exploitable security
issue in a public ticket. Use the repository's private GitHub Security Advisory
reporting channel when available. For non-sensitive defects, use
[GitHub Issues](https://github.com/bubakbubak500/ARDOS-Guardian/issues).

Include the Guardian version, Windows version, reproduction steps, expected
behavior, and the smallest diagnostic information required. Remove callsigns,
local paths, message content, and attachments unless they are essential.

## Download trust

Official Guardian binaries are published only on this repository's GitHub
Releases page. Releases include:

- a Windows installer and portable ZIP;
- `SHA256SUMS.txt`;
- `release-manifest.json` used by the in-app updater;
- GitHub build-provenance attestations.

Current packages are not Authenticode-signed, so Windows can report an unknown
publisher. Verify the SHA-256 and release origin before running a package.

VARA is proprietary third-party software. Guardian never bundles it. The
readiness assistant restricts downloads to the official Winlink distribution
host and accepts only the exact version, size, archive layout, and SHA-256
pinned in that Guardian release.

## Offline companion boundary

The phone companion is disabled until the operator starts it. It listens on the
configured local TCP port and is therefore reachable by devices on the same
network. API access requires a one-use, five-minute QR pairing secret; sessions
are HttpOnly/SameSite, idle-expiring and revocable. Stop the companion when it
is not needed and do not expose its port through router forwarding.

Immediate RF transmission from a phone is disabled on every server start and
must be armed explicitly at the desktop. Only Emergency-priority phone traffic
can request it. Pairing does not bypass Guardian's radio, channel or routing
safety checks.

The initial local transport is HTTP because isolated field networks do not have
a public certificate authority. WPA2/WPA3 or physical control of the local
network is therefore part of the trust boundary: do not run the companion on an
untrusted public hotspot. Message data remains local but is not additionally
encrypted between the paired phone and Guardian in version 2.3.4.
