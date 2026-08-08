# Guardian G2 2.0.4 — Guardian transmits the test burst itself

2.0.3 could save a test burst as a WAV file, and the on-air procedure then said
"play that WAV through the radio". The operator's reaction was, fairly: *how am I
supposed to play this into the radio?*

Answering that meant finding a media player, pointing it at the right output
device, and keying PTT by hand at the right moment. Which is the same fault as the
one 2.0.3 fixed, wearing different clothes: the console was gone, but a manual step
outside Guardian was still in the way. Guardian owns the transmit device and the
PTT line already, so it does it itself.

## Transmit into the radio

**Tools ▸ Modem test ▸ Transmit into the radio.** Guardian keys the radio, plays
the generated burst through the configured TX device at the profile's own sample
rate, and unkeys. Nothing to find, nothing to route, nothing to key by hand.

It asks first, because nothing else in that workspace touches a transmitter. The
confirmation names how long it is about to transmit for, the profile and MCS, the
frequency and device, and reminds you to identify — this is a waveform nobody else
will recognise. The default button is Cancel.

Saving the WAV is still there, for archiving a burst or feeding it to something
else. You no longer need it to run the test.

## One keying discipline, not two

The keying that does this is now `modem.audio.transmit_waveform`, and both the
payload transport and the test burst go through it. It previously existed twice —
once in the control transport and once in the OFDM pipe — and *"the transmitter is
always released"* is not a property to maintain in two copies: the copy that gets it
wrong leaves a station keyed on a channel other people are using.

The order in it is not arbitrary and each part was paid for on air:

- **Lead-in after keying**, because a waveform that starts before the carrier does
  is a burst the far end cannot synchronise to.
- **A tail of silence appended to the samples**, because stopping the output stream
  discards whatever the host API and a USB device still hold buffered — measured at
  ~130 ms off the end of every control burst before the guard existed.
- **The tail wait and the unkey in `finally`**, because PortAudio returning means it
  has finished filling the endpoint rather than that the radio has finished sending,
  and because if playback raised, dropping PTT still has to happen.

Tests cover the transmitter being released on a normal transmission and on a
playback exception, refusal when no TX device resolves and when a payload transfer
owns the codec, that a 96 kHz profile is transmitted at 96 kHz, and that the
control modem gets its sound card back afterwards.

## A test burst goes through the codec handoff

It is a transmission like any other, so it suspends the control channel and resumes
it afterwards rather than keying underneath it. If a payload transfer is in
progress it refuses and says so, instead of contending for the device.

## The air-test procedure

Step 2 of `docs/OFDM_AIR_TEST.md` — and of the Czech `OFDM_AIR_TEST.cs.md` — now
reads *set the radio, identify, press Transmit into the radio*. No media player, no
manual keying, no step that happens outside Guardian.

Nothing about the modem itself changed, and nothing has been on the air yet.
