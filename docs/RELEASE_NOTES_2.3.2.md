# Guardian G2 2.3.2 — Station Lab and fast selective repeat

Guardian 2.3.2 turns the waveform experiments into a measurable radio workflow.

- **Operation → Station test & AutoTune** calls one consenting Guardian and
  characterizes the real soundcard/radio path in both directions.
- Quick Tune sweeps modem drive. On Windows it can also sweep the selected radio
  output endpoint after an A/B probe proves that the mixer affects the remote
  level. It restores the original mixer state on success, cancel, timeout and
  failure; a crash-recovery journal protects interrupted runs.
- Unsafe clipping and unreliable points cannot become recommendations. Raw JSON
  and CSV measurements are saved, and applying the result always requires the
  local operator's final click.
- OFDM, SC-HS, SC-FTN and SEFDM can concatenate independently protected
  microbursts under one PTT, followed by one cumulative selective-repeat ACK.
  Sparse missing-block ACKs reduce control payload, and POLL recovers a lost
  final microburst or ACK.
- The compatibility default is one microburst per PTT. Configure a larger train
  only when both peers run Guardian 2.3.2.
- Transfer UI separates application speed from PHY payload rate, channel
  goodput, keyed duty cycle and PTT count.
- A deterministic end-to-end FM model now includes audio filtering, optional
  pre/de-emphasis, deviation/limiting, RF AWGN/multipath, discriminator, clock
  error, AGC and receive clipping. Its output is explicitly labelled as modeled.

In a deterministic 8 KiB SC-FTN MCS6/FEC 7/8 benchmark, four microbursts per
train increased modeled channel goodput from 6,166 to 7,840 bit/s (+27.1%). A
64 KiB run reduced DATA/ACK PTT pairs from 8+8 to 4+4 and increased 8,541 to
9,058 bit/s (+6.1%). These figures measure the software model, not an RF mask or
an on-air guarantee.

The existing control-session handshake and proven message semantics remain
unchanged. The CAL frames are a separate, directed, bounded protocol and never
create mail sessions. The optional identifier remains at 40 WPM.

See `docs/G2_STATION_LAB_2.3.2.md` for operation, safety, compatibility,
benchmark assumptions and remaining on-air acceptance work.
