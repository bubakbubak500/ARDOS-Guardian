# Guardian 0.6.38

**A no-CAT handheld behind an AIOC cable can now actually be keyed through
Hamlib.** Investigating a report — Hamlib Dummy configured for a UV-9R-class
handheld on an AIOC, "starts but never communicates" — found the root cause in
how Guardian launched rigctld, confirmed live against rigctld 4.7.2.

## The bug

Hamlib's Dummy model is a simulator: it answers every command and **never
opens the `-r` rig device at all**. Guardian passed the COM port as exactly
that, so:

```
rigctld -m 1 -r COM7        # what Guardian 0.6.37 started
```

starts happily (verified: it starts even with a COM port that does not
exist), answers `T 1` with `RPRT 0`, reports PTT as on — and never touches
the AIOC. Everything looks configured; nothing is ever keyed. Worse, the PTT
test read that echo back and reported a verified pass.

A no-CAT radio needs the port passed as the **PTT device**:

```
rigctld -m 1 -P RTS -p COM7   # what Guardian starts now
```

## What changed

- **New setting: *Hamlib PTT via*** in Radio control — *CAT command*
  (default, unchanged for real rigs), *RTS line*, or *DTR line* on the CAT
  port. RTS/DTR map to rigctld's `--ptt-type`/`--ptt-file`. For the AIOC
  setup: model *Hamlib Dummy — no-CAT radio / AIOC*, the cable's COM port,
  PTT via RTS (or DTR), then *Test PTT*.
- **The dummy model no longer gets `-r`** — it never used it, and the port
  now goes where it works.
- **The full rigctld command line is logged** at every start
  (`rigctld started: rigctld -m 1 -t 4532 -P RTS -p COM7`) — the one fact
  every PTT mystery needs first.
- **rigctld is restarted when its command line goes stale.** A changed PTT
  line, port or model exists only on the command line; `ensure()` used to
  reuse any responsive instance, silently keeping the old wiring. Guardian's
  own child is now restarted when its arguments no longer match; someone
  else's rigctld is left alone.
- **Radio settings now take effect on Save/Apply.** The driver was built once
  at startup, so a changed backend, port or PTT wiring previously waited for
  an application restart.
- **The PTT test stopped trusting echoes.** The dummy model repeats whatever
  was set, and a serial PTT line reads back Guardian's own wire — neither is
  the radio speaking, so both now report "cannot confirm TX — watch the radio
  itself" instead of a false pass. The test also logs which wiring it is
  exercising (`PTT test: keying hamlib for 2.0 s (rigctld 127.0.0.1:4532,
  PTT RTS on COM7)`).
- **rigctld error codes are translated.** `RPRT -6` now reads "IO error —
  serial port missing, busy, or opened by another program"; `-5` explains a
  timeout; `-9` a rig rejection — at exactly the moment someone is debugging
  PTT wiring.

## For the UV-9K/AIOC station

1. Update, open *Settings → Radio control*.
2. Radio model: **Hamlib Dummy — no-CAT radio / AIOC**. CAT / PTT serial
   port: the AIOC's COM port (now a dropdown). **Hamlib PTT via: RTS** —
   if the radio does not key, try **DTR**; which line the AIOC asserts
   depends on its firmware configuration.
3. Save, connect the radio, **Test PTT**. The log now shows the exact
   rigctld command line and, on failure, a translated reason.

## Tests

The command builder (dummy gets `-P`/`-p` and no `-r`; CAT rigs unchanged;
serial PTT on a real rig keeps both), the stale-arguments restart with
someone else's rigctld left alone, the driver rebuild on changed settings,
`reports_ptt` across model/PTT combinations, the RPRT translation, and the
new setting's save/unsaved-check paths. Verified live against rigctld 4.7.2:
the old command line fake-passes with a nonexistent port; the new one keys
the line or fails loudly. Each new test was checked against a deliberately
broken implementation to confirm it fails there.
