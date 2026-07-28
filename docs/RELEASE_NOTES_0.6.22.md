# Guardian 0.6.22

Attachments can now be opened and saved, and a path-traversal flaw found while
building that is fixed at the transport layer.

**First successful two-station on-air transfer:** OK7PS ↔ OK2IPW, 145.2375 MHz
FM, IC-705 both ends, on 0.6.20. A 370-byte message went out in 14 seconds over
the primary `BUFFER`-drain path and the 362-byte reply came back the same way,
both confirmed end-to-end. VARA stepped 566 → 1188 → 2390 bps as it moved real
data. `STATUS.md` records the detail.

## Attachments

- An attachment bar appears under the reader whenever the open message has
  attachments: pick one and **Open** it, **Save as…** it, or **Save all…** into
  a folder. It stays hidden for messages without attachments.
- **Open** writes the attachment to a private temporary file and hands it to
  Windows. Executables and scripts (`.exe`, `.ps1`, `.bat`, `.vbs`, `.lnk`, …)
  first ask for confirmation naming the station the file came from — an
  attachment arrives over the radio from someone else, and opening it runs
  their code.
- Saving is always operator-directed; Guardian never writes an attachment
  anywhere on its own.

## Attachment names are peer input

Found while building the above. `to_bundle()` put the attachment name straight
into the bundle's zip path (`att/<name>`), so a name like
`..\..\Windows\System32\evil.txt`:

- escaped the archive for anything that extracted it with `extractall()` — the
  classic Zip Slip, and the bundle travels over the air to other stations; and
- failed to round-trip, because `from_bundle()` looked the original name up
  against the zip's normalised entry and **silently dropped the attachment**.

Names are now reduced to a bare filename on the way into the bundle and
re-checked on the way out, so a hand-crafted bundle cannot steer a name back
out of `att/`. Two attachments that reduce to the same name stay distinct
(`report.pdf`, `report-2.pdf`) instead of one overwriting the other. The UI
uses the same function, so what you see is what gets written.

The automated suite covers hostile names surviving the bundle without escaping
it, same-name attachments staying distinguishable, the attachment bar's
visibility and contents, and the executable-suffix list.
