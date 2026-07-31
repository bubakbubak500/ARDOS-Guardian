# Guardian 0.6.41

**A PC where Guardian showed no audio devices while VARA showed them fine.**
None of the usual Windows checks helped, so the code went under the
microscope — and there is a real bug, one that fits a Czech (or any
non-English) Windows exactly.

## One unreadable device emptied the whole list

Guardian asked the audio backend for the device list **in a single call**.
The backend decodes each device name itself, and for host APIs other than
MME/DirectSound/ASIO a name that is not valid UTF-8 makes it raise. Windows
does produce such names in a non-English locale — **WDM-KS hands back the
local ANSI code page**, so one endpoint with a diacritic in it is enough.
That single exception aborted the enumeration of *every* device, and
Guardian, which quietly treated any failure as "no audio hardware", showed
an empty picker. VARA never touches this backend, so it listed everything
normally.

Reproduced here by poisoning one endpoint: before, both lists came back
empty; now the remaining 23 devices are listed and the bad one is named in
diagnostics.

Devices are now enumerated **one at a time**: an endpoint that cannot be read
is skipped and recorded, and the rest of the PC's audio hardware appears as
it should.

## The picker now says *why* it is empty

An empty list used to be indistinguishable from a crashed backend — the
status line said "check the interface and Windows privacy settings"
regardless, which is what sent this operator hunting through Windows for
what turned out to be a decoding failure. The reason now travels with the
result and is shown as-is:

- `the audio backend could not be loaded (ImportError: …)` — the PortAudio
  DLL is missing or blocked (antivirus quarantine is the usual cause);
- `the audio backend reported an error (…)` — PortAudio or a host API failed;
- `the audio backend started but reported no usable device (N endpoint(s)
  seen); 1 endpoint(s) unreadable: …` — the case above.

## Refresh devices now really refreshes

PortAudio enumerates once, when it initialises, and hands out that snapshot
forever. A codec plugged in after Guardian started could therefore never
appear, however often *Refresh devices* was pressed — while another program
started later saw it immediately. Refresh now re-initialises the backend so
it looks at the hardware again.

It deliberately does **not** do that while the control channel is running —
that would pull the device out from under the open stream — and says so
instead of pretending.

## Diagnostics report what the backend sees

*Help ▸ Diagnostics* (and its export) now carries an `audio_backend`
section: PortAudio version, every host API, and the **unfiltered** device
list with channel counts and sample rates, which endpoints Guardian filters
out as aliases, which ones were unreadable and why, plus what Guardian ends
up showing. That distinguishes "the backend sees nothing" from "our host-API
choice hides what it sees" — the distinction we could not make from here.

**For the affected station:** update, open Settings → Audio, press *Refresh
devices*. If it is still empty, export the diagnostics and send the JSON —
the answer will be in `audio_backend`.

## Also fixed

A default host API exposing playback but no capture used to hide **every**
microphone: the cross-API fallback only ran when both lists were empty. Each
direction now falls back on its own.

## Tests

One undecodable endpoint no longer blanking the picker (with the bad device
named in diagnostics), a missing backend reported rather than swallowed, an
empty backend stating how many endpoints it saw, per-direction host-API
fallback, refresh re-initialising the backend, refresh refusing to do so
under a live control channel, the empty-picker status quoting the real
reason, and the diagnostics section being present. All six deliberate
mutations of the new logic were caught.
