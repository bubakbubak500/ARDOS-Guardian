# Guardian 0.6.32

> **Confirmed on air, 2026-07-29 evening:** with 0.6.32 on both stations the
> HF control channel works — frames decode and the handshake completes on
> 21 MHz USB. The guard silence below was the last of the five HF faults
> (tone geometry, RX window, preamble search, missing AFC, clipped tail).


**Every transmission was losing its last ~130 ms of audio.** Found by
demodulating three consecutive OK7PS captures of known frames, symbol by
symbol: symbols 0–130 of 147 decoded error-free in all three, and the last
~16 symbols were pure noise — margins near 1.0 with the wanted tone at a
fraction of the winner's energy. The burst envelope confirmed the frame ended
early on the air.

The same constant clip explains the previous day exactly: at the old
32 ms/symbol rate, 130 ms was ~4 symbols, which the FEC squeezed down to a
single wrong bit in the final byte — the mysterious "body perfect, CRC field
corrupt by one bit" signature. At the new 8 ms/symbol rate the identical clip
spans 16 symbols = the 4 corrupt trailing bytes seen in all three captures.
One cause fits both days.

The mechanism: `sd.play()` + `sd.wait()` can return before the host API / USB
device has drained its buffer, and stopping the stream discards what is left.
The discarded remainder was the end of the frame — the CRC.

**The fix:** every transmitted burst now carries 400 ms of silence after the
frame (`TX_GUARD_SECONDS`), so what the stream teardown discards is silence
instead of data. PTT timing is unchanged; the guard airs inside the existing
PTT window. Measured loss was ~130 ms, so the guard is a 3× margin.

Tests: truncating the last 130 ms of a bare frame loses it, the same
truncation of a guarded frame decodes — this test fails against the old code,
verified by reverting — and the TX path is checked to append exactly the
guard, as pure silence, after an unmodified frame.

**Both stations need 0.6.32.** The on-air format is unchanged from 0.6.31
(only trailing silence is added), but 0.6.31 receivers still lose their own
transmitted tails, so both ends must upgrade for the handshake to complete.
