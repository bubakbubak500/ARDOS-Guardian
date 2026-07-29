# Guardian 0.6.31

Four changes to the HF control modem, from measuring OK7PS's failed-audio
capture against the frame OK2IPW's log says was transmitted.

**Read this first: the capture still does not decode.** The changes below are
each justified on their own, and three are validated against real audio, but
none of them closes the remaining gap. Treat HF as still unproven.

## A wider tone grid

On operating advice: an SSB channel gives ~2.4 kHz and the original grid used
469 Hz of it. The tones move from 600–1069 Hz at 31.25 Hz spacing to
**400–2275 Hz at 125 Hz**, clear of the ~300 Hz rolloff some radios have and
well inside the filter.

Two things follow. A `HAVE_MSG` drops from **6.30 s to 1.58 s**, and the
measured −8.5 Hz dial error goes from 27% of a tone spacing to **7%** — the
receiver becomes four times less sensitive to the frequency error that is
unavoidable between two radios.

## Automatic frequency correction

Two IC-705s each inside a ±0.5 ppm TCXO spec differ by ~21 Hz at 21 MHz. That
cannot be tuned away, so the receiver measures it by fitting the 16-tone grid
to the located frame and shifts its references. **Validated against the
capture: it measures −8.50 Hz, matching an independent analysis.**

## The preamble is found anywhere in the window

The timing search covered the first two symbols of a window many times longer
than a frame. On air the frame sat **3.5 seconds in** — it had been finding
frames by luck. Correlating just the two preamble tones locates it with a 0.97
match. **This is what made the offset measurable at all**; every earlier
attempt measured it in the noise ahead of the burst.

## Soft-decision decoding

The margin profile from the capture is the argument: after correcting the
offset, almost every symbol is decided **30:1** and two are near coin flips at
**1.13:1**. `argmax` handed those to the Viterbi as certainties. The
demodulator now passes per-bit confidence through, so reliable neighbours can
resolve the doubtful ones.

## What is still wrong

The capture's **body decodes perfectly — all 31 bytes — and only the two CRC
bytes are corrupt**, by a single bit, deterministically, on both stations. It
is not frame location, not the frequency offset, and not hard slicing; all
three are now fixed and it still fails. Something at the very end of a
transmission is being lost. The wider grid changes that end from a 32 ms symbol
to an 8 ms one, which may or may not matter.

More captures are needed, which is why this ships.
