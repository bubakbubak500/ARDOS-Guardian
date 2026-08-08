# Guardian G2 2.0.2 — record what the radio heard

2.0.1 shipped an OFDM modem that had never been on the air, and a field sheet
whose first step was "record the received audio with Audacity or a phone". This
release removes that step: Guardian records it itself, in exactly the format the
offline tools read.

That matters more than it sounds. A capture is the only artefact that lets the
*whole receiver* be run again over precisely what a radio produced — as many
times as you like, with changes. A log says what happened; a capture is what lets
it be fixed. Almost everything still to be tuned on this modem — the squelch, the
burst timing, the air profile, the equaliser — can be developed against a capture
without going back on the air, so getting good captures out of the first session
is worth more than anything else that session produces.

## Recording received audio

Start and stop it from the shell. Files land in

```
%APPDATA%\Guardian-G2\captures\capture-YYYYmmdd-HHMMSS.wav
```

as mono 16-bit PCM at the audio path's own rate — which is exactly what
`tools\ofdm_bench.py --read-wav` expects, so there is no export or resample step
in which a good capture can be turned into a misleading one.

- **The level is visible while it runs.** Elapsed time and the loudest sample so
  far. This is the difference between a session you can use and one you have to
  repeat, and it has to be visible *at the radio* rather than discovered
  afterwards.
- **The verdict is immediate.** On stopping, Guardian says whether the capture was
  silent, clipping, very quiet, or usable, and why — "silent (peak -74 dBFS) —
  check that the receive device is the one the radio feeds" is more use than a
  file that turns out to be empty a day later.
- **It can decode the capture on the spot**, reporting sync confidence, measured
  SNR, EVM and frequency offset, so a bad audio path is caught while the other
  station is still on the frequency.
- **No control channel required.** A station brought up purely to record what the
  other end transmits is the first and most useful step of an on-air test, and
  that is the case it is built for. It refuses to record while a payload transfer
  owns the sound card, and says so.

Nothing is normalised, trimmed, filtered or compressed on the way out. The silence
around a burst is data — it is the noise floor a squelch is measured against — and
the absolute level is how a clipping radio is told apart from a quiet one. A file
Guardian wrote is already in the state an analyst wants it in.

## Two details worth naming

**The file write does not happen in the audio callback.** Blocks are queued and
written by a thread of their own, because writing to disk inside a PortAudio
callback is how you get dropouts — and a capture with gaps in it is worse than no
capture, since the gaps look exactly like a channel that faded. If a block is ever
dropped the count is reported, so a gappy capture can never be mistaken for a
clean one.

**When the control channel is open, its existing receive stream is tapped** rather
than a second handle being opened on the same device. One device handle is less to
go wrong, and it guarantees the capture is exactly the audio the modem is working
from. When the control channel is closed, a stream of its own is opened instead.

## Also

- `docs/OFDM_AIR_TEST.md` — the on-air field sheet, rewritten around the built-in
  recorder. Steps in the order they should actually be done, the readings to write
  down at each one, a table of what each reading means and what to change when it
  is wrong, and the four settings a live exchange is likely to need adjusting.
- `DEFAULT_SAMPLE_RATE` is now a named constant in `guardian/modem/audio.py`. It
  was a bare default on one constructor until a second caller needed the same
  figure, and two copies of a sample rate is exactly the sort of thing that drifts.

Nothing about the modem itself changed in this release, and nothing has been on
the air yet.
