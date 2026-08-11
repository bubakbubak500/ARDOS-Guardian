"""Compare Guardian G2 waveform families on one reproducible 2.7 kHz case."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from guardian.ofdm import bench as ofdm_bench  # noqa: E402
from guardian.ofdm.adaptation import AdaptationConfig  # noqa: E402
from guardian.ofdm.channel import ChannelSpec  # noqa: E402
from guardian.ofdm.coding import fec_profile  # noqa: E402
from guardian.ofdm.config import BENCH  # noqa: E402
from guardian.waveforms import bench as experimental_bench  # noqa: E402
from guardian.waveforms.config import (SC_FTN_2K7, SC_HS_2K7,  # noqa: E402
                                       SEFDM_2K7)


@dataclass(frozen=True)
class Row:
    waveform: str
    occupied_hz: float
    mcs: int
    fec: str
    snr_db: float
    decoded: int
    runs: int
    payload_bytes: int
    airtime_seconds: float
    payload_goodput_bps: float
    crest_db: float
    residual_snr_db: float | None
    last_error: str


@dataclass(frozen=True)
class TransferRow:
    waveform: str
    mcs: int
    passed: bool
    payload_bytes: int
    application_goodput_bps: float
    application_bytes_per_second: float
    protocol_payload_bps: float
    channel_seconds: float
    retries: int


def measure(profile, engine, args) -> Row:
    if getattr(profile, "family", "ofdm") == "sefdm" and args.sefdm_mcs is not None:
        selected_mcs = args.sefdm_mcs
    else:
        selected_mcs = min(args.mcs, 3) if engine is ofdm_bench else args.mcs
    results = []
    for run in range(args.runs):
        spec = None
        if args.channel == "awgn":
            spec = ChannelSpec(
                snr_db=args.snr,
                delay=311,
                trailing=profile.symbol_samples,
            )
        results.append(engine.run_burst(
            profile, selected_mcs, payload_bytes=args.payload, snr_db=args.snr,
            seed=args.seed + run, fec=args.fec, spec=spec,
        ))
    passed = [result for result in results if result.passed]
    seconds = float(np.mean([result.seconds for result in results]))
    residual = [result.metrics.residual_snr_db for result in results
                if result.metrics.residual_snr_db is not None]
    return Row(
        waveform=profile.name,
        occupied_hz=profile.occupied_bandwidth,
        mcs=selected_mcs,
        fec=args.fec,
        snr_db=args.snr,
        decoded=len(passed),
        runs=len(results),
        payload_bytes=args.payload,
        airtime_seconds=seconds,
        payload_goodput_bps=(args.payload * 8.0 / seconds if seconds > 0.0 else 0.0),
        crest_db=float(np.mean([result.tx_crest_db for result in results])),
        residual_snr_db=float(np.mean(residual)) if residual else None,
        last_error=next((result.metrics.error or "" for result in reversed(results)
                         if not result.passed), ""),
    )


def measure_transfer(profile, engine, args) -> TransferRow:
    if getattr(profile, "family", "ofdm") == "sefdm" and args.sefdm_mcs is not None:
        selected_mcs = args.sefdm_mcs
    else:
        selected_mcs = min(args.mcs, 3) if engine is ofdm_bench else args.mcs
    selected_fec = fec_profile(args.fec)
    if engine is ofdm_bench:
        fixed = AdaptationConfig(
            adaptive_fec=False,
            fixed_fec=selected_fec,
            adaptive_burst=False,
            fixed_burst_bytes=args.burst_bytes,
            min_burst_bytes=args.burst_bytes,
            max_burst_bytes=args.burst_bytes,
            arq_block_bytes=args.arq_block_bytes,
        )
        result = engine.run_transfer(
            profile,
            selected_mcs,
            payload_bytes=args.full_transfer,
            snr_db=args.snr,
            seed=args.seed,
            ptt_turnaround=args.turnaround,
            adaptation_config=fixed,
        )
    else:
        result = engine.run_transfer(
            profile,
            selected_mcs,
            payload_bytes=args.full_transfer,
            snr_db=args.snr,
            seed=args.seed,
            ptt_turnaround=args.turnaround,
            fec=selected_fec,
            burst_bytes=args.burst_bytes,
            arq_block_bytes=args.arq_block_bytes,
        )
    goodput = float(result.throughput_bps or 0.0)
    return TransferRow(
        waveform=profile.name,
        mcs=selected_mcs,
        passed=result.passed,
        payload_bytes=result.payload_bytes,
        application_goodput_bps=goodput,
        application_bytes_per_second=goodput / 8.0,
        protocol_payload_bps=float(result.protocol_payload_bps or 0.0),
        channel_seconds=result.channel_seconds,
        retries=result.retries,
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Fair OFDM/SC-HS/SC-FTN/SEFDM 2.7 kHz burst benchmark"
    )
    result.add_argument("--snr", type=float, default=30.0)
    result.add_argument("--mcs", type=int, default=3)
    result.add_argument(
        "--sefdm-mcs", type=int, choices=range(7),
        help="override MCS for SEFDM (MCS6/32-APSK is the robust 2.3.1 choice)",
    )
    result.add_argument("--fec", default="7/8", choices=("1/2", "2/3", "3/4", "5/6", "7/8"))
    result.add_argument("--payload", type=int, default=512)
    result.add_argument("--runs", type=int, default=8)
    result.add_argument("--seed", type=int, default=0x213)
    result.add_argument("--channel", choices=("awgn", "realistic"), default="awgn")
    result.add_argument(
        "--full-transfer", type=int, metavar="BYTES", default=0,
        help="also run a full selective-repeat transfer for burst-reliable families",
    )
    result.add_argument(
        "--burst-bytes", type=int, default=8192,
        choices=(256, 512, 1024, 2048, 4096, 8192, 16384),
    )
    result.add_argument(
        "--arq-block-bytes", type=int, default=512, choices=(256, 512, 1024),
    )
    result.add_argument("--turnaround", type=float, default=0.25)
    result.add_argument("--json", type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    cases = (
        (BENCH, ofdm_bench),
        (SC_HS_2K7, experimental_bench),
        (SC_FTN_2K7, experimental_bench),
        (SEFDM_2K7, experimental_bench),
    )
    rows = [measure(profile, engine, args) for profile, engine in cases]
    print("waveform      band Hz  MCS  decoded   air s  payload bit/s  crest dB  residual dB")
    for row in rows:
        residual = "--" if row.residual_snr_db is None else f"{row.residual_snr_db:7.2f}"
        print(
            f"{row.waveform:12} {row.occupied_hz:7.0f}  {row.mcs:3d}  "
            f"{row.decoded:2}/{row.runs:<2}   {row.airtime_seconds:5.3f}  "
            f"{row.payload_goodput_bps:13.0f}  {row.crest_db:8.2f}  {residual}"
        )
        if row.last_error:
            print(f"  last rejection: {row.last_error}")
    transfers: list[TransferRow] = []
    if args.full_transfer > 0:
        print("\nfull transfer (same deterministic echo/CFO channel)")
        print("waveform      passed  payload B  app bit/s  app B/s  channel s  retries")
        for (profile, engine), burst in zip(cases, rows):
            if burst.decoded != burst.runs:
                print(f"{profile.name:12} skipped: burst preflight was not reliable")
                continue
            transfer = measure_transfer(profile, engine, args)
            transfers.append(transfer)
            print(
                f"{transfer.waveform:12} {str(transfer.passed):>6}  "
                f"{transfer.payload_bytes:9d}  "
                f"{transfer.application_goodput_bps:9.0f}  "
                f"{transfer.application_bytes_per_second:7.0f}  "
                f"{transfer.channel_seconds:9.3f}  {transfer.retries:7d}"
            )
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "bursts": [asdict(row) for row in rows],
                    "transfers": [asdict(row) for row in transfers],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    bursts_ok = any(row.decoded == row.runs for row in rows)
    transfers_ok = not transfers or all(row.passed for row in transfers)
    return 0 if bursts_ok and transfers_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
