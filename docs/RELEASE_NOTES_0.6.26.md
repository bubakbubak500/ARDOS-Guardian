# Guardian 0.6.26

## The manual Winlink hand-off is gone

It was insurance while `vara_p2p` was unproven on air. Now that two-station
transfers work, it only offered a way to configure the station into a slower
manual workflow. `winlink_manual.py`, the hand-off dialog and the operator
prompt plumbing are removed.

The picker in Settings stays, listing VARA P2P alone, so the next transport
slots in rather than being rebuilt. A config still holding `winlink_manual` is
coerced to `vara_p2p` on load — no station is left without a transport.

## Editing a route no longer means retyping it

Selecting a row in Settings → Network loads it into the form below, so an
existing entry can be corrected and saved with the same button. Until now the
form stayed blank and an edit only landed if the callsign was retyped exactly;
a typo silently created a second route instead. Removing a route clears the
form it was loaded into.

## Import and export the network as a spreadsheet

**Soubor → Importovat síť z CSV… / Exportovat síť do CSV… / Uložit vzorový
soubor sítě…**

CSV rather than xlsx: it opens by double-click in Excel and LibreOffice Calc,
needs no extra dependency in the frozen build, and can be repaired in Notepad
on a field laptop. For emergency comms a text file beats a binary format.

Guardian writes UTF-8 **with BOM**, semicolon-separated — what a Czech Excel
expects. Without the BOM it mangles diacritics; with a comma it puts each row
in one cell.

```
destination;preferred;backup;frequency_mhz;mode
OK2IPW;;;145.2375;FM
OK1AAA;OK2IPW;ANY;145.3000;FM
OSTRAVA;OK1AAA;;;
```

Reading is deliberately more forgiving than writing: either separator, BOM or
not, any header case, a decimal comma or point, a bare Hz figure instead of
MHz, and a headerless file in the documented column order. Import merges into
the existing table (same destination wins) and reports what it did — bad rows
are listed, never dropped in silence.

The automated suite covers the export format, a full round trip, the tolerant
reader, problem reporting, the template being valid input, route selection
filling the form, and removal clearing it.
