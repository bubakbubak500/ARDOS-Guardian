"""Bench the Guardian OFDM VHF modem on the PC, with no radio involved.

    python tools/ofdm_bench.py                    # a whole message, with ARQ
    python tools/ofdm_bench.py --single           # one burst, in detail
    python tools/ofdm_bench.py --sweep            # decode rate against SNR
    python tools/ofdm_bench.py --single --write-wav b.wav   # capture as audio
    python tools/ofdm_bench.py --read-wav b.wav   # decode audio back to bytes

The WAV modes are what make this useful once there are radios: record the audio a
receiver actually hears, hand the file to `--read-wav`, and the measurements below
describe the real channel instead of a simulated one.

Every burst here goes through the deterministic channel simulator -- delay, gain,
frequency offset, an echo and a clock error -- not a perfect array-to-array
handover. `--snr 999` is the way to ask for the ideal case.
"""

from __future__ import annotations

import argparse
import math
import sys
import threading
import wave
from pathlib import Path

import numpy as np

# Runnable straight from a checkout, without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from guardian.ofdm import (BENCH, OfdmLink, PhyHeader, build_burst,  # noqa: E402
                           burst_duration, decode_burst, mcs, profile,
                           profile_names, simulated_pair, split_blocks)
from guardian.ofdm.channel import Channel, ChannelSpec, realistic  # noqa: E402
from guardian.ofdm.config import OfdmProfile  # noqa: E402
from guardian.ofdm.constellation import bits_per_symbol  # noqa: E402
from guardian.ofdm.framing import OfdmFrameType, header_symbols  # noqa: E402

WAV_SCALE = 32767


def _q(x: float) -> float:
    """Gaussian tail probability, from the stdlib error function."""
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def uncoded_ber(modulation: str, snr_db: float) -> float:
    """Textbook uncoded bit error rate for a square constellation.

    Printed beside the measured figures as a sanity check on the whole chain's
    normalisation: if the modem's own numbers do not sit near these, something is
    scaled wrong long before any RF is involved. The coded result must of course
    be far better than this.
    """
    bits = bits_per_symbol(modulation)
    order = 1 << bits
    es_over_n0 = 10.0 ** (snr_db / 10.0)
    eb_over_n0 = es_over_n0 / bits
    if bits <= 2:  # BPSK and QPSK are the same curve per bit
        return _q(math.sqrt(2.0 * eb_over_n0))
    root = math.sqrt(order)
    return ((4.0 / bits) * (1.0 - 1.0 / root)
            * _q(math.sqrt(3.0 * bits / (order - 1.0) * eb_over_n0)))


