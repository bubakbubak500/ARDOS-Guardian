"""Measuring the modem without a radio, as data rather than as printed text.

This is the engine behind both front ends: the Modem test workspace in the
application and `tools/ofdm_bench.py`. It lives in the package rather than in the
script because `tools/` is a directory of scripts, not an importable module, and
a frozen build has no `tools/` at all -- so anything the application needs has to
be here. There is one implementation and two ways to reach it, which is the only
arrangement in which the number an operator reads and the number a developer
reads cannot drift apart.

Everything returns dataclasses. Nothing here prints, and nothing here knows about
Qt, a console, or a radio.
"""

from __future__ import annotations

import math
import queue
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .channel import Channel, ChannelSpec, realistic
from .config import OfdmProfile, mcs
from .constellation import bits_per_symbol
from .framing import (OfdmFrameType, PhyHeader, build_burst, burst_duration,
                      decode_burst, header_symbols, section_symbols, split_blocks)
from .link import OfdmLink, simulated_pair
from .metrics import LinkMetrics

#: Full scale for the signed 16-bit PCM these tools read and write.
WAV_SCALE = 32767
WAV_SAMPLE_WIDTH = 2

#: In-band SNRs the sweep walks, strongest first. Chosen to straddle the point
#: where each MCS gives up (measured: 4 dB for BPSK, 7 for QPSK, 12 for 16-QAM,
#: 18 for 64-QAM) so the cliff is visible on any of them rather than falling off
#: the end of the table.
SWEEP_POINTS: tuple[float, ...] = (24.0, 20.0, 16.0, 14.0, 12.0, 10.0, 8.0,
                                   6.0, 5.0, 4.0, 2.0)


# -- theory anchor ----------------------------------------------------------- #

def _q(x: float) -> float:
    """Gaussian tail probability, from the stdlib error function."""
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def uncoded_ber(modulation: str, snr_db: float) -> float:
    """Textbook uncoded bit error rate for a square constellation.

    Reported beside the measured figures as a check on the whole chain's
    normalisation rather than on the code: if the modem's own numbers do not sit
    near this curve, something is scaled wrong long before any RF is involved.
    The coded result must of course be far better than this.
    """
    bits = bits_per_symbol(modulation)
    order = 1 << bits
    eb_over_n0 = 10.0 ** (snr_db / 10.0) / bits
    if bits <= 2:  # BPSK and QPSK are the same curve per bit
        return _q(math.sqrt(2.0 * eb_over_n0))
    root = math.sqrt(order)
    return ((4.0 / bits) * (1.0 - 1.0 / root)
            * _q(math.sqrt(3.0 * bits / (order - 1.0) * eb_over_n0)))


# -- WAV --------------------------------------------------------------------- #

