# Guardian 0.6.36

**Test PTT.** A small button at the bottom of *Settings → Radio control* keys
the transmitter for two seconds so you can prove the interface really switches
the radio — before an emergency is the moment you find out it does not.

## What it does

- Confirms first: the dialog says the radio will transmit for two seconds on
  its current frequency and to check the antenna or dummy load. A stray click
  puts nothing on air.
- Keys through whichever backend is configured — Hamlib/rigctld or the
  RTS/DTR serial line — after waiting for any control burst already on the air
  to finish. If the radio is not open yet it is brought up (including starting
  rigctld) exactly as *Connect* does.
- **Reads back what the rig thinks it is doing while it is keyed.** "No
  exception" is not proof: a driver that accepts the command and transmits
  nothing is precisely the interesting failure. Three outcomes, all written to
  the log and shown under the button:
  - *passed* — keyed, and the radio reported TX;
  - *cannot confirm TX* — the command was accepted and released, but this
    backend has no telemetry (VOX/serial). Watch the radio itself;
  - *still reports TX after unkeying* — check the interface now.
- The unkey is in a `finally`, so a fault mid-test still stops the carrier. The
  keying time is capped at five seconds however the call is made.
- Refused, with the reason on screen, when no radio control is configured,
  while a payload transfer holds the radio, or while another radio command is
  running.
- Runs on a worker, so the dialog never freezes while the rig is keyed.

The button tests the radio **Guardian is actually using**. Change a port, model
or PTT line and it says so instead of keying: save or apply first, then test.

## Tests

Keying and read-back confirmed against a fake rig; a rig left keyed reported as
a fault rather than a pass; a backend with no telemetry saying so instead of
claiming a verified pass; the carrier stopped even when the test itself throws;
refusal without radio control and during a payload transfer; the five-second
cap; and on the dialog side, no keying without confirmation, no keying of
unapplied settings, and the button present-but-disabled with no live station.
Each was checked against a deliberately broken implementation to confirm it
fails there.
