# Guardian G2 2.3.3 — capacity modem, superframe and measured equalization

Guardian G2 2.3.3 extends the independent soundcard modem while keeping the
proven ARDOS control frames unchanged. All new physical/protocol modes are
separately selectable; the compatible OFDM/frame-v2 path remains available.

## Capacity and waveform work

- SC-HS, SC-FTN, SC-FDE-FTN and SEFDM now have 1K2, 2K7, 5K, 10K and 20K
  profiles. The 20K rung occupies about 18.75 kHz at 48 ksample/s.
- Fractional symbol timing removes the old integer-samples-per-symbol limit.
- SC-FDE-FTN applies the trained FIR through bounded-memory overlap-save FFT
  convolution. FTN-aware colored-noise covariance, noise-dependent MMSE
  regularization and bounded robust training iterations are measured in every
  received burst.
- The single-carrier guard now covers the complete 81-tap equalizer radius.
  This fixes deterministic erasures at the end of the last codeword and makes
  reference EVM/GMI trustworthy.

## Modulation and shaping

- The experimental MCS table now contains 8-PSK; rectangular 32/128/512-QAM;
  16/32/64/128/256/512-APSK; 1024-QAM; and separate 16/64/256/1024-GQAM
  candidates with compressed outer geometry.
- MCS20 is genuine constant-composition PAS64: 54 enumerative amplitude bits
  plus 32 sign bits map reversibly to 16 symbols, or 5.375 coded bit/symbol.
  It remains an experiment to compare with ordinary 64-QAM; it is not silently
  substituted for another MCS.
- CRC-proven sections report reference EVM and generalized mutual information
  in bit/symbol. Adaptive MCS uses three clean reports to move up and backs off
  immediately after loss; the selected MCS is its ceiling.

## FEC, HARQ and receiver iterations

- Three opt-in systematic sparse accumulate LDPC rates are available: 1/2,
  3/4 and 9/10, with normalized min-sum decoding.
- Failed codewords retain deinterleaved LLRs. Convolutional retries combine on
  the common rate-1/2 mother code, including parity revealed by a stronger
  retry. LDPC retries combine shared systematic LLRs and same-graph parity.
- Up to three reliability-gated LDPC feedback iterations are tried. Only a
  candidate with the final per-block CRC can leave the modem.

## Protocol-v3 superframe

- Opt-in frame v3 carries up to 63 independently FEC/CRC-protected ARQ blocks
  under one preamble, channel training, robust header, PTT cycle and cumulative
  ACK. Frame v2 trains remain the compatibility fallback.
- A lost middle codeword produces a sparse NACK and only that ARQ block is sent
  again. A lost final ACK is still recovered by the robust POLL path.
- Train length can grow after three clean windows and shortens immediately on
  loss. The existing maximum continuous-PTT timer remains authoritative.

## Station Lab and evidence

- Calibration probe protocol v2 explicitly carries waveform bandwidth. Version
  1 probe tokens are still accepted as 2K7.
- Full Characterizer covers every waveform/width before spending airtime on
  denser constellations. Raw receive WAV captures are saved beside JSON/CSV
  reports for replay.
- `python -m guardian.waveforms.capacity_report` runs a deterministic
  end-to-end FM sweep and exports raw JSON/CSV rows. The checked-in 2.3.3 matrix
  is `docs/G2_CAPACITY_BENCH_2.3.3.{json,csv}`.

## Compatibility and interpretation

Frame v3, new MCS IDs and modern LDPC require Guardian G2 2.3.3 at both ends.
Leave Superframe v3 and Modern LDPC disabled for an older peer. Wider audio
profiles also require a radio path that actually passes that bandwidth.

The included benchmark is deterministic laboratory evidence, not a claim of
on-air superiority over VARA FM. A fair comparison still requires the same
payload, radios, channel, deviation, PTT timing and wall-clock interval.