def write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    """Write mono 16-bit PCM with the stdlib `wave` module."""
    clipped = np.clip(np.asarray(samples, dtype=np.float64), -1.0, 1.0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes((clipped * WAV_SCALE).astype("<i2").tobytes())


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Read mono 16-bit PCM back to floats in -1..1. Takes channel 0 of a stereo file."""
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    if width != 2:
        raise SystemExit(f"{path}: need 16-bit PCM, got {width * 8}-bit")
    data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / WAV_SCALE
    if channels > 1:
        data = data[::channels]
    return data, rate


def describe_profile(prof: OfdmProfile, index: int) -> list[str]:
    """The waveform parameters, spelled out."""
    scheme = mcs(index)
    low, high = prof.occupied_band
    coded = prof.coded_bits_per_symbol(scheme.bits_per_symbol)
    rate = scheme.code_rate
    net = coded * rate.numerator / rate.denominator / prof.symbol_duration
    return [
        f"profile:              {prof.name}   [simulation/bench profile,"
        " not a VHF air profile]",
        f"sample rate:          {prof.sample_rate} Hz",
        f"FFT size:             {prof.fft_size}   CP {prof.cp_length} samples"
        f" ({prof.cp_duration * 1000:.2f} ms)",
        f"subcarrier spacing:   {prof.subcarrier_spacing:.3f} Hz",
        f"active carriers:      {prof.num_carriers}"
        f"  ({prof.num_data_carriers} data + {prof.num_pilots} pilot)",
        f"occupied bandwidth:   {prof.occupied_bandwidth:.0f} Hz"
        f"  ({low:.0f}-{high:.0f} Hz baseband)",
        "                      [profile-derived; sample rate is NOT bandwidth,"
        " and no RF claim is made]",
        f"symbol duration:      {prof.symbol_duration * 1000:.2f} ms",
        f"MCS:                  {scheme.label}",
        f"bits per symbol:      {coded} coded / "
        f"{coded * rate.numerator // rate.denominator} information",
        f"PHY rate:             {net:.0f} bit/s"
        "  [payload carriers only, before preamble/header/ACK overhead]",
        f"block size:           {prof.block_size} bytes",
        f"header:               {header_symbols(prof)} symbols at"
        f" {mcs(0).label}",
    ]


def run_single(prof: OfdmProfile, index: int, payload_bytes: int,
               snr_db: float, seed: int, wav_out: Path | None) -> int:
    """One block through the channel, reported in full. Returns an exit code."""
    rng = np.random.default_rng(seed)
    payload = rng.integers(0, 256, payload_bytes, dtype=np.uint8).tobytes()
    blocks = split_blocks(payload, prof.block_size)
    if len(blocks) > 1:
        print(f"note: {payload_bytes} bytes is {len(blocks)} blocks; benching the"
              f" first {len(blocks[0])} of them. Use --arq for the whole message.")
    block = blocks[0]

    header = PhyHeader(OfdmFrameType.DATA, msg_id=0x0FD, block_seq=0,
                       block_count=len(blocks), mcs=index, payload_len=len(block))
    clean = build_burst(prof, header, block)
    channel = realistic(prof, snr_db=snr_db, seed=seed)
    aired = channel(clean)
    decoded = decode_burst(prof, aired)
    metrics = decoded.metrics
    identical = decoded.payload == block

    print("Guardian OFDM VHF bench")
    print("=" * 66)
    for line in describe_profile(prof, index):
        print(line)
    print("-" * 66)
    print(f"channel:              {channel.spec.describe()}")
    print(f"                      [SNR is in-band; the wideband SNR of this audio"
          f" is {10 * math.log10(prof.bandwidth_fraction):+.1f} dB]")
    print(f"TX payload:           {len(block)} bytes")
    print(f"waveform:             {len(clean)} samples"
          f"  ({burst_duration(prof, header):.3f} s at {prof.sample_rate} Hz)")
    print(f"crest factor:         {20 * math.log10(np.max(np.abs(clean)) / np.sqrt(np.mean(clean ** 2))):.1f} dB"
          f"  (TX RMS {np.sqrt(np.mean(clean ** 2)):.3f} of full scale)")
    print("-" * 66)
    print(f"simulated SNR:        {snr_db:.1f} dB in-band")
    print(f"measured SNR:         "
          f"{'unavailable' if metrics.snr_db is None else f'{metrics.snr_db:.1f} dB'}")
    print(f"EVM:                  "
          f"{'unavailable' if metrics.evm_rms is None else f'{metrics.evm_rms * 100:.2f} %'}"
          f"  ({metrics.evm_source})")
    print(f"sync confidence:      {metrics.sync_confidence:.3f}"
          if metrics.sync_confidence is not None else "sync confidence:      unavailable")
    print(f"CFO estimate:         {metrics.cfo_hz:+.2f} Hz"
          if metrics.cfo_hz is not None else "CFO estimate:         unavailable")
    if metrics.phase_slope is not None:
        print(f"pilot phase slope:    {metrics.phase_slope:+.5f} rad/carrier"
              "  (timing or clock offset)")
    if metrics.audio_rms is not None:
        print(f"RX audio RMS:         {metrics.audio_rms:.4f} of full scale"
              f"  (crest {metrics.crest_factor_db:.1f} dB)")
    if metrics.channel_response is not None:
        power = np.abs(metrics.channel_response) ** 2
        print(f"channel response:     {10 * np.log10(power.max() / power.min()):.1f} dB"
              " spread across the band")
    print(f"uncoded BER (theory): {uncoded_ber(mcs(index).modulation, snr_db):.2e}"
          "  [the code must beat this by orders of magnitude]")
    print("retries:              0  [single burst; use --arq to exercise ARQ]")
    print(f"result:               {'PASS' if identical else 'FAIL'}"
          f"{'' if metrics.error is None else '  -- ' + metrics.error}")
    print(f"RX payload identical: {'YES' if identical else 'NO'}")

    if wav_out is not None:
        write_wav(wav_out, aired, prof.sample_rate)
        print(f"\nwrote {wav_out}  ({len(aired) / prof.sample_rate:.2f} s,"
              f" {prof.sample_rate} Hz mono 16-bit)")
    return 0 if identical else 1


def run_arq(prof: OfdmProfile, index: int, payload_bytes: int, snr_db: float,
            seed: int) -> int:
    """A whole message across a simulated duplex link, with acknowledgements."""
    rng = np.random.default_rng(seed)
    payload = rng.integers(0, 256, payload_bytes, dtype=np.uint8).tobytes()
    spec = ChannelSpec(snr_db=snr_db, gain=0.7, delay=911, freq_offset_hz=2.0,
                       multipath=((0, 1.0), (int(prof.sample_rate / 1000), 0.3)),
                       trailing=prof.symbol_samples)
    near, far = simulated_pair(prof, spec, seed=seed)
    sender = OfdmLink(prof, near, mcs_index=index, ptt_turnaround=0.25,
                      timeout_margin=0.5, on_log=lambda m: print(f"  tx | {m}"))
    receiver = OfdmLink(prof, far, mcs_index=index, ptt_turnaround=0.25,
                        timeout_margin=0.5, on_log=lambda m: print(f"  rx | {m}"))

    print("Guardian OFDM VHF bench -- ARQ over a simulated duplex link")
    print("=" * 66)
    for line in describe_profile(prof, index):
        print(line)
    print(f"channel:              {spec.describe()}")
    print("-" * 66)

    received: dict[str, bytes | None] = {}
    listener = threading.Thread(
        target=lambda: received.__setitem__("data", receiver.receive_message(msg_id=0x0FD)),
        daemon=True,
    )
    listener.start()
    ok = sender.send_message(0x0FD, payload)
    listener.join(timeout=600)
    identical = received.get("data") == payload

    print("-" * 66)
    print(f"TX payload:           {len(payload)} bytes"
          f" in {len(split_blocks(payload, prof.block_size))} blocks")
    print(f"simulated SNR:        {snr_db:.1f} dB in-band")
    snr = receiver.adaptation.mean_snr_db
    print(f"measured SNR:         "
          f"{'unavailable' if snr is None else f'{snr:.1f} dB'}  (mean over bursts)")
    evm = receiver.adaptation.evm_history
    print(f"EVM:                  "
          f"{'unavailable' if not evm else f'{float(np.mean(evm)) * 100:.2f} %'}")
    last = receiver.last_metrics
    if last is not None:
        print(f"sync confidence:      {last.sync_confidence:.3f}"
              if last.sync_confidence is not None
              else "sync confidence:      unavailable")
        print(f"CFO estimate:         {last.cfo_hz:+.2f} Hz"
              if last.cfo_hz is not None else "CFO estimate:         unavailable")
        if last.phase_slope is not None:
            print(f"pilot phase slope:    {last.phase_slope:+.5f} rad/carrier")
        if last.audio_rms is not None:
            print(f"RX audio RMS:         {last.audio_rms:.4f} of full scale"
                  f"  (crest {last.crest_factor_db:.1f} dB)")
    worst = receiver.adaptation.worst_carriers
    if worst is not None and receiver.adaptation.carrier_power is not None:
        power = receiver.adaptation.carrier_power
        print(f"channel response:     "
              f"{10 * np.log10(power.max() / power.min()):.1f} dB spread;"
              f" weakest carriers {[int(index) for index in worst[:3]]}")
    print(f"uncoded BER (theory): {uncoded_ber(mcs(index).modulation, snr_db):.2e}")
    print(f"retries:              {sender.status.retries}")
    print(f"blocks:               {sender.adaptation.summary()}")
    per = sender.adaptation.packet_error_rate
    print(f"packet error rate:    "
          f"{'unavailable' if per is None else f'{per * 100:.1f} %'}")
    print(f"channel occupancy:    {sender.channel_seconds:.1f} s of airtime"
          " (both directions, plus PTT turnaround)")
    print(f"measured throughput:  "
          f"{'unavailable' if sender.status.est_bitrate_bps is None else f'{sender.status.est_bitrate_bps:.0f} bit/s'}"
          "  [end to end, ACKs and retries included]")
    print(f"result:               {'PASS' if ok and identical else 'FAIL'}")
    print(f"RX payload identical: {'YES' if identical else 'NO'}")
    return 0 if (ok and identical) else 1


def run_sweep(prof: OfdmProfile, index: int, runs: int, seed: int) -> int:
    """Decode rate and measured SNR against applied SNR, one block per run."""
    rng = np.random.default_rng(seed)
    payload = rng.integers(0, 256, prof.block_size, dtype=np.uint8).tobytes()
    header = PhyHeader(OfdmFrameType.DATA, 0x0FD, 0, 1, index, len(payload))
    clean = build_burst(prof, header, payload)

    print("Guardian OFDM VHF bench -- SNR sweep")
    print("=" * 66)
    for line in describe_profile(prof, index):
        print(line)
    print("-" * 66)
    print(f"{runs} seeded runs per point, one {len(payload)}-byte block each.")
    print(f"{'in-band SNR':>12} {'decoded':>9} {'measured':>10} {'EVM':>8}"
          f" {'wrong bytes':>12} {'uncoded BER':>12}")
    worst = 0
    for snr_db in (24, 20, 16, 14, 12, 10, 8, 6, 5, 4, 2):
        good = 0
        wrong = 0
        measured: list[float] = []
        evms: list[float] = []
        for run in range(runs):
            spec = ChannelSpec(snr_db=float(snr_db), delay=577,
                               trailing=prof.symbol_samples)
            decoded = decode_burst(prof, Channel(prof, spec, seed=seed + run)(clean))
            if decoded.metrics.snr_db is not None:
                measured.append(decoded.metrics.snr_db)
            if decoded.metrics.evm_rms is not None:
                evms.append(decoded.metrics.evm_rms)
            if decoded.payload == payload:
                good += 1
            elif decoded.payload is not None:
                # The one outcome that must never happen at any SNR: bytes
                # delivered that are not the bytes sent.
                wrong += 1
        worst = max(worst, wrong)
        print(f"{snr_db:>9} dB {good:>4}/{runs:<4}"
              f" {(f'{np.mean(measured):.1f} dB' if measured else '   --   '):>10}"
              f" {(f'{np.mean(evms) * 100:.1f} %' if evms else '  --  '):>8}"
              f" {wrong:>12} {uncoded_ber(mcs(index).modulation, snr_db):>12.1e}")
    print("-" * 66)
    print(f"wrong-byte deliveries: {worst}"
          f"  ({'PASS -- the CRC never let a bad block through' if not worst else 'FAIL'})")
    return 0 if not worst else 1


def run_read_wav(prof: OfdmProfile, path: Path) -> int:
    """Decode a captured WAV file, whatever produced it."""
    samples, rate = read_wav(path)
    print("Guardian OFDM VHF bench -- decoding a WAV capture")
    print("=" * 66)
    print(f"file:                 {path}")
    print(f"samples:              {len(samples)}"
          f"  ({len(samples) / rate:.2f} s at {rate} Hz)")
    if rate != prof.sample_rate:
        print(f"result:               FAIL -- file is {rate} Hz, profile"
              f" {prof.name} expects {prof.sample_rate} Hz")
        return 1
    print(f"audio RMS:            {np.sqrt(np.mean(samples ** 2)):.4f} of full scale")
    decoded = decode_burst(prof, samples)
    metrics = decoded.metrics
    print("-" * 66)
    if decoded.header is None:
        print(f"result:               FAIL -- {metrics.error}")
        return 1
    print(f"header:               {decoded.header.summary()}")
    print(f"measured SNR:         "
          f"{'unavailable' if metrics.snr_db is None else f'{metrics.snr_db:.1f} dB'}")
    print(f"EVM:                  "
          f"{'unavailable' if metrics.evm_rms is None else f'{metrics.evm_rms * 100:.2f} %'}")
    print(f"CFO estimate:         {metrics.cfo_hz:+.2f} Hz")
    print(f"sync confidence:      {metrics.sync_confidence:.3f}")
    print(f"payload:              "
          f"{'--' if decoded.payload is None else f'{len(decoded.payload)} bytes'}")
    print(f"result:               {'PASS' if decoded.ok else 'FAIL'}"
          f"{'' if metrics.error is None else '  -- ' + metrics.error}")
    return 0 if decoded.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bench the Guardian OFDM VHF modem without a radio.")
    parser.add_argument("--profile", default=BENCH.name, choices=profile_names(),
                        help="waveform profile (default: %(default)s)")
    parser.add_argument("--mcs", type=int, default=1,
                        help="MCS index for the data section (default: %(default)s)")
    parser.add_argument("--bytes", type=int, default=4096,
                        help="payload size (default: %(default)s)")
    parser.add_argument("--snr", type=float, default=15.0,
                        help="in-band SNR in dB; use a large value for an ideal"
                             " channel (default: %(default)s)")
    parser.add_argument("--seed", type=int, default=0xA5,
                        help="channel seed (default: 0x%(default)X)")
    parser.add_argument("--single", action="store_true",
                        help="bench one burst in detail instead of a whole"
                             " message; this is the mode that reports the"
                             " per-carrier receiver measurements")
    parser.add_argument("--sweep", action="store_true",
                        help="report decode rate against SNR")
    parser.add_argument("--runs", type=int, default=10,
                        help="runs per sweep point (default: %(default)s)")
    parser.add_argument("--write-wav", type=Path, metavar="FILE",
                        help="also write the channel output as a WAV file")
    parser.add_argument("--read-wav", type=Path, metavar="FILE",
                        help="decode a WAV capture instead of simulating")
    args = parser.parse_args(argv)

    prof = profile(args.profile)
    mcs(args.mcs)  # reject an unknown index before doing any work

    if args.read_wav is not None:
        return run_read_wav(prof, args.read_wav)
    if args.sweep:
        return run_sweep(prof, args.mcs, args.runs, args.seed)
    if args.single or args.write_wav is not None:
        return run_single(prof, args.mcs, args.bytes, args.snr, args.seed,
                          args.write_wav)
    return run_arq(prof, args.mcs, args.bytes, args.snr, args.seed)


if __name__ == "__main__":
    raise SystemExit(main())
