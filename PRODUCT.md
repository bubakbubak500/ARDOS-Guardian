# Guardian product description

Guardian is a Windows operator console for ARDOS store-and-forward messaging
over amateur radio. It coordinates short control frames, route discovery,
multi-hop relay, queued delivery, and VARA FM/HF payload transfers without
requiring a continuously available internet service.

## Intended users

- licensed amateur-radio operators experimenting with resilient messaging;
- emergency-communications groups evaluating structured digital traffic;
- stations that need explicit routing, relay visibility, and auditable local
  message state;
- developers testing the ARDOS protocol and its VARA payload hand-off.

## Core workflow

1. Configure the station identity, radio control, VARA mode, and local ports.
2. Use Station readiness to detect Hamlib, VARA FM, and VARA HF without
   transmitting.
3. Connect hardware and start the live control channel explicitly.
4. Compose a plain or structured message into Outbox.
5. Announce, route, transfer, acknowledge, and retain the resulting state in the
   local mailbox and activity log.

Guardian includes Python and its libraries in the Windows package. Hamlib can be
installed as a verified portable dependency. VARA is proprietary third-party
software: Guardian does not redistribute it, accept its licence, or install it
silently. The readiness assistant can download only a reviewed archive from the
official Winlink distribution server after consent, verify the exact size and
SHA-256 pinned in the Guardian release, and ask separately before launching the
vendor installer.

## Safety boundaries

- Starting Guardian does not start RF audio or key PTT.
- A composed message is local until the operator starts the control channel and
  sends it.
- A green local VARA connection does not prove an RF link to another station.
- Operators remain responsible for frequency, power, antenna, licence,
  third-party software terms, and applicable regulation.
- Guardian is not a certified public-safety dispatch or life-safety system.

## Data and network use

Station configuration, routes, messages, attachments, and diagnostic history
remain in the user's local profile. Network access is limited to explicit
external-tool downloads and the GitHub update channel. Diagnostic export is
manual and excludes message bodies and attachments, although it can contain
callsigns and local paths.
