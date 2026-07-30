# Guardian 0.6.35

**An alert now goes looking for the net.** If the route table names working
frequencies other than the one you are on, Guardian tunes the radio to each of
them in turn, repeats the same alert there, and puts the radio back. And the
heard-stations table finally shows what it always promised: a signal figure,
and the channel the station was heard on.

## Alerts across every known frequency

An alert only ever reached the stations listening where you happened to be
tuned. The route table is the only record Guardian has of where the rest of the
net lives, so it is now used as an alert channel list.

- The alert goes out on the **current frequency first**, exactly as before:
  three copies, 10 s apart.
- The sweep then **waits for those copies to finish** — tuning away mid-flood
  would strand them on a channel the radio has already left — and moves on,
  bounded at 45 s in case the queue stalls.
- On each further frequency: QSY (with mode), 0.6 s to settle, **two copies
  3 s apart**, then the next channel.
- Afterwards the radio returns to **the frequency *and* mode it started on**. A
  rig handed back to an FM channel still set to USB would be deaf there.

Every channel is attempted independently. A rig that refuses a frequency, a CAT
timeout, a failure mid-burst — each costs that one channel, is written to the
log with the frequency that failed, and the sweep carries on. Reach is the
whole point; losing eight frequencies because the second one failed would
defeat it.

**Bounds.** At most **10 extra frequencies**, duplicates in the table collapsed
to one, and the frequency you are already on is skipped. That is a safety rail,
not an expectation: a real net has a handful of channels, and a route table
with a hundred must not key the radio for half an hour. The sweep also stops
early if the control channel is stopped or VARA takes the codec — and still
puts the radio back.

**The same alert, not new ones.** Every copy keeps the original message id, so
a station in earshot of two swept channels still shows it once and still
relays it once. Nothing about the wire format changes.

**In the dialog.** A checkbox says how many other frequencies are known and
what will happen. It is **ticked by default for emergency and priority codes,
unticked for routine ones** — an evacuation is what the sweep exists for;
spraying a QRT across every channel in the table is just noise. The
confirmation warns that the radio will be retuned before anything is
transmitted. With no other frequency in the route table, the box says so and is
disabled.

Alerts sweep on a worker thread, so the dialog closes immediately instead of
blocking the UI for ten QSYs.

## Heard stations: signal and channel

The **Last S/N** column existed but nothing ever filled it — no control frame
carried a measurement, so every station showed `-`. Neither modem reports a
channel figure, so it is now estimated from the receive audio itself: the
loudest quarter of the demodulated window is the burst, the slow-tracking idle
level is the noise. It is an **(S+N)/N estimate of the audio the rig delivers**,
not a VARA or S-meter reading, which is why the column is labelled *(est.)*.
Before the noise floor has settled there is no honest number, and the table
shows `-` rather than inventing one. The estimate is also appended to the `RX`
line in the log.

A new **Heard on** column records where the radio was tuned when the frame
arrived — taken from the CAT poll, not from the audio. On a simplex net that is
the station's frequency; after a QSY or a sweep it says which channel the
contact was actually on.

## Tests

New coverage: distinct frequencies collected from the route table in order;
the sweep visiting every channel, keeping one message id, restoring frequency
and mode, surviving a channel that will not tune, stopping when the control
channel goes away, staying inside its cap, skipping the current frequency,
waiting for the queued home repeats and not waiting forever, and running off
the UI thread; the S/N estimate against a known burst-to-floor ratio and its
two "say nothing" cases; the estimate travelling with the frame it belongs to;
a heard station filed with signal and channel, and a station heard with neither
keeping its previous values; the dialog's checkbox defaults per priority and
its retune warning; and the two new table columns. Each was checked against a
deliberately broken implementation to confirm it fails there.
