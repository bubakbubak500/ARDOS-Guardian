"""Benchmark facade for Guardian G2's experimental waveform families."""

from __future__ import annotations

import math
import threading
import time
import wave
from pathlib import Path

import numpy as np

from ..ofdm import bench as ofdm_bench
from ..ofdm.channel import Channel, ChannelSpec, realistic
from ..ofdm.adaptation import AdaptationConfig, LinkAdaptationController
from ..ofdm.coding import FecProfile, effective_rate, encoded_bits, fec_profile, fec_spec
from ..ofdm.config import sc_mcs
from ..ofdm.framing import OfdmFrameType, PhyHeader, split_blocks
from ..ofdm.link import OfdmLink, simulated_pair
from ..ofdm.metrics import LinkMetrics
from .config import WaveformProfile
from .constellation import bits_per_symbol, coded_capacity
from .framing import ExperimentalBurstCodec, header_blocks

WaveformFacts = ofdm_bench.WaveformFacts
BurstResult = ofdm_bench.BurstResult
CaptureResult = ofdm_bench.CaptureResult
TransferResult = ofdm_bench.TransferResult


def describe(profile: WaveformProfile, mcs_index: int,
             fec: FecProfile | int | str = FecProfile.FEC_1_2) -> WaveformFacts:
    scheme = sc_mcs(mcs_index)
    selected_fec = fec_profile(fec)
    low, high = profile.occupied_band
    coded = coded_capacity(profile.points_per_block, scheme.modulation)
    nominal = fec_spec(selected_fec).rate
    information = coded * nominal.numerator // nominal.denominator
    header = PhyHeader(
        OfdmFrameType.DATA, 0, mcs=mcs_index, fec=selected_fec,
        payload_len=profile.block_size,
    )
    info_bits = profile.block_size * 8
    transmitted = encoded_bits(profile.block_size + 2, selected_fec)
    raw_rate = coded / profile.block_duration
    return WaveformFacts(
        profile=profile.name,
        sample_rate=profile.sample_rate,
        fft_size=(0 if profile.is_single_carrier else profile.fft_size),
        cp_length=(0 if profile.is_single_carrier else profile.cp_length),
        cp_ms=(0.0 if profile.is_single_carrier else
               profile.cp_length / profile.sample_rate * 1000.0),
        subcarrier_spacing=(profile.symbol_rate if profile.is_single_carrier
                            else profile.carrier_spacing_hz),
        symbol_ms=profile.block_duration * 1000.0,
        carriers=(1 if profile.is_single_carrier else profile.num_carriers),
        data_carriers=profile.points_per_block,
        pilots=profile.num_pilots,
        band_low=low,
        band_high=high,
        occupied=profile.occupied_bandwidth,
        block_size=profile.block_size,
        mcs_index=scheme.index,
        mcs_label=scheme.label,
        coded_bits_per_symbol=coded,
        information_bits_per_symbol=information,
        phy_rate=raw_rate * nominal.numerator / nominal.denominator,
        header_symbols=header_blocks(profile),
        full_block_seconds=ExperimentalBurstCodec.burst_duration(profile, header),
        fec_label=fec_spec(selected_fec).label,
        raw_data_bps=raw_rate,
        block_information_bits=info_bits,
        block_encoded_bits=transmitted,
        effective_fec_rate=effective_rate(info_bits, transmitted),
    )


