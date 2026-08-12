"""Reproducible Guardian G2 waveform/MCS sweep over the end-to-end FM model."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from ..ofdm.coding import FecProfile, fec_profile, fec_spec
from ..ofdm.fm_channel import FmChannel, FmChannelSpec
from ..ofdm.framing import OfdmFrameType, PhyHeader, SubBlock
from .bench import describe
from .config import PROFILES, WaveformProfile
from .framing import ExperimentalBurstCodec


@dataclass(frozen=True)
class CapacityPoint:
    profile: str
    occupied_hz: float
    mcs: int
    fec: str
    rf_snr_db: float
    repeats: int
    frames_ok: int
    frame_success: float
    median_gmi_bits_per_symbol: float | None
    median_evm_rms: float | None
    nominal_phy_bps: float
    decoded_payload_bps: float
    seed: int


def benchmark_point(profile: WaveformProfile, mcs_index: int, *,
                    fec: FecProfile | int | str = FecProfile.LDPC_3_4,
                    rf_snr_db: float = 30.0, payload_bytes: int = 512,
                    repeats: int = 3, seed: int = 0x233) -> CapacityPoint:
    selected = fec_profile(fec)
    codec = ExperimentalBurstCodec()
    rng = np.random.default_rng(seed)
    successes = 0
    gmis: list[float] = []
    evms: list[float] = []
    payload_bits = 0
    samples = 0
    for repeat in range(max(1, int(repeats))):
        payload = rng.integers(
            0, 256, min(int(payload_bytes), profile.block_size), dtype=np.uint8
        ).tobytes()
        header = PhyHeader(
            OfdmFrameType.DATA, seed, block_seq=repeat,
            block_count=max(1, int(repeats)), mcs=mcs_index, fec=selected,
            payload_len=len(payload), subblock_count=1,
        )
        clean = codec.build_burst(
            profile, header, blocks=[SubBlock(repeat, payload)]
        )
        channel = FmChannel(
            profile,
            FmChannelSpec(
                rf_snr_db=float(rf_snr_db),
                tx_audio_high_hz=min(profile.occupied_band[1] + 150.0,
                                     profile.sample_rate / 2.0 - 100.0),
                rx_audio_high_hz=min(profile.occupied_band[1] + 150.0,
                                     profile.sample_rate / 2.0 - 100.0),
            ),
            seed=seed ^ repeat,
        )
        decoded = codec.decode_burst(profile, channel(clean))
        samples += len(clean)
        if decoded.payload == payload and decoded.ok:
            successes += 1
            payload_bits += len(payload) * 8
        if decoded.metrics.gmi_bits_per_symbol is not None:
            gmis.append(decoded.metrics.gmi_bits_per_symbol)
        if decoded.metrics.evm_rms is not None:
            evms.append(decoded.metrics.evm_rms)
    facts = describe(profile, mcs_index, selected)
    return CapacityPoint(
        profile.name, profile.occupied_bandwidth, int(mcs_index),
        fec_spec(selected).label, float(rf_snr_db), max(1, int(repeats)),
        successes, successes / max(1, int(repeats)),
        float(np.median(gmis)) if gmis else None,
        float(np.median(evms)) if evms else None,
        facts.phy_rate,
        payload_bits * profile.sample_rate / max(1, samples),
        int(seed),
    )


def write_report(points: list[CapacityPoint], destination: Path | str
                 ) -> tuple[Path, Path]:
    base = Path(destination)
    base.parent.mkdir(parents=True, exist_ok=True)
    json_path = Path(f"{base}.json")
    csv_path = Path(f"{base}.csv")
    rows = [asdict(point) for point in points]
    json_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(CapacityPoint.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(rows)
    return json_path, csv_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", nargs="+", default=["SC_FDE_FTN_2K7"])
    parser.add_argument("--mcs", nargs="+", type=int, default=[2, 3, 6, 20])
    parser.add_argument("--snr", nargs="+", type=float, default=[18.0, 24.0, 30.0])
    parser.add_argument("--fec", default="LDPC-3/4")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--payload", type=int, default=512)
    parser.add_argument("--seed", type=int, default=0x233)
    parser.add_argument("--output", type=Path, default=Path("g2-capacity-2.3.3"))
    args = parser.parse_args(argv)
    points = [
        benchmark_point(
            PROFILES[name], index, fec=args.fec, rf_snr_db=snr,
            payload_bytes=args.payload, repeats=args.repeats,
            seed=args.seed ^ index ^ int(snr * 10),
        )
        for name in args.profiles for index in args.mcs for snr in args.snr
    ]
    json_path, csv_path = write_report(points, args.output)
    print(json_path)
    print(csv_path)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as an operator tool
    raise SystemExit(main())
