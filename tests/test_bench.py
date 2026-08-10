"""The measurement engine behind both the app's Modem test and the CLI.

One implementation, two front ends: whatever these tests pin is what an operator
reads on screen and what a developer reads in a terminal, which is the only
arrangement in which the two cannot drift apart.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from guardian.ofdm import bench
from guardian.ofdm.channel import ChannelSpec
from guardian.ofdm.config import (BENCH, DEFAULT_PROFILE_NAME, MCS_TABLE,
                                  PROFILE_LADDER, PROFILES, OfdmConfigError,
                                  profile, profile_names, profile_or_default)

SEED = 0xA5
FAST = (20.0, 8.0, 4.0)      # enough points to show a cliff without a long run


# -- the profile ladder ------------------------------------------------------ #

def test_the_ladder_is_ordered_by_bandwidth_not_by_name() -> None:
    # Alphabetical order would put NARROW between the WIDEs and read as nonsense
    # in a picker; ascending bandwidth is also the order to try them on air.
    widths = [profile(name).occupied_bandwidth for name in PROFILE_LADDER]
    assert widths == sorted(widths)
    assert list(PROFILE_LADDER)[:2] == ["NARROW_1K2", "BENCH"]
    assert PROFILE_LADDER[-1] == "WIDE_40K"
    # profile_names() still exists and still sorts alphabetically.
    assert profile_names() == sorted(PROFILES)


def test_every_rung_shares_one_spacing_and_one_guard() -> None:
    # The point of the ladder: moving up changes the bandwidth and nothing else,
    # so frequency-offset tolerance (set by the spacing) and multipath tolerance
    # (set by the guard) are the same on every rung.
    spacings = {profile(name).subcarrier_spacing for name in PROFILE_LADDER}
    guards = {round(profile(name).cp_duration, 9) for name in PROFILE_LADDER}
    assert spacings == {46.875}
    assert guards == {round(128 / 48000, 9)}


def test_the_ladder_roughly_doubles_at_each_step() -> None:
    widths = [profile(name).occupied_bandwidth for name in PROFILE_LADDER]
    for narrow, wide in zip(widths, widths[1:]):
        assert 1.8 < wide / narrow < 2.3


def test_bench_remains_the_default_and_is_untouched() -> None:
    # The whole test suite is written against BENCH's numbers, so it is the one
    # rung to leave alone.
    assert DEFAULT_PROFILE_NAME == "BENCH"
    assert profile("BENCH") is BENCH
    assert BENCH.num_carriers == 52
    assert BENCH.occupied_bandwidth == pytest.approx(2437.5)


def test_the_wide_rung_that_needs_a_faster_sound_card_says_so_in_its_rate() -> None:
    # Past 24 kHz of audio the card has to run faster; that is a real
    # prerequisite an operator can check before trying it.
    assert profile("WIDE_20K").sample_rate == 48000
    assert profile("WIDE_40K").sample_rate == 96000
    assert profile("WIDE_20K").occupied_bandwidth < 24000
    assert profile("WIDE_40K").occupied_bandwidth > 24000


def test_an_unknown_profile_raises_but_the_forgiving_lookup_falls_back() -> None:
    with pytest.raises(OfdmConfigError):
        profile("WIDE_500K")
    assert profile_or_default("WIDE_500K") is BENCH
    assert profile_or_default("WIDE_10K") is profile("WIDE_10K")


# -- describing a waveform --------------------------------------------------- #

def test_the_facts_are_all_derived_from_the_profile() -> None:
    facts = bench.describe(BENCH, 1)
    assert facts.profile == "BENCH"
    assert facts.sample_rate == 48000
    assert facts.fft_size == 1024
    assert facts.cp_length == 128
    assert facts.cp_ms == pytest.approx(2.667, abs=0.01)
    assert facts.subcarrier_spacing == pytest.approx(46.875)
    assert facts.symbol_ms == pytest.approx(24.0)
    assert (facts.carriers, facts.data_carriers, facts.pilots) == (52, 44, 8)
    assert facts.occupied == pytest.approx(2437.5)
    assert facts.mcs_label == "MCS1 QPSK r=1/2"
    assert facts.coded_bits_per_symbol == 88
    assert facts.information_bits_per_symbol == 44
    assert facts.phy_rate == pytest.approx(1833.0, abs=1.0)
    assert facts.header_symbols == 7
    assert facts.full_block_seconds == pytest.approx(2.592, abs=0.01)
    assert facts.fec_label == "1/2"
    assert facts.raw_data_bps == pytest.approx(3667.0, abs=1.0)
    assert facts.effective_fec_rate < 0.5  # CRC and trellis termination are real overhead.
    assert facts.band == "539-2977 Hz"


@pytest.mark.parametrize("index", [entry.index for entry in MCS_TABLE])
def test_a_denser_mcs_carries_more_per_symbol_and_finishes_sooner(index: int) -> None:
    facts = bench.describe(BENCH, index)
    reference = bench.describe(BENCH, 0)
    assert facts.coded_bits_per_symbol >= reference.coded_bits_per_symbol
    assert facts.full_block_seconds <= reference.full_block_seconds
    assert facts.phy_rate >= reference.phy_rate


def test_a_wider_profile_is_faster_and_shorter() -> None:
    narrow = bench.describe(profile("BENCH"), 1)
    wide = bench.describe(profile("WIDE_20K"), 1)
    assert wide.occupied > 7 * narrow.occupied
    assert wide.phy_rate > 7 * narrow.phy_rate
    assert wide.full_block_seconds < narrow.full_block_seconds
    # A wide band needs fewer symbols for the header, which is pure overhead.
    assert wide.header_symbols < narrow.header_symbols


# -- one burst --------------------------------------------------------------- #

def test_a_burst_reports_measured_against_applied() -> None:
    result = bench.run_burst(BENCH, 1, payload_bytes=256, snr_db=18.0, seed=SEED)
    assert result.passed and result.identical
    assert result.applied_snr_db == 18.0
    assert result.metrics.snr_db == pytest.approx(18.0, abs=2.0)
    assert 0.0 < result.metrics.evm_rms < 1.0
    assert result.payload_bytes == 256
    assert result.seconds > 0.0
    assert 6.0 < result.tx_crest_db < 20.0
    assert result.tx_rms == pytest.approx(BENCH.tx_rms, rel=0.01)
    assert result.channel_spread_db is not None
    assert "SNR 18 dB in-band" in result.channel


def test_the_wideband_offset_is_reported_so_the_snr_cannot_be_misread() -> None:
    # A stated in-band SNR fills the whole audio band with noise at that density,
    # so quoting the wideband figure would flatter the modem by about 10 dB.
    result = bench.run_burst(BENCH, 1, payload_bytes=64, snr_db=15.0, seed=SEED)
    assert result.wideband_offset_db == pytest.approx(
        10 * math.log10(BENCH.bandwidth_fraction), abs=0.01)
    assert result.wideband_offset_db < -9.0


def test_a_payload_larger_than_a_block_is_clamped_not_refused() -> None:
    result = bench.run_burst(BENCH, 1, payload_bytes=99_999, snr_db=20.0, seed=SEED)
    assert result.payload_bytes == BENCH.block_size
    assert result.passed


def test_a_burst_can_be_pushed_through_a_named_channel() -> None:
    spec = ChannelSpec(snr_db=25.0, delay=1000, trailing=2000)
    result = bench.run_burst(BENCH, 1, payload_bytes=128, seed=SEED, spec=spec)
    assert result.passed
    assert result.applied_snr_db == 25.0
    assert "delay 1000 samples" in result.channel


def test_a_burst_below_the_cliff_fails_without_delivering_bytes() -> None:
    result = bench.run_burst(BENCH, 3, payload_bytes=512, snr_db=2.0, seed=SEED)
    assert not result.passed
    assert not result.identical


def test_a_burst_can_be_written_out_and_read_back(tmp_path) -> None:
    path = tmp_path / "burst.wav"
    result = bench.run_burst(BENCH, 1, payload_bytes=256, snr_db=20.0, seed=SEED,
                             wav_path=path)
    assert result.wav_path == path
    samples, rate = bench.read_wav(path)
    assert rate == BENCH.sample_rate
    assert len(samples) > result.samples          # the channel added lead and tail
    assert bench.decode_capture(BENCH, path).passed


@pytest.mark.parametrize("name", ["NARROW_1K2", "BENCH", "WIDE_5K", "WIDE_10K",
                                  "WIDE_20K", "WIDE_40K"])
def test_every_rung_carries_a_block_through_the_simulator(name: str) -> None:
    # The claim the ladder rests on: widening is a profile entry, not a code
    # change, so every rung has to work with no special-casing anywhere.
    result = bench.run_burst(profile(name), 1, payload_bytes=256, snr_db=20.0,
                             seed=SEED)
    assert result.passed, f"{name}: {result.metrics.error}"
    assert result.metrics.snr_db == pytest.approx(20.0, abs=2.5)


# -- a whole transfer -------------------------------------------------------- #

def test_a_transfer_reports_blocks_retries_and_measured_throughput() -> None:
    result = bench.run_transfer(BENCH, 1, payload_bytes=1024, snr_db=15.0,
                                seed=SEED, ptt_turnaround=0.0)
    assert result.passed
    assert result.blocks == 2
    assert result.blocks_acked == 2
    assert result.retries == 0
    assert result.packet_error_rate == 0.0
    assert result.measured_snr_db == pytest.approx(15.0, abs=2.0)
    assert result.channel_seconds > 0.0
    assert result.protocol_overhead_bytes > 0
    # Below the raw PHY rate, because acknowledgements and overhead are counted.
    assert 0 < result.throughput_bps < bench.describe(BENCH, 1).phy_rate


def test_a_transfer_streams_its_log_as_it_goes() -> None:
    lines = []
    result = bench.run_transfer(BENCH, 1, payload_bytes=600, snr_db=18.0,
                                seed=SEED, ptt_turnaround=0.0, on_log=lines.append)
    assert lines == list(result.log)
    assert any("sending" in line for line in lines)
    assert any(line.startswith("tx | ") for line in lines)
    assert any(line.startswith("rx | ") for line in lines)
    assert any("remote_snr=" in line and "remote_evm=" in line for line in lines)
    assert any("protocol_overhead=" in line and "goodput=" in line
               for line in lines)
    assert "\n".join(lines).isascii()


# -- the sweep --------------------------------------------------------------- #

def test_a_sweep_finds_the_cliff_and_never_delivers_wrong_bytes() -> None:
    result = bench.run_sweep(BENCH, 1, runs=3, seed=SEED, points=FAST)
    assert [point.applied_snr_db for point in result.points] == list(FAST)
    assert result.points[0].reliable                     # 20 dB is comfortable
    assert not result.points[-1].decoded                 # 4 dB is past the cliff
    assert result.lowest_reliable_snr_db == 8.0
    assert result.cliff_snr_db == 4.0
    assert result.wrong_byte_deliveries == 0
    assert not result.cancelled
    for point in result.points:
        assert point.runs == 3
        assert point.measured_snr_db == pytest.approx(point.applied_snr_db, abs=1.5)


def test_a_sweep_reports_each_point_as_it_completes() -> None:
    seen = []
    result = bench.run_sweep(BENCH, 1, runs=1, seed=SEED, points=FAST,
                             on_point=seen.append)
    assert seen == list(result.points)
    assert len(seen) == len(FAST)


def test_a_sweep_stops_when_it_is_cancelled() -> None:
    # Eleven points at ten runs is a couple of thousand Viterbi decodes; an
    # operator has to be able to stop it, and stopping must keep what it found.
    seen = []

    def stop_after_one() -> bool:
        return len(seen) >= 1

    result = bench.run_sweep(BENCH, 1, runs=1, seed=SEED, points=FAST,
                             on_point=seen.append, cancelled=stop_after_one)
    assert result.cancelled
    assert len(result.points) == 1
    assert result.points[0].applied_snr_db == FAST[0]


def test_a_sweep_cancelled_before_it_starts_returns_nothing() -> None:
    result = bench.run_sweep(BENCH, 1, runs=1, points=FAST, cancelled=lambda: True)
    assert result.cancelled
    assert result.points == ()
    assert result.wrong_byte_deliveries == 0
    assert result.lowest_reliable_snr_db is None
    assert result.cliff_snr_db is None


def test_the_default_sweep_points_straddle_every_mcs_cliff() -> None:
    # Measured cliffs: 4 dB BPSK, 7 QPSK, 12 16-QAM, 18 64-QAM. Every one has to
    # fall inside the table or the sweep would show a flat column of passes.
    assert min(bench.SWEEP_POINTS) <= 2.0
    assert max(bench.SWEEP_POINTS) >= 24.0
    assert bench.SWEEP_POINTS == tuple(sorted(bench.SWEEP_POINTS, reverse=True))


# -- theory anchor ----------------------------------------------------------- #

@pytest.mark.parametrize("modulation", ["bpsk", "qpsk", "qam16", "qam64"])
def test_the_theory_curve_falls_as_the_signal_improves(modulation: str) -> None:
    values = [bench.uncoded_ber(modulation, snr) for snr in (2, 8, 16, 24)]
    assert values == sorted(values, reverse=True)
    assert 0.0 < values[-1] < values[0] < 0.5


def test_the_theory_curve_matches_the_textbook_qpsk_figure() -> None:
    # QPSK at 8 dB Es/N0 is 5 dB Eb/N0, i.e. Q(sqrt(2*10^0.5)) = 6.0e-3.
    assert bench.uncoded_ber("qpsk", 8.0) == pytest.approx(6.0e-3, rel=0.05)


# -- WAV, and the file to put on air ----------------------------------------- #

def test_a_written_wav_is_mono_sixteen_bit_at_the_profile_rate(tmp_path) -> None:
    path = bench.write_wav(tmp_path / "x.wav", np.zeros(500), 96000)
    samples, rate = bench.read_wav(path)
    assert rate == 96000
    assert len(samples) == 500


def test_reading_a_wav_that_is_not_sixteen_bit_is_refused(tmp_path) -> None:
    import wave
    path = tmp_path / "eight.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(1)
        handle.setframerate(48000)
        handle.writeframes(b"\x80" * 100)
    with pytest.raises(ValueError, match="16-bit"):
        bench.read_wav(path)


def test_the_transmit_file_has_no_channel_applied(tmp_path) -> None:
    # This is the file that goes through a radio, so it must be exactly what the
    # modem would put on the sound card -- anything else would measure the
    # simulator instead of the radio.
    path = tmp_path / "tx.wav"
    samples, written = bench.make_test_burst(BENCH, 1, payload_bytes=256,
                                             seed=SEED, repeats=1, wav_path=path)
    assert written == path
    result = bench.decode_capture(BENCH, path)
    assert result.passed
    # A clean burst measures as essentially noiseless.
    assert result.metrics.snr_db > 60.0
    assert result.metrics.evm_rms < 0.01


def test_the_transmit_file_repeats_the_burst_with_gaps_and_a_lead_in(tmp_path) -> None:
    # Several bursts from one transmission means several independent measurements
    # of the same settings, and the lead-in gives a receiver's squelch a noise
    # floor to measure before the first one arrives.
    single, _ = bench.make_test_burst(BENCH, 1, repeats=1, gap_seconds=1.0)
    triple, path = bench.make_test_burst(BENCH, 1, repeats=3, gap_seconds=1.0,
                                         wav_path=tmp_path / "three.wav")
    lead = int(1.0 * BENCH.sample_rate)
    assert not np.any(single[:lead]), "no lead-in silence"

    # Three bursts plus the gaps between them, plus lead-in and tail.
    one_burst = len(single) - 2 * lead
    assert len(triple) == pytest.approx(3 * one_burst + 4 * lead, abs=lead)
    # Only the first burst is reported, and it is block 1 of 3.
    decoded = bench.decode_capture(BENCH, path)
    assert decoded.passed
    assert decoded.header.block_seq == 0
    assert decoded.header.block_count == 3


@pytest.mark.parametrize("name", ["BENCH", "WIDE_10K", "WIDE_40K"])
def test_a_transmit_file_decodes_on_the_profile_that_made_it(tmp_path,
                                                            name: str) -> None:
    prof = profile(name)
    path = tmp_path / f"{name}.wav"
    bench.make_test_burst(prof, 1, payload_bytes=256, seed=SEED, repeats=1,
                          wav_path=path)
    assert bench.decode_capture(prof, path).passed


# -- decoding a capture ------------------------------------------------------ #

def test_a_capture_at_the_wrong_rate_says_so_rather_than_decoding_noise(tmp_path) -> None:
    # The commonest way a session is wasted: a 96 kHz profile recorded at 48 kHz.
    path = tmp_path / "wrong.wav"
    bench.make_test_burst(profile("WIDE_40K"), 1, payload_bytes=128, seed=SEED,
                          repeats=1, wav_path=path)
    result = bench.decode_capture(BENCH, path)

    assert not result.passed
    assert not result.rate_matches
    assert result.sample_rate == 96000
    assert result.expected_rate == 48000
    assert "96000" in result.error and "48000" in result.error
    assert result.header is None


def test_a_missing_file_is_reported_not_raised(tmp_path) -> None:
    result = bench.decode_capture(BENCH, tmp_path / "nope.wav")
    assert not result.passed
    assert result.error
    assert result.samples == 0


def test_a_capture_of_noise_yields_no_payload(tmp_path) -> None:
    rng = np.random.default_rng(SEED)
    path = bench.write_wav(tmp_path / "noise.wav",
                           rng.normal(0.0, 0.05, 2 * BENCH.sample_rate),
                           BENCH.sample_rate)
    result = bench.decode_capture(BENCH, path)
    assert not result.passed
    assert result.payload_bytes is None
    assert result.audio_rms == pytest.approx(0.05, rel=0.1)


def test_a_capture_reports_its_level_so_a_bad_recording_is_obvious(tmp_path) -> None:
    path = bench.write_wav(tmp_path / "quiet.wav", np.full(48_000, 0.002),
                           BENCH.sample_rate)
    result = bench.decode_capture(BENCH, path)
    assert result.audio_peak == pytest.approx(0.002, abs=1e-4)
    assert result.seconds == pytest.approx(1.0, abs=0.01)
