# Guardian 0.6.34

**Net-wide alerts.** A station can now broadcast a short warning to everyone
on the current frequency. Receiving stations show it and pass it on, so it
reaches beyond the originator's own footprint. First build of the feature —
to be tried on air and iterated.

## What travels

One byte of alert code plus an optional ASCII note, inside the **existing**
control frame. The wire format is untouched on purpose: no station needs
reconfiguring, and a build that predates this release simply ignores a frame
type it does not know.

```
14  header + CRC        1  alert code
 6  source callsign    25  note (21 with a 7-character callsign)
 1  empty next_hop     --
                       48  MAX_CONTROL_FRAME_BYTES, unchanged
```

The note is truncated to what fits rather than refused — an abbreviated alert
still beats one that was never sent. A full burst is about 1.7 s on MFSK-16
and 1.2 s on AFSK; whichever modem the band is running carries it, with no
extra setting.

Sending a code instead of a sentence is what lets each station read the alert
**in its own language**: the byte expands to a translated sentence at display
time. The seed table (codes are permanent once used on air — new ones get
added, existing ones are never renumbered):

| Code | Meaning | Priority |
| --- | --- | --- |
| 0x01 | MAYDAY — life in danger | EMERGENCY |
| 0x02 | Medical emergency | EMERGENCY |
| 0x03 | Evacuation under way | EMERGENCY |
| 0x10 | Station going off air (QRT) | ROUTINE |
| 0x11 | Changing frequency (QSY) | PRIORITY |
| 0x12 | Station ready (QRV) | ROUTINE |
| 0x20 | Net test — exercise only | ROUTINE |
| 0x30 | Mains power outage | PRIORITY |
| 0x31 | Running on battery | PRIORITY |

## How it floods

- The originator transmits the same frame **3 times, 10 s apart** — nothing
  acknowledges an alert, so repetition is the only reliability there is.
- A station shows an alert **once**, keyed by message id, however many copies
  and relays carry it.
- It then relays with **TTL−1** (starting at 3), keeping the *originator* in
  the source field and putting its own callsign in `next_hop` so the path is
  visible in diagnostics.
- The relay waits **1–5 s, jittered per callsign**. Every station in earshot
  heard the same alert at the same instant; without the spread they would all
  key together.
- Seen ids are remembered for an hour, so a late relay is recognised but a
  repeat exercise later in the day is not swallowed.

## In the UI

- A **red-bordered banner** appears under the station context bar, above the
  mailbox counters: the sentence, the note, the time and who sent it. Amber
  for routine codes, red for the rest. *Dismiss* hides it — a newer alert
  still comes through.
- **Alert** next to Compose in Mail opens the send dialog: code picker, note
  field with a hint for that code and a live character counter, then a
  confirmation before anything is transmitted.
- Alerts are refused with an explanation while the **control channel is
  down**, and every one is written to the log.

Tests cover the byte budget against the real encoder, truncation and
ASCII-forcing, a three-station flood converging with each station displaying
once, TTL decrement and the originator surviving a relay, the last hop not
relaying, spaced repeats, per-station jitter, a failing UI callback not
stopping the flood, the banner's placement and dismissal behaviour, and the
send dialog's confirmation and no-control-channel paths. Each was checked
against a deliberately broken implementation to confirm it fails there.
