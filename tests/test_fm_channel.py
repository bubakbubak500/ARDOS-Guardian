from __future__ import annotations

import numpy as np

from guardian.ofdm import BENCH
from guardian.ofdm.fm_channel import FmChannel, FmChannelSpec


def _tone(seconds=0.5):
    n = np.arange(int(BENCH.sample_rate * seconds))
    return 0.4 * np.sin(2 * np.pi * 1200 * n / BENCH.sample_rate)


def test_fm_channel_is_deterministic_and_reports_rf_not_linear_audio():
    spec = FmChannelSpec(rf_snr_db=18.0, rx_agc_rms=None)
    first = FmChannel(BENCH, spec, seed=77)
    second = FmChannel(BENCH, spec, seed=77)
    a = first(_tone())
    b = second(_tone())
    assert np.array_equal(a, b)
    assert first.last_metrics.model == "end-to-end-fm"
    assert first.last_metrics.rf_snr_db == 18.0


def test_clean_fm_round_trip_preserves_in_band_waveform():
    source = _tone()
    channel = FmChannel(BENCH, FmChannelSpec(
        rf_snr_db=80.0, tx_audio_low_hz=100, tx_audio_high_hz=3500,
        rx_audio_low_hz=100, rx_audio_high_hz=3500,
        rx_agc_rms=None, limiter_level=1.0,
    ))
    received = channel(source)
    correlation = np.corrcoef(source[200:], received[200:len(source)])[0, 1]
    assert correlation > 0.995


def test_excess_drive_hits_fm_limiter_and_is_measurable():
    source = _tone() * 4.0
    channel = FmChannel(BENCH, FmChannelSpec(
        rf_snr_db=60.0, limiter_level=0.5, rx_agc_rms=None,
    ))
    channel(source)
    assert channel.last_metrics.limited_samples > 100
    assert channel.last_metrics.limiter_fraction > 0.1
    assert channel.last_metrics.achieved_peak_deviation_hz == 2500.0


def test_rf_multipath_and_sample_clock_are_seed_reproducible():
    spec = FmChannelSpec(
        rf_snr_db=25.0, multipath=((0, 1 + 0j), (13, 0.25 - 0.1j)),
        ppm=35.0,
    )
    channel = FmChannel(BENCH, spec, seed=99)
    first = channel(_tone())
    channel.reset()
    second = channel(_tone())
    assert np.array_equal(first, second)

