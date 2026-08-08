"""Burst acquisition: detection, timing, and carrier frequency offset.

The modem must not assume a laboratory-perfect waveform. Everything here starts
from a buffer where the burst is somewhere in the middle of a noise floor.
"""

from __future__ import annotations

import numpy as np
import pytest

from guardian.ofdm import BENCH, PhyHeader, build_burst, decode_burst
from guardian.ofdm.channel import Channel, ChannelSpec
from guardian.ofdm.framing import OfdmFrameType
from guardian.ofdm.phy import OfdmModulator
from guardian.ofdm.sync import burst_head, detect, repetition_metric

SEED = 0xA5


def _burst(size: int = 256, index: int = 1, seed: int = SEED) -> tuple[bytes, np.ndarray]:
    payload = np.random.default_rng(seed).integers(0, 256, size, dtype=np.uint8).tobytes()
    header = PhyHeader(OfdmFrameType.DATA, msg_id=11, mcs=index,
                       payload_len=len(payload))
    return payload, build_burst(BENCH, header, payload)


def _aired(waveform: np.ndarray, seed: int = 1, **spec) -> np.ndarray:
    spec.setdefault("trailing", 2 * BENCH.symbol_samples)
    return Channel(BENCH, ChannelSpec(**spec), seed=seed)(waveform)


# -- the detector ----------------------------------------------------------- #

def test_the_repetition_metric_peaks_where_the_burst_starts() -> None:
    _, waveform = _burst()
    delay = 3000
    metric, _, _energy = repetition_metric(_aired(waveform, delay=delay, snr_db=20.0), BENCH)
    # The plateau covers the cyclic prefix ahead of the first symbol body.
    assert metric[delay: delay + BENCH.cp_length].min() > 0.8
    # Well before the burst there is nothing but noise.
    assert metric[: delay - BENCH.fft_size].max() < 0.5


def test_the_metric_is_a_correlation_coefficient_and_stays_bounded() -> None:
    # It used to divide by the second window's energy alone, which is unbounded
    # at the trailing edge of a burst: the metric reached 45 there and the
    # detector locked onto the end of every strong burst instead of the start.
    _, waveform = _burst()
    metric, _, _energy = repetition_metric(_aired(waveform, delay=2000, snr_db=30.0), BENCH)
    assert metric.max() <= 1.0 + 1e-9
    assert metric.argmax() < 2000 + 2 * BENCH.symbol_samples


def test_nothing_is_detected_in_noise_or_silence() -> None:
    rng = np.random.default_rng(SEED)
    assert detect(rng.normal(0.0, 0.2, 3 * BENCH.sample_rate), BENCH) is None
    assert detect(np.zeros(3 * BENCH.sample_rate), BENCH) is None
    assert detect(np.zeros(10), BENCH) is None


def test_the_burst_head_is_the_deterministic_prefix_of_every_burst() -> None:
    modulator = OfdmModulator(BENCH)
    head = burst_head(BENCH, modulator)
    _, waveform = _burst()
    assert len(head) == (BENCH.preamble_symbols + BENCH.training_symbols) * BENCH.symbol_samples
    # It is literally the start of the burst, up to the level scaling that
    # `burst()` applies to the whole waveform at once.
    scale = float(np.dot(waveform[: len(head)], head) / np.dot(head, head))
    assert np.allclose(waveform[: len(head)], head * scale, atol=1e-12)


# -- required test 6: arbitrary delay --------------------------------------- #

@pytest.mark.parametrize("delay", [0, 1, 7, 1153, 4321, 48000])
def test_a_burst_is_found_to_the_sample_at_any_delay(delay: int) -> None:
    payload, waveform = _burst()
    result = detect(_aired(waveform, delay=delay, snr_db=25.0), BENCH)

    assert result is not None
    assert abs(result.burst_start - delay) <= 2, (
        f"located at {result.burst_start}, burst really starts at {delay}"
    )
    assert result.confidence > 0.8
    assert result.audio_rms > 0.0


@pytest.mark.parametrize("delay", [0, 1, 7, 1153, 4321, 48000])
def test_the_payload_survives_any_delay(delay: int) -> None:
    payload, waveform = _burst()
    decoded = decode_burst(BENCH, _aired(waveform, delay=delay, snr_db=25.0))
    assert decoded.payload == payload


def test_timing_lands_inside_the_guard_so_a_small_error_costs_nothing() -> None:
    # A timing error inside the cyclic prefix is only a phase ramp across the
    # carriers, which the pilot slope fit removes. That is what the guard is for.
    payload, waveform = _burst()
    for offset in (-5, -2, 0, 2, 5):
        aired = _aired(waveform, delay=4000, snr_db=30.0)
        shifted = np.concatenate([np.zeros(max(0, offset)), aired])[max(0, -offset):]
        assert decode_burst(BENCH, shifted).payload == payload