def run_burst(profile: WaveformProfile, mcs_index: int = 2, *,
              payload_bytes: int = 512, snr_db: float = 20.0,
              seed: int = 0xA5, wav_path: Path | str | None = None,
              spec: ChannelSpec | None = None,
              fec: FecProfile | int | str = FecProfile.FEC_1_2) -> BurstResult:
    selected_fec = fec_profile(fec)
    facts = describe(profile, mcs_index, selected_fec)
    rng = np.random.default_rng(seed)
    size = min(int(payload_bytes), profile.block_size)
    payload = rng.integers(0, 256, size, dtype=np.uint8).tobytes()
    header = PhyHeader(
        OfdmFrameType.DATA, 0x0FD, 0, 1, mcs_index, len(payload),
        fec=selected_fec,
    )
    codec = ExperimentalBurstCodec()
    clean = codec.build_burst(profile, header, payload)
    channel = (Channel(profile, spec, seed=seed) if spec is not None
               else realistic(profile, snr_db=snr_db, seed=seed))
    aired = channel(clean)
    decoded = codec.decode_burst(profile, aired)
    rms = float(np.sqrt(np.mean(clean ** 2)))
    peak = float(np.max(np.abs(clean)))
    written = None
    if wav_path is not None:
        written = ofdm_bench.write_wav(wav_path, aired, profile.sample_rate)
    return BurstResult(
        facts=facts,
        channel=channel.spec.describe(),
        payload_bytes=len(payload),
        applied_snr_db=snr_db if channel.spec.snr_db is None else channel.spec.snr_db,
        wideband_offset_db=10.0 * math.log10(profile.bandwidth_fraction),
        samples=len(clean),
        seconds=len(clean) / profile.sample_rate,
        tx_rms=rms,
        tx_crest_db=(20.0 * math.log10(peak / rms)
                     if rms > 0.0 and peak > 0.0 else float("nan")),
        metrics=decoded.metrics,
        identical=decoded.payload == payload,
        # A modulation-only closed form is misleading for FTN and SEFDM; their
        # interference-aware measured decoder result is the benchmark.
        uncoded_ber=float("nan"),
        wav_path=written,
    )


def run_transfer(profile: WaveformProfile, mcs_index: int = 2, *,
                 payload_bytes: int = 8192, snr_db: float = 30.0,
                 seed: int = 0xA5, ptt_turnaround: float = 0.25,
                 timeout: float = 120.0,
                 fec: FecProfile | int | str = FecProfile.FEC_7_8,
                 burst_bytes: int = 8192, arq_block_bytes: int = 512,
                 spec: ChannelSpec | None = None,
                 train_bursts: int = 1, superframe: bool = False,
                 adaptive_train: bool = False) -> TransferResult:
    selected_fec = fec_profile(fec)
    config = AdaptationConfig(
        adaptive_fec=False,
        fixed_fec=selected_fec,
        adaptive_burst=False,
        fixed_burst_bytes=max(arq_block_bytes, int(burst_bytes)),
        min_burst_bytes=max(arq_block_bytes, int(burst_bytes)),
        max_burst_bytes=max(arq_block_bytes, int(burst_bytes)),
        arq_block_bytes=int(arq_block_bytes),
    )
    sender_controller = LinkAdaptationController(config, mcs_index=mcs_index)
    receiver_controller = LinkAdaptationController(config, mcs_index=mcs_index)
    facts = describe(profile, mcs_index, selected_fec)
    rng = np.random.default_rng(seed)
    payload = rng.integers(0, 256, int(payload_bytes), dtype=np.uint8).tobytes()
    one_ms = max(1, int(profile.sample_rate / 1000))
    channel_spec = spec or ChannelSpec(
        snr_db=snr_db,
        gain=0.7,
        delay=911,
        freq_offset_hz=2.0,
        multipath=((0, 1.0), (one_ms, 0.3)),
        trailing=profile.symbol_samples,
    )
    near, far = simulated_pair(profile, channel_spec, seed=seed)
    codec = ExperimentalBurstCodec()
    sender = OfdmLink(
        profile, near, mcs_index=mcs_index, ptt_turnaround=ptt_turnaround,
        timeout_margin=0.5, controller=sender_controller, codec=codec,
        train_bursts=train_bursts,
        superframe=superframe, adaptive_train=adaptive_train,
    )
    receiver = OfdmLink(
        profile, far, mcs_index=mcs_index, ptt_turnaround=ptt_turnaround,
        timeout_margin=0.5, controller=receiver_controller, codec=codec,
        train_bursts=train_bursts,
        superframe=superframe, adaptive_train=adaptive_train,
    )
    received: dict[str, bytes | None] = {}
    listener = threading.Thread(
        target=lambda: received.setdefault(
            "data", receiver.receive_message(msg_id=0x0FD)
        ),
        daemon=True,
    )
    listener.start()
    started = time.monotonic()
    sent_ok = sender.send_message(0x0FD, payload)
    listener.join(timeout=timeout)
    wall_seconds = time.monotonic() - started
    evm_values = receiver.adaptation.evm_history
    return TransferResult(
        facts=facts,
        channel=channel_spec.describe(),
        payload_bytes=len(payload),
        blocks=len(split_blocks(payload, arq_block_bytes)),
        applied_snr_db=snr_db,
        measured_snr_db=receiver.adaptation.mean_snr_db,
        measured_evm=float(np.mean(evm_values)) if evm_values else None,
        retries=sender.status.retries,
        blocks_acked=sender.adaptation.blocks_acked,
        packet_error_rate=sender.adaptation.packet_error_rate,
        channel_seconds=sender.channel_seconds,
        throughput_bps=sender.status.est_bitrate_bps,
        identical=received.get("data") == payload,
        sent_ok=sent_ok,
        fec_initial=fec_spec(selected_fec).label,
        fec_final=fec_spec(selected_fec).label,
        burst_initial=burst_bytes,
        burst_final=burst_bytes,
        arq_block_bytes=arq_block_bytes,
        retransmitted_bytes=sender.status.retransmitted_bytes,
        data_bursts=near.transmissions,
        ack_bursts=far.transmissions,
        data_airtime_seconds=near.samples_sent / profile.sample_rate,
        ack_airtime_seconds=far.samples_sent / profile.sample_rate,
        turnaround_seconds=(near.transmissions + far.transmissions) * ptt_turnaround,
        raw_data_bps=facts.raw_data_bps,
        fec_adjusted_bps=facts.phy_rate,
        protocol_payload_bps=(
            None if near.samples_sent + far.samples_sent <= 0 else
            len(payload) * 8.0 * profile.sample_rate /
            (near.samples_sent + far.samples_sent)
        ),
        protocol_overhead_bytes=sender.status.protocol_overhead_bytes,
        wall_seconds=wall_seconds,
    )