def write_wav(path: Path | str, samples, sample_rate: int) -> Path:
    """Write mono 16-bit PCM, the one format these tools read."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(np.asarray(samples, dtype=np.float64), -1.0, 1.0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(WAV_SAMPLE_WIDTH)
        handle.setframerate(int(sample_rate))
        handle.writeframes((clipped * WAV_SCALE).astype("<i2").tobytes())
    return path


def read_wav(path: Path | str) -> tuple[np.ndarray, int]:
    """Read mono 16-bit PCM back to floats in -1..1; channel 0 of a stereo file."""
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    if width != WAV_SAMPLE_WIDTH:
        raise ValueError(f"{Path(path).name} is {width * 8}-bit; expected 16-bit PCM")
    data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / WAV_SCALE
    if channels > 1:
        data = data[::channels]
    return data, rate


# -- what a waveform is ------------------------------------------------------ #

@dataclass(frozen=True)
class WaveformFacts:
    """Everything about a profile and MCS that is worth putting on screen."""

    profile: str
    sample_rate: int
    fft_size: int
    cp_length: int
    cp_ms: float
    subcarrier_spacing: float
    symbol_ms: float
    carriers: int
    data_carriers: int
    pilots: int
    band_low: float
    band_high: float
    occupied: float
    block_size: int
    mcs_index: int
    mcs_label: str
    coded_bits_per_symbol: int
    information_bits_per_symbol: int
    phy_rate: float
    header_symbols: int
    full_block_seconds: float

    @property
    def band(self) -> str:
        return f"{self.band_low:.0f}-{self.band_high:.0f} Hz"


def describe(profile: OfdmProfile, mcs_index: int) -> WaveformFacts:
    """The parameters of one profile/MCS pairing, all derived, none stored twice."""
    scheme = mcs(mcs_index)
    low, high = profile.occupied_band
    coded = profile.coded_bits_per_symbol(scheme.bits_per_symbol)
    rate = scheme.code_rate
    information = coded * rate.numerator // rate.denominator
    header = PhyHeader(OfdmFrameType.DATA, 0, mcs=mcs_index,
                       payload_len=profile.block_size)
    return WaveformFacts(
        profile=profile.name,
        sample_rate=profile.sample_rate,
        fft_size=profile.fft_size,
        cp_length=profile.cp_length,
        cp_ms=profile.cp_duration * 1000.0,
        subcarrier_spacing=profile.subcarrier_spacing,
        symbol_ms=profile.symbol_duration * 1000.0,
        carriers=profile.num_carriers,
        data_carriers=profile.num_data_carriers,
        pilots=profile.num_pilots,
        band_low=low,
        band_high=high,
        occupied=profile.occupied_bandwidth,
        block_size=profile.block_size,
        mcs_index=scheme.index,
        mcs_label=scheme.label,
        coded_bits_per_symbol=coded,
        information_bits_per_symbol=information,
        phy_rate=coded * rate.numerator / rate.denominator / profile.symbol_duration,
        header_symbols=header_symbols(profile),
        full_block_seconds=burst_duration(profile, header),
    )


# -- one burst --------------------------------------------------------------- #

@dataclass(frozen=True)
class BurstResult:
    """One block through the channel simulator, measured."""

    facts: WaveformFacts
    channel: str
    payload_bytes: int
    applied_snr_db: float
    wideband_offset_db: float
    samples: int
    seconds: float
    tx_rms: float
    tx_crest_db: float
    metrics: LinkMetrics
    identical: bool
    uncoded_ber: float
    wav_path: Path | None = None

    @property
    def passed(self) -> bool:
        return self.identical

    @property
    def channel_spread_db(self) -> float | None:
        """Peak-to-trough of the measured channel response, in dB."""
        if self.metrics.channel_response is None:
            return None
        power = np.abs(self.metrics.channel_response) ** 2
        if not power.size or power.min() <= 0.0:
            return None
        return float(10.0 * np.log10(power.max() / power.min()))


def run_burst(profile: OfdmProfile, mcs_index: int = 1, *, payload_bytes: int = 512,
              snr_db: float = 15.0, seed: int = 0xA5,
              wav_path: Path | str | None = None,
              spec: ChannelSpec | None = None) -> BurstResult:
    """Push one block through the channel and report every measurement.

    The channel is the deterministic simulator with everything switched on --
    delay, gain, an echo, a dial error and a clock offset -- not a perfect
    array-to-array handover. A very large `snr_db` is how to ask for the ideal
    case.
    """
    facts = describe(profile, mcs_index)
    rng = np.random.default_rng(seed)
    size = min(int(payload_bytes), profile.block_size)
    payload = rng.integers(0, 256, size, dtype=np.uint8).tobytes()
    header = PhyHeader(OfdmFrameType.DATA, msg_id=0x0FD, block_seq=0, block_count=1,
                       mcs=mcs_index, payload_len=len(payload))
    clean = build_burst(profile, header, payload)
    channel = (Channel(profile, spec, seed=seed) if spec is not None
               else realistic(profile, snr_db=snr_db, seed=seed))
    aired = channel(clean)
    decoded = decode_burst(profile, aired)

    rms = float(np.sqrt(np.mean(clean ** 2)))
    peak = float(np.max(np.abs(clean)))
    written = None
    if wav_path is not None:
        written = write_wav(wav_path, aired, profile.sample_rate)

    return BurstResult(
        facts=facts,
        channel=channel.spec.describe(),
        payload_bytes=len(payload),
        applied_snr_db=snr_db if channel.spec.snr_db is None else channel.spec.snr_db,
        # White noise at the density a stated in-band SNR implies also fills the
        # rest of the audio band, so the wideband SNR of the same audio is lower
        # by this much. Quoting that figure instead would flatter the modem.
        wideband_offset_db=10.0 * math.log10(profile.bandwidth_fraction),
        samples=len(clean),
        seconds=len(clean) / profile.sample_rate,
        tx_rms=rms,
        tx_crest_db=(20.0 * math.log10(peak / rms)) if rms > 0 and peak > 0 else float("nan"),
        metrics=decoded.metrics,
        identical=decoded.payload == payload,
        uncoded_ber=uncoded_ber(mcs(mcs_index).modulation,
                                snr_db if channel.spec.snr_db is None
                                else channel.spec.snr_db),
        wav_path=written,
    )


# -- a whole transfer -------------------------------------------------------- #

@dataclass(frozen=True)
class TransferResult:
    """A whole message across a simulated duplex link, with acknowledgements."""

    facts: WaveformFacts
    channel: str
    payload_bytes: int
    blocks: int
    applied_snr_db: float
    measured_snr_db: float | None
    measured_evm: float | None
    retries: int
    blocks_acked: int
    packet_error_rate: float | None
    channel_seconds: float
    throughput_bps: float | None
    identical: bool
    sent_ok: bool
    log: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.sent_ok and self.identical


def run_transfer(profile: OfdmProfile, mcs_index: int = 1, *,
                 payload_bytes: int = 4096, snr_db: float = 15.0,
                 seed: int = 0xA5, ptt_turnaround: float = 0.25,
                 timeout: float = 600.0, on_log=None) -> TransferResult:
    """Move a whole message over the simulated duplex link and report the outcome."""
    facts = describe(profile, mcs_index)
    rng = np.random.default_rng(seed)
    payload = rng.integers(0, 256, int(payload_bytes), dtype=np.uint8).tobytes()
    one_ms = max(1, int(profile.sample_rate / 1000))
    spec = ChannelSpec(snr_db=snr_db, gain=0.7, delay=911, freq_offset_hz=2.0,
                       multipath=((0, 1.0), (one_ms, 0.3)),
                       trailing=profile.symbol_samples)
    near, far = simulated_pair(profile, spec, seed=seed)

    lines: list[str] = []

    def record(prefix: str):
        def sink(message: str) -> None:
            line = f"{prefix} | {message}"
            lines.append(line)
            if on_log is not None:
                on_log(line)
        return sink

    sender = OfdmLink(profile, near, mcs_index=mcs_index,
                      ptt_turnaround=ptt_turnaround, timeout_margin=0.5,
                      on_log=record("tx"))
    receiver = OfdmLink(profile, far, mcs_index=mcs_index,
                        ptt_turnaround=ptt_turnaround, timeout_margin=0.5,
                        on_log=record("rx"))

    received: dict[str, bytes | None] = {}
    listener = threading.Thread(
        target=lambda: received.__setitem__(
            "data", receiver.receive_message(msg_id=0x0FD)),
        daemon=True,
    )
    listener.start()
    sent_ok = sender.send_message(0x0FD, payload)
    listener.join(timeout=timeout)

    evm = receiver.adaptation.evm_history
    return TransferResult(
        facts=facts,
        channel=spec.describe(),
        payload_bytes=len(payload),
        blocks=len(split_blocks(payload, profile.block_size)),
        applied_snr_db=snr_db,
        measured_snr_db=receiver.adaptation.mean_snr_db,
        measured_evm=float(np.mean(evm)) if evm else None,
        retries=sender.status.retries,
        blocks_acked=sender.adaptation.blocks_acked,
        packet_error_rate=sender.adaptation.packet_error_rate,
        channel_seconds=sender.channel_seconds,
        throughput_bps=sender.status.est_bitrate_bps,
        identical=received.get("data") == payload,
        sent_ok=sent_ok,
        log=tuple(lines),
    )


# -- decode rate against SNR ------------------------------------------------- #

@dataclass(frozen=True)
class SweepPoint:
    applied_snr_db: float
    runs: int
    decoded: int
    wrong_bytes: int
    measured_snr_db: float | None
    measured_evm: float | None
    uncoded_ber: float

    @property
    def reliable(self) -> bool:
        return self.decoded == self.runs


@dataclass(frozen=True)
class SweepResult:
    facts: WaveformFacts
    runs: int
    points: tuple[SweepPoint, ...] = ()
    cancelled: bool = False

    @property
    def wrong_byte_deliveries(self) -> int:
        """Blocks handed back whose bytes were not the bytes sent. Must be zero.

        The one result that matters more than any rate: below the cliff a block
        has to be rejected, never delivered corrupted.
        """
        return sum(point.wrong_bytes for point in self.points)

    @property
    def lowest_reliable_snr_db(self) -> float | None:
        """The weakest signal at which every run decoded."""
        reliable = [point.applied_snr_db for point in self.points if point.reliable]
        return min(reliable) if reliable else None

    @property
    def cliff_snr_db(self) -> float | None:
        """The strongest signal at which nothing decoded at all."""
        dead = [point.applied_snr_db for point in self.points if not point.decoded]
        return max(dead) if dead else None


def run_sweep(profile: OfdmProfile, mcs_index: int = 1, *, runs: int = 10,
              seed: int = 0xA5, points=None, on_point=None,
              cancelled=None) -> SweepResult:
    """Decode rate against in-band SNR, one block per run.

    `on_point` is called with each `SweepPoint` as it completes, so a caller can
    show the table filling in rather than waiting for all of it. `cancelled` is
    polled between points; a sweep of eleven points at ten runs is a couple of
    thousand Viterbi decodes and an operator must be able to stop it.
    """
    facts = describe(profile, mcs_index)
    rng = np.random.default_rng(seed)
    payload = rng.integers(0, 256, profile.block_size, dtype=np.uint8).tobytes()
    header = PhyHeader(OfdmFrameType.DATA, 0x0FD, 0, 1, mcs_index, len(payload))
    clean = build_burst(profile, header, payload)
    modulation = mcs(mcs_index).modulation

    collected: list[SweepPoint] = []
    for snr_db in (points if points is not None else SWEEP_POINTS):
        if cancelled is not None and cancelled():
            return SweepResult(facts=facts, runs=runs,
                               points=tuple(collected), cancelled=True)
        decoded = wrong = 0
        measured: list[float] = []
        evms: list[float] = []
        for run in range(runs):
            spec = ChannelSpec(snr_db=float(snr_db), delay=577,
                               trailing=profile.symbol_samples)
            result = decode_burst(profile, Channel(profile, spec,
                                                   seed=seed + run)(clean))
            if result.metrics.snr_db is not None:
                measured.append(result.metrics.snr_db)
            if result.metrics.evm_rms is not None:
                evms.append(result.metrics.evm_rms)
            if result.payload == payload:
                decoded += 1
            elif result.payload is not None:
                wrong += 1
        point = SweepPoint(
            applied_snr_db=float(snr_db),
            runs=runs,
            decoded=decoded,
            wrong_bytes=wrong,
            measured_snr_db=float(np.mean(measured)) if measured else None,
            measured_evm=float(np.mean(evms)) if evms else None,
            uncoded_ber=uncoded_ber(modulation, float(snr_db)),
        )
        collected.append(point)
        if on_point is not None:
            on_point(point)
    return SweepResult(facts=facts, runs=runs, points=tuple(collected))


# -- decoding a real capture ------------------------------------------------- #

@dataclass(frozen=True)
class CaptureResult:
    """What the modem made of a WAV file, whatever produced it."""

    path: Path
    samples: int
    sample_rate: int
    expected_rate: int
    seconds: float
    audio_rms: float
    audio_peak: float
    header: PhyHeader | None
    payload_bytes: int | None
    metrics: LinkMetrics
    error: str = ""

    @property
    def rate_matches(self) -> bool:
        return self.sample_rate == self.expected_rate

    @property
    def passed(self) -> bool:
        return self.metrics.frame_ok and self.rate_matches


def decode_capture(profile: OfdmProfile, path: Path | str) -> CaptureResult:
    """Read a WAV file and try to decode a burst out of it.

    Never raises for a bad file: an unreadable or wrong-rate capture comes back
    with `error` set, because this is reached from a UI where an exception is a
    worse answer than a sentence.
    """
    path = Path(path)
    try:
        samples, rate = read_wav(path)
    except (OSError, ValueError, wave.Error) as exc:
        return CaptureResult(
            path=path, samples=0, sample_rate=0, expected_rate=profile.sample_rate,
            seconds=0.0, audio_rms=0.0, audio_peak=0.0, header=None,
            payload_bytes=None, metrics=LinkMetrics(error=str(exc)), error=str(exc),
        )

    rms = float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0
    peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
    if rate != profile.sample_rate:
        reason = (f"the file is {rate} Hz but profile {profile.name} expects "
                  f"{profile.sample_rate} Hz")
        return CaptureResult(
            path=path, samples=len(samples), sample_rate=rate,
            expected_rate=profile.sample_rate, seconds=len(samples) / max(rate, 1),
            audio_rms=rms, audio_peak=peak, header=None, payload_bytes=None,
            metrics=LinkMetrics(error=reason), error=reason,
        )

    decoded = decode_burst(profile, samples)
    return CaptureResult(
        path=path,
        samples=len(samples),
        sample_rate=rate,
        expected_rate=profile.sample_rate,
        seconds=len(samples) / rate,
        audio_rms=rms,
        audio_peak=peak,
        header=decoded.header,
        payload_bytes=None if decoded.payload is None else len(decoded.payload),
        metrics=decoded.metrics,
        error=decoded.metrics.error or "",
    )


# -- generating a file to transmit ------------------------------------------- #

def make_test_burst(profile: OfdmProfile, mcs_index: int = 1, *,
                    payload_bytes: int = 512, seed: int = 0xA5,
                    repeats: int = 1, gap_seconds: float = 1.0,
                    wav_path: Path | str | None = None) -> tuple[np.ndarray, Path | None]:
    """A clean burst (or several, spaced out) with no channel applied.

    This is the file to transmit on air: it is what the modem would put on the
    sound card, so playing it through a radio and recording the far end measures
    the radio rather than the simulator. Several repeats with a gap between them
    means one transmission produces several independent measurements of the same
    settings, which is worth much more than one.
    """
    rng = np.random.default_rng(seed)
    size = min(int(payload_bytes), profile.block_size)
    parts: list[np.ndarray] = []
    gap = np.zeros(int(max(0.0, gap_seconds) * profile.sample_rate))
    for index in range(max(1, int(repeats))):
        payload = rng.integers(0, 256, size, dtype=np.uint8).tobytes()
        header = PhyHeader(OfdmFrameType.DATA, msg_id=0x0FD, block_seq=index,
                           block_count=max(1, int(repeats)), mcs=mcs_index,
                           payload_len=len(payload))
        if parts:
            parts.append(gap)
        parts.append(build_burst(profile, header, payload))
    waveform = np.concatenate(parts) if parts else np.zeros(0)
    # Lead-in silence so a receiver's squelch has a noise floor to measure before
    # the first burst arrives, which is what its detector needs.
    lead = np.zeros(int(max(gap_seconds, 0.5) * profile.sample_rate))
    waveform = np.concatenate([lead, waveform, gap])
    written = None
    if wav_path is not None:
        written = write_wav(wav_path, waveform, profile.sample_rate)
    return waveform, written