def test_a_second_burst_in_the_buffer_is_ignored_not_mixed_in() -> None:
    # Only the first burst is reported; the sender is stop-and-wait, so a second
    # one in the same buffer belongs to the next exchange.
    payload, waveform = _burst()
    gap = np.zeros(BENCH.sample_rate // 2)
    stream = np.concatenate([np.zeros(2000), waveform, gap, waveform])
    result = detect(stream, BENCH)
    assert result is not None
    assert abs(result.burst_start - 2000) <= 2
    assert decode_burst(BENCH, stream).payload == payload


# -- required test 8: carrier frequency offset ------------------------------ #

@pytest.mark.parametrize("offset_hz", [-10.0, -2.0, -0.5, 0.0, 0.5, 2.0, 10.0])
def test_a_small_frequency_offset_is_measured_and_removed(offset_hz: float) -> None:
    payload, waveform = _burst()
    aired = _aired(waveform, delay=1500, snr_db=15.0, freq_offset_hz=offset_hz)
    decoded = decode_burst(BENCH, aired)

    assert decoded.payload == payload
    assert decoded.metrics.cfo_hz == pytest.approx(offset_hz, abs=1.0)


def test_the_offset_estimate_is_precise_on_a_clean_channel() -> None:
    # Two stages: the preamble plateau is unambiguous across a whole subcarrier
    # spacing but coarse, and the training symbols then measure the remainder in
    # the bin domain where nothing biases it.
    payload, waveform = _burst()
    for offset_hz in (-8.0, -1.0, 3.5, 9.0):
        aired = _aired(waveform, delay=1200, freq_offset_hz=offset_hz)
        result = detect(aired, BENCH)
        assert result is not None
        assert result.cfo_hz == pytest.approx(offset_hz, abs=0.05)


def test_a_clean_channel_reports_essentially_no_offset_and_no_noise() -> None:
    # An artificial residual here shows up as a fake noise floor everywhere
    # downstream: a leftover 0.01 Hz was worth an apparent 55 dB SNR ceiling.
    payload, waveform = _burst(size=BENCH.block_size)
    decoded = decode_burst(BENCH, waveform)
    assert abs(decoded.metrics.cfo_hz) < 0.01
    assert decoded.metrics.snr_db > 60.0


def test_a_fixed_phase_rotation_is_absorbed_by_the_channel_estimate() -> None:
    payload, waveform = _burst()
    for phase in (0.0, 0.8, 2.5, -3.0):
        aired = _aired(waveform, delay=900, snr_db=20.0, phase_offset=phase)
        assert decode_burst(BENCH, aired).payload == payload


# -- gain, clipping, clock -------------------------------------------------- #

@pytest.mark.parametrize("gain", [0.02, 0.3, 1.0, 3.0])
def test_the_receiver_is_indifferent_to_level(gain: float) -> None:
    # Equalisation divides by the measured channel, so an overall gain cancels.
    # A radio's audio level must not be something the operator has to tune.
    payload, waveform = _burst()
    aired = _aired(waveform, delay=700, snr_db=20.0, gain=gain)
    decoded = decode_burst(BENCH, aired)
    assert decoded.payload == payload
    assert decoded.metrics.snr_db == pytest.approx(20.0, abs=1.5)


def test_a_clipped_input_stage_still_decodes() -> None:
    payload, waveform = _burst()
    aired = _aired(waveform, delay=800, snr_db=20.0, clip_ratio=0.45)
    decoded = decode_burst(BENCH, aired)
    assert decoded.payload == payload
    # Clipping is a real impairment; the crest factor is how it shows up.
    assert decoded.metrics.crest_factor_db < 12.0


@pytest.mark.parametrize("ppm", [-20.0, -5.0, 5.0, 20.0])
def test_a_soundcard_clock_offset_shows_up_as_a_pilot_phase_slope(ppm: float) -> None:
    # Two stations never share a clock. The slope of the pilot phases across the
    # band is what a sample-rate difference looks like, and tracking it is what
    # makes two independent soundcards workable.
    payload, waveform = _burst()
    aired = _aired(waveform, delay=1000, snr_db=20.0, ppm=ppm)
    decoded = decode_burst(BENCH, aired)

    assert decoded.payload == payload
    assert decoded.metrics.phase_slope is not None
    assert np.isfinite(decoded.metrics.phase_slope)


def test_everything_at_once_still_decodes() -> None:
    payload, waveform = _burst(size=BENCH.block_size)
    aired = _aired(waveform, seed=7, snr_db=15.0, gain=0.6, delay=4097,
                   freq_offset_hz=3.0, phase_offset=1.2, clip_ratio=0.6,
                   multipath=((0, 1.0), (48, 0.35 + 0.15j)), ppm=8.0)
    decoded = decode_burst(BENCH, aired)

    assert decoded.payload == payload
    assert decoded.metrics.snr_db == pytest.approx(15.0, abs=2.0)
    assert decoded.metrics.cfo_hz == pytest.approx(3.0, abs=1.0)
