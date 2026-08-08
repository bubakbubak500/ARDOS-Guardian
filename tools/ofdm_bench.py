"""Print the OFDM bench measurements on a console.

Everything here is also in the application, under **Tools -> Modem test**, and
that is the way to reach it: no Python, no PowerShell, no shell at all. This
script exists for a developer who wants the numbers in a terminal or in a diff,
and it is a thin front end -- every measurement comes from
`guardian.ofdm.bench`, so the figures printed here and the figures on screen are
produced by the same code and cannot drift apart.

    python tools/ofdm_bench.py                      # a whole message, with ARQ
    python tools/ofdm_bench.py --single             # one burst, in detail
    python tools/ofdm_bench.py --sweep              # decode rate against SNR
    python tools/ofdm_bench.py --profiles           # the profile ladder
    python tools/ofdm_bench.py --transmit tx.wav    # a clean file to put on air
    python tools/ofdm_bench.py --read-wav rx.wav    # decode a capture
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Runnable straight from a checkout, without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from guardian.ofdm import bench  # noqa: E402
from guardian.ofdm.config import (DEFAULT_PROFILE_NAME, MCS_TABLE,  # noqa: E402
                                  PROFILE_LADDER, mcs, profile)

RULE = "=" * 70
THIN = "-" * 70


def _value(value, unit: str = "", digits: int = 1) -> str:
    """Render a measurement, or say it is unavailable rather than printing a 0."""
    if value is None:
        return "unavailable"
    return f"{value:.{digits}f}{unit}"


def _percent(value, digits: int = 2) -> str:
    return _value(None if value is None else value * 100.0, " %", digits)


def show_facts(facts: bench.WaveformFacts) -> None:
    print(f"profile:              {facts.profile}")
    print(f"sample rate:          {facts.sample_rate} Hz")
    print(f"FFT size:             {facts.fft_size}   CP {facts.cp_length} samples"
          f" ({facts.cp_ms:.2f} ms)")
    print(f"subcarrier spacing:   {facts.subcarrier_spacing:.3f} Hz")
    print(f"active carriers:      {facts.carriers}"
          f"  ({facts.data_carriers} data + {facts.pilots} pilot)")
    print(f"occupied bandwidth:   {facts.occupied:.0f} Hz  ({facts.band} baseband)")
    print("                      [profile-derived; sample rate is NOT bandwidth,"
          " and no RF claim is made]")
    print(f"symbol duration:      {facts.symbol_ms:.2f} ms")
    print(f"MCS:                  {facts.mcs_label}")
    print(f"bits per symbol:      {facts.coded_bits_per_symbol} coded /"
          f" {facts.information_bits_per_symbol} information")
    print(f"PHY rate:             {facts.phy_rate:.0f} bit/s"
          "  [payload carriers only, before preamble/header/ACK overhead]")
    print(f"block size:           {facts.block_size} bytes")
    print(f"header:               {facts.header_symbols} symbols at {mcs(0).label}")
    print(f"full block airtime:   {facts.full_block_seconds:.3f} s")


def show_profiles() -> int:
    print("Guardian OFDM VHF profile ladder")
    print(RULE)
    print("None of these is a proven air profile. They exist to be tried on a real")
    print("radio, in this order, until one stops working -- what decides the answer")
    print("is the audio bandwidth the receive path passes, not the channel spacing.")
    print()
    header = (f"{'profile':12} {'occupied':>10} {'band':>18} {'sampling':>9}"
              f" {'carriers':>9} {'QPSK rate':>11} {'block':>8}")
    print(header)
    for name in PROFILE_LADDER:
        facts = bench.describe(profile(name), 1)
        print(f"{name:12} {facts.occupied / 1000:8.2f} kHz {facts.band:>18}"
              f" {facts.sample_rate / 1000:7.0f} k {facts.data_carriers:9d}"
              f" {facts.phy_rate:9.0f} b/s {facts.full_block_seconds:7.2f} s")
    print()
    print("Every rung shares one subcarrier spacing (46.875 Hz) and one guard")
    print("(2.67 ms), so frequency-offset and multipath tolerance do not change as")
    print("you move up -- only the bandwidth does.")
    print()
    print("Widening is not free: the same transmit level over twice the carriers is")
    print("3 dB less per carrier. BENCH to WIDE_20K is about 9 dB for about eight")
    print("times the throughput.")
    return 0


def show_burst(result: bench.BurstResult) -> int:
    metrics = result.metrics
    print("Guardian OFDM VHF bench")
    print(RULE)
    show_facts(result.facts)
    print(THIN)
    print(f"channel:              {result.channel}")
    print("                      [SNR is in-band; the wideband SNR of this audio"
          f" is {result.wideband_offset_db:+.1f} dB]")
    print(f"TX payload:           {result.payload_bytes} bytes")
    print(f"waveform:             {result.samples} samples"
          f"  ({result.seconds:.3f} s)")
    print(f"crest factor:         {result.tx_crest_db:.1f} dB"
          f"  (TX RMS {result.tx_rms:.3f} of full scale)")
    print(THIN)
    print(f"simulated SNR:        {result.applied_snr_db:.1f} dB in-band")
    print(f"measured SNR:         {_value(metrics.snr_db, ' dB')}")
    print(f"EVM:                  {_percent(metrics.evm_rms)}"
          f"  ({metrics.evm_source})")
    print(f"sync confidence:      {_value(metrics.sync_confidence, '', 3)}")
    print(f"CFO estimate:         {_value(metrics.cfo_hz, ' Hz', 2)}")
    print(f"pilot phase slope:    {_value(metrics.phase_slope, ' rad/carrier', 5)}")
    print(f"RX audio RMS:         {_value(metrics.audio_rms, '', 4)} of full scale"
          f"  (crest {_value(metrics.crest_factor_db, ' dB')})")
    print(f"channel response:     {_value(result.channel_spread_db, ' dB')}"
          " spread across the band")
    print(f"uncoded BER (theory): {result.uncoded_ber:.2e}"
          "  [the code must beat this by orders of magnitude]")
    print("retries:              0  [single burst; omit --single to exercise ARQ]")
    print(f"result:               {'PASS' if result.passed else 'FAIL'}"
          f"{'' if metrics.error is None else '  -- ' + metrics.error}")
    print(f"RX payload identical: {'YES' if result.identical else 'NO'}")
    if result.wav_path is not None:
        print(f"\nwrote {result.wav_path}")
    return 0 if result.passed else 1


def show_transfer(result: bench.TransferResult) -> int:
    print("Guardian OFDM VHF bench -- ARQ over a simulated duplex link")
    print(RULE)
    show_facts(result.facts)
    print(f"channel:              {result.channel}")
    print(THIN)
    for line in result.log:
        print(f"  {line}")
    print(THIN)
    print(f"TX payload:           {result.payload_bytes} bytes"
          f" in {result.blocks} blocks")
    print(f"simulated SNR:        {result.applied_snr_db:.1f} dB in-band")
    print(f"measured SNR:         {_value(result.measured_snr_db, ' dB')}"
          "  (mean over bursts)")
    print(f"EVM:                  {_percent(result.measured_evm)}")
    print(f"retries:              {result.retries}")
    print(f"blocks acked:         {result.blocks_acked}/{result.blocks}")
    print(f"packet error rate:    {_percent(result.packet_error_rate, 1)}")
    print(f"channel occupancy:    {result.channel_seconds:.1f} s of airtime"
          " (both directions, plus PTT turnaround)")
    print(f"measured throughput:  {_value(result.throughput_bps, ' bit/s', 0)}"
          "  [end to end, ACKs and retries included]")
    print(f"result:               {'PASS' if result.passed else 'FAIL'}")
    print(f"RX payload identical: {'YES' if result.identical else 'NO'}")
    return 0 if result.passed else 1


def show_sweep(result: bench.SweepResult) -> int:
    print("Guardian OFDM VHF bench -- SNR sweep")
    print(RULE)
    show_facts(result.facts)
    print(THIN)
    print(f"{result.runs} seeded runs per point, one"
          f" {result.facts.block_size}-byte block each.")
    print(f"{'in-band SNR':>12} {'decoded':>9} {'measured':>12} {'EVM':>9}"
          f" {'wrong bytes':>12} {'uncoded BER':>12}")
    for point in result.points:
        print(f"{point.applied_snr_db:>9.0f} dB {point.decoded:>4}/{point.runs:<4}"
              f" {_value(point.measured_snr_db, ' dB'):>12}"
              f" {_percent(point.measured_evm, 0):>9}"
              f" {point.wrong_bytes:>12} {point.uncoded_ber:>12.1e}")
    print(THIN)
    print(f"reliable down to:     {_value(result.lowest_reliable_snr_db, ' dB', 0)}")
    print(f"nothing decodes at:   {_value(result.cliff_snr_db, ' dB', 0)}")
    wrong = result.wrong_byte_deliveries
    verdict = ("PASS -- the CRC never let a bad block through" if not wrong
               else "FAIL -- bytes were delivered that were not sent")
    print(f"wrong-byte deliveries: {wrong}  ({verdict})")
    return 0 if not wrong else 1


def show_capture(result: bench.CaptureResult) -> int:
    print("Guardian OFDM VHF bench -- decoding a WAV capture")
    print(RULE)
    print(f"file:                 {result.path}")
    print(f"samples:              {result.samples}"
          f"  ({result.seconds:.2f} s at {result.sample_rate} Hz)")
    print(f"audio RMS:            {result.audio_rms:.4f} of full scale"
          f"  (peak {result.audio_peak:.4f})")
    print(THIN)
    if result.header is None:
        print(f"result:               FAIL -- {result.error}")
        return 1
    print(f"header:               {result.header.summary()}")
    print(f"measured SNR:         {_value(result.metrics.snr_db, ' dB')}")
    print(f"EVM:                  {_percent(result.metrics.evm_rms)}")
    print(f"CFO estimate:         {_value(result.metrics.cfo_hz, ' Hz', 2)}")
    print(f"sync confidence:      {_value(result.metrics.sync_confidence, '', 3)}")
    payload = ("--" if result.payload_bytes is None
               else f"{result.payload_bytes} bytes")
    print(f"payload:              {payload}")
    print(f"result:               {'PASS' if result.passed else 'FAIL'}"
          f"{'' if not result.error else '  -- ' + result.error}")
    return 0 if result.passed else 1


def show_transmit(prof, mcs_index: int, path: Path, payload_bytes: int,
                  repeats: int, seed: int) -> int:
    _, written = bench.make_test_burst(prof, mcs_index, payload_bytes=payload_bytes,
                                       seed=seed, repeats=repeats, wav_path=path)
    facts = bench.describe(prof, mcs_index)
    print(f"wrote {written}")
    print(f"  {repeats} clean burst(s) of {min(payload_bytes, prof.block_size)}"
          f" bytes at {facts.mcs_label}, {facts.occupied:.0f} Hz occupied,"
          f" {facts.sample_rate} Hz mono")
    print("  No channel is applied: play this through the radio, record the far"
          " end, and decode the recording with --read-wav.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bench the Guardian OFDM VHF modem without a radio. The same "
                    "measurements are in the application under Tools -> Modem test.")
    parser.add_argument("--profile", default=DEFAULT_PROFILE_NAME,
                        choices=list(PROFILE_LADDER),
                        help="waveform profile (default: %(default)s)")
    parser.add_argument("--mcs", type=int, default=1,
                        choices=[entry.index for entry in MCS_TABLE],
                        help="MCS index for the data section (default: %(default)s)")
    parser.add_argument("--bytes", type=int, default=4096, help="payload size")
    parser.add_argument("--snr", type=float, default=15.0,
                        help="in-band SNR in dB; a large value asks for an ideal"
                             " channel (default: %(default)s)")
    parser.add_argument("--seed", type=int, default=0xA5, help="channel seed")
    parser.add_argument("--runs", type=int, default=10, help="runs per sweep point")
    parser.add_argument("--single", action="store_true",
                        help="bench one burst in detail instead of a whole message")
    parser.add_argument("--sweep", action="store_true",
                        help="report decode rate against SNR")
    parser.add_argument("--profiles", action="store_true",
                        help="list the profile ladder and stop")
    parser.add_argument("--transmit", type=Path, metavar="FILE",
                        help="write a CLEAN burst (no channel applied) to play"
                             " through a radio")
    parser.add_argument("--repeats", type=int, default=3,
                        help="bursts in a --transmit file (default: %(default)s)")
    parser.add_argument("--write-wav", type=Path, metavar="FILE",
                        help="also write the channel output of --single as a WAV")
    parser.add_argument("--read-wav", type=Path, metavar="FILE",
                        help="decode a WAV capture instead of simulating")
    args = parser.parse_args(argv)

    if args.profiles:
        return show_profiles()

    prof = profile(args.profile)
    if args.read_wav is not None:
        return show_capture(bench.decode_capture(prof, args.read_wav))
    if args.transmit is not None:
        return show_transmit(prof, args.mcs, args.transmit, args.bytes,
                             args.repeats, args.seed)
    if args.sweep:
        return show_sweep(bench.run_sweep(prof, args.mcs, runs=args.runs,
                                          seed=args.seed))
    if args.single or args.write_wav is not None:
        return show_burst(bench.run_burst(prof, args.mcs, payload_bytes=args.bytes,
                                          snr_db=args.snr, seed=args.seed,
                                          wav_path=args.write_wav))
    return show_transfer(bench.run_transfer(prof, args.mcs,
                                            payload_bytes=args.bytes,
                                            snr_db=args.snr, seed=args.seed))


if __name__ == "__main__":
    raise SystemExit(main())
