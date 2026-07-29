# Guardian 0.6.30

**The RX window could not hold an MFSK frame.** 0.6.29 fixed the tone geometry
and made the audio sound right, but detection then stopped entirely — not one
`RX bad frame` line, where 0.6.28 had produced a stream of them.

The audio transport kept a **fixed 4-second** rolling window,
`deque(maxlen=sample_rate * 4)`. That was ample for AFSK's 1.2-second frames.
Correcting the MFSK geometry made its frames **6.9 seconds**, so a whole frame
could never be inside the window: the demodulator was handed a permanent
fragment, found no frame, and returned quietly. Silence in the log was the
symptom of a buffer too small, not of a channel gone dead. The 4.00-second
diagnostic capture from the previous run was the window itself.

- The window is now sized from the modem: `airtime(largest frame) + one poll +
  1 s`. AFSK keeps exactly the 4 s it has always had; MFSK gets 8.8 s.
- The RX poll follows too. Polling every 250 ms for a frame that takes seven
  seconds only burns CPU — at the sizes involved it was 88% of a core, and
  demodulation could not keep pace with the poll. MFSK now polls at 0.87 s for
  17% of a core; **AFSK is untouched at 250 ms**, since FM is the one path
  proven on air.
- `MAX_CONTROL_FRAME_BYTES` is now declared once in the protocol and used by
  both the RX window and the session timeouts. The largest frame Guardian
  actually emits is 43 bytes; a test asserts every frame type stays under the
  bound, because a frame that outgrew it would stop being received at all.

Tests cover the window holding a whole frame on either modem, AFSK keeping its
historical window and poll, and the frame-size bound. The window test fails
against the old fixed buffer, verified by reverting.

**Both stations need 0.6.30** — and note that 0.6.29 could not receive at all,
so nothing was lost by it.