def make_test_burst(profile: WaveformProfile, mcs_index: int = 2, *,
                    payload_bytes: int = 512, seed: int = 0xA5,
                    repeats: int = 1, gap_seconds: float = 1.0,
                    wav_path: Path | str | None = None,
                    fec: FecProfile | int | str = FecProfile.FEC_1_2):
    rng = np.random.default_rng(seed)
    selected_fec = fec_profile(fec)
    size = min(int(payload_bytes), profile.block_size)
    codec = ExperimentalBurstCodec()
    parts: list[np.ndarray] = []
    gap = np.zeros(int(max(0.0, gap_seconds) * profile.sample_rate))
    count = max(1, int(repeats))
    for index in range(count):
        payload = rng.integers(0, 256, size, dtype=np.uint8).tobytes()
        header = PhyHeader(
            OfdmFrameType.DATA, 0x0FD, index, count, mcs_index, len(payload),
            fec=selected_fec,
        )
        if parts:
            parts.append(gap)
        parts.append(codec.build_burst(profile, header, payload))
    waveform = np.concatenate(parts) if parts else np.zeros(0)
    lead = np.zeros(int(max(gap_seconds, 0.5) * profile.sample_rate))
    waveform = np.concatenate([lead, waveform, gap])
    written = None
    if wav_path is not None:
        written = ofdm_bench.write_wav(wav_path, waveform, profile.sample_rate)
    return waveform, written


def decode_capture(profile: WaveformProfile, path: Path | str) -> CaptureResult:
    path = Path(path)
    try:
        samples, rate = ofdm_bench.read_wav(path)
    except (OSError, ValueError, wave.Error) as exc:
        return CaptureResult(
            path=path, samples=0, sample_rate=0, expected_rate=profile.sample_rate,
            seconds=0.0, audio_rms=0.0, audio_peak=0.0, header=None,
            payload_bytes=None, metrics=LinkMetrics(error=str(exc)), error=str(exc),
        )
    rms = float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0
    peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
    if rate != profile.sample_rate:
        reason = f"the file is {rate} Hz but profile {profile.name} expects {profile.sample_rate} Hz"
        return CaptureResult(
            path=path, samples=len(samples), sample_rate=rate,
            expected_rate=profile.sample_rate, seconds=len(samples) / max(rate, 1),
            audio_rms=rms, audio_peak=peak, header=None, payload_bytes=None,
            metrics=LinkMetrics(error=reason), error=reason,
        )
    decoded = ExperimentalBurstCodec.decode_burst(profile, samples)
    return CaptureResult(
        path=path, samples=len(samples), sample_rate=rate,
        expected_rate=profile.sample_rate, seconds=len(samples) / rate,
        audio_rms=rms, audio_peak=peak, header=decoded.header,
        payload_bytes=None if decoded.payload is None else len(decoded.payload),
        metrics=decoded.metrics, error=decoded.metrics.error or "",
    )
