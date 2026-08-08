"""The channel simulator, and what the modem survives on it.

These are the tests that stand in for RF until there are two radios: noise at a
stated SNR, a frequency-selective echo, a notch across three carriers, and the
one behaviour that matters more than any of them -- a corrupted burst is
rejected rather than delivered.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from guardian.ofdm import BENCH, PhyHeader, build_burst, decode_burst, mcs
from guardian.ofdm.channel import Channel, ChannelSpec, ideal, realistic
from guardian.ofdm.constellation import bits_per_symbol
from guardian.ofdm.framing import OfdmFrameType

SEED = 0xA5


def _payload(size: int = BENCH.block_size, seed: int = SEED) -> bytes:
    return np.random.default_rng(seed).integers(0, 256, size, dtype=np.uint8).tobytes()


def _burst(payload: bytes, index: int = 1) -> np.ndarray:
    header = PhyHeader(OfdmFrameType.DATA, msg_id=21, mcs=index,
                       payload_len=len(payload))
    return build_burst(BENCH, header, payload)


def _q(x: float) -> float:
    return 0.5 * math.erfc(x / math.sqrt(2.0))


# -- the simulator itself --------------------------------------------------- #

def test_the_same_spec_and_seed_produce_identical_samples() -> None:
    waveform = _burst(_payload(64))
    spec = ChannelSpec(snr_db=10.0, delay=321, freq_offset_hz=3.0, ppm=4.0,
                       multipath=((0, 1.0), (48, 0.4)), clip_ratio=0.7)
    first = Channel(BENCH, spec, seed=7)(waveform)
    second = Channel(BENCH, spec, seed=7)(waveform)
    assert np.array_equal(first, second)
    # A different seed must actually change the noise, or the sweeps are a lie.
    assert not np.array_equal(first, Channel(BENCH, spec, seed=8)(waveform))


def test_resetting_a_channel_replays_the_same_noise() -> None:
    waveform = _burst(_payload(64))
    channel = Channel(BENCH, ChannelSpec(snr_db=10.0), seed=3)
    first = channel(waveform)
    assert not np.array_equal(channel(waveform), first)
    channel.reset()
    assert np.array_equal(channel(waveform), first)


def test_an_ideal_channel_returns_the_waveform_untouched() -> None:
    waveform = _burst(_payload(64))
    assert np.array_equal(ideal(BENCH)(waveform), waveform)


def test_snr_is_specified_in_band_and_that_is_what_the_receiver_measures() -> None:
    # The definition that keeps the modem honest. Noise fills the whole 24 kHz
    # audio band; the demodulator only ever sees the tenth of it the carriers
    # occupy, so a wideband figure would flatter the modem by about 10 dB.
    waveform = _burst(_payload())
    for applied in (6.0, 10.0, 15.0, 20.0, 25.0):
        aired = Channel(BENCH, ChannelSpec(snr_db=applied, delay=500,
                                           trailing=2000), seed=11)(waveform)
        measured = decode_burst(BENCH, aired).metrics.snr_db
        assert measured == pytest.approx(applied, abs=1.0)


def test_the_wideband_snr_of_the_audio_is_about_ten_db_worse() -> None:
    # Stated explicitly so nobody quotes the wrong one later.
    waveform = _burst(_payload())
    quiet = Channel(BENCH, ChannelSpec(snr_db=10.0), seed=11)
    aired = quiet(waveform)
    wideband = 10 * math.log10(np.var(waveform) / np.var(aired - waveform))
    assert wideband == pytest.approx(10.0 + 10 * math.log10(BENCH.bandwidth_fraction),
                                     abs=1.0)


def test_the_spec_describes_itself_for_the_log() -> None:
    assert ChannelSpec().describe() == "ideal"
    described = ChannelSpec(snr_db=8.0, delay=100, freq_offset_hz=-2.0,
                            ppm=5.0).describe()
    assert "SNR 8 dB in-band" in described
    assert "delay 100 samples" in described
    assert "CFO -2 Hz" in described
    assert "clock +5 ppm" in described


# -- required test 7: moderate AWGN at the robust MCS ----------------------- #

def test_twenty_seeded_runs_at_eight_db_decode_and_never_lie() -> None:
    payload = _payload()
    waveform = _burst(payload, index=1)
    good = 0
    wrong = 0
    measured = []
    for seed in range(20):
        spec = ChannelSpec(snr_db=8.0, delay=1000, trailing=2000)
        decoded = decode_burst(BENCH, Channel(BENCH, spec, seed=seed)(waveform))
        if decoded.metrics.snr_db is not None:
            measured.append(decoded.metrics.snr_db)
        if decoded.payload == payload:
            good += 1
        elif decoded.payload is not None:
            wrong += 1

    assert good >= 19, f"only {good}/20 blocks decoded at 8 dB"
    assert wrong == 0, "a block was delivered whose bytes were not what was sent"
    assert float(np.mean(measured)) == pytest.approx(8.0, abs=2.0)


@pytest.mark.parametrize("snr_db,minimum", [(20.0, 10), (14.0, 10), (10.0, 10),
                                            (8.0, 10), (6.0, 8)])
def test_the_decode_rate_holds_up_down_to_six_db(snr_db: float, minimum: int) -> None:
    payload = _payload(256)
    waveform = _burst(payload)
    good = sum(
        decode_burst(BENCH,
                     Channel(BENCH, ChannelSpec(snr_db=snr_db, delay=600,
                                                trailing=2000), seed=seed)(waveform)
                     ).payload == payload
        for seed in range(10)
    )
    assert good >= minimum


def test_the_coded_link_beats_the_uncoded_theory_curve() -> None:
    # A sanity check on the whole chain's normalisation rather than on the code:
    # at 8 dB per carrier, uncoded QPSK would put a bit error every 170 bits, so a
    # 4112-bit block would essentially never survive. The code turns that into a
    # block that always survives. If this failed, something would be scaled wrong
    # long before any radio was involved.
    es_over_n0 = 10.0 ** 0.8
    uncoded = _q(math.sqrt(2.0 * es_over_n0 / bits_per_symbol("qpsk")))
    assert uncoded > 1e-3
    assert (1.0 - uncoded) ** (BENCH.block_size * 8) < 1e-9

    payload = _payload()
    waveform = _burst(payload)
    spec = ChannelSpec(snr_db=8.0, delay=400, trailing=2000)
    assert decode_burst(BENCH, Channel(BENCH, spec, seed=2)(waveform)).payload == payload


# -- required test 9: multipath -------------------------------------------- #

def test_a_one_millisecond_echo_is_equalised_away() -> None:
    payload = _payload()
    waveform = _burst(payload)
    one_ms = int(BENCH.sample_rate / 1000)
    assert one_ms < BENCH.cp_length, "the guard has to cover the echo"

    spec = ChannelSpec(snr_db=15.0, delay=500, trailing=3000,
                       multipath=((0, 1.0), (one_ms, 0.4)))
    decoded = decode_burst(BENCH, Channel(BENCH, spec, seed=4)(waveform))

    assert decoded.payload == payload
    # The echo is visible in the channel estimate as ripple across the band --
    # a flat channel would not show this, so the measurement is real.
    response = np.abs(decoded.metrics.channel_response)
    assert response.max() / response.min() > 1.5


@pytest.mark.parametrize("gain", [0.2, 0.4, 0.6])
def test_stronger_echoes_deepen_the_ripple_and_still_decode(gain: float) -> None:
    payload = _payload(256)
    waveform = _burst(payload)
    spec = ChannelSpec(snr_db=18.0, delay=500, trailing=3000,
                       multipath=((0, 1.0), (72, gain)))
    decoded = decode_burst(BENCH, Channel(BENCH, spec, seed=5)(waveform))
    assert decoded.payload == payload
    response = np.abs(decoded.metrics.channel_response)
    assert response.max() / response.min() > 1.0 + gain


def test_an_echo_well_past_the_guard_is_where_this_stops_working() -> None:
    # Honest about the limit. The cyclic prefix is 2.67 ms, and an echo past it is
    # inter-symbol interference that no per-carrier equaliser can undo. Measured at
    # a strong 0.8 echo and 25 dB: everything up to the full guard decodes, twice
    # the guard never does. (Three times the guard happens to decode again, which
    # is geometry rather than robustness -- it is not something to rely on.)
    # What must hold either way is that failure means rejection, not wrong bytes.
    payload = _payload(256)
    waveform = _burst(payload)

    def decode_rate(echo_delay: int) -> int:
        delivered = 0
        for seed in range(8):
            spec = ChannelSpec(snr_db=25.0, delay=500, trailing=8000,
                               multipath=((0, 1.0), (echo_delay, 0.8)))
            decoded = decode_burst(BENCH, Channel(BENCH, spec, seed=seed)(waveform))
            if decoded.payload is not None:
                # However bad the channel gets, the bytes are either right or absent.
                assert decoded.payload == payload
                delivered += 1
        return delivered

    assert decode_rate(BENCH.cp_length) == 8            # inside the guard: reliable
    assert decode_rate(2 * BENCH.cp_length) <= 2        # outside it: collapses


# -- required test 10: a narrow notch -------------------------------------- #

def test_three_carriers_lost_to_a_notch_are_recovered_by_the_code() -> None:
    # The interleaver's reason to exist. Those three carriers are wrong in every
    # single OFDM symbol of the burst; spread across the coded stream they become
    # isolated errors the K=7 code absorbs.
    payload = _payload()
    waveform = _burst(payload)
    spacing = BENCH.subcarrier_spacing
    first = BENCH.first_carrier + 20
    low = (first - 0.5) * spacing
    high = (first + 2.5) * spacing

    spec = ChannelSpec(snr_db=15.0, delay=500, trailing=3000,
                       notch=(low, high, -30.0))
    decoded = decode_burst(BENCH, Channel(BENCH, spec, seed=8)(waveform))

    assert decoded.payload == payload
    # The notch is measurable in the channel estimate, so the receiver knows
    # which carriers are bad -- which is what a bit-loading map would read.
    response = np.abs(decoded.metrics.channel_response)
    notched = response[20:23]
    assert notched.mean() < 0.25 * float(np.median(response))


def test_a_notch_wide_enough_to_matter_eventually_wins() -> None:
    # Twelve of 52 carriers gone is about a quarter of the band, past what a
    # rate-1/2 code can make up. The limit is recorded, not hidden.
    payload = _payload()
    waveform = _burst(payload)
    spacing = BENCH.subcarrier_spacing
    low = (BENCH.first_carrier + 15 - 0.5) * spacing
    high = (BENCH.first_carrier + 27 + 0.5) * spacing
    spec = ChannelSpec(snr_db=12.0, delay=500, trailing=3000,
                       notch=(low, high, -40.0))
    decoded = decode_burst(BENCH, Channel(BENCH, spec, seed=9)(waveform))
    assert decoded.payload != payload
    assert decoded.payload is None      # rejected, not silently wrong


def test_the_per_carrier_noise_estimate_is_what_saves_the_notch_case() -> None:
    # Without per-carrier weighting the decoder would trust a notched carrier's
    # confident nonsense as much as a good carrier's truth. Compare the equalised
    # noise on the notched carriers against the rest.
    payload = _payload(256)
    waveform = _burst(payload)
    spacing = BENCH.subcarrier_spacing
    low = (BENCH.first_carrier + 20 - 0.5) * spacing
    high = (BENCH.first_carrier + 22 + 0.5) * spacing
    spec = ChannelSpec(snr_db=15.0, delay=500, trailing=3000,
                       notch=(low, high, -30.0))
    decoded = decode_burst(BENCH, Channel(BENCH, spec, seed=8)(waveform))

    gain = np.abs(decoded.metrics.channel_response) ** 2
    # Zero-forcing divides by the channel, so noise on those carriers is
    # amplified by the same factor the signal was attenuated by.
    assert float(np.median(gain)) / gain[20:23].mean() > 10.0


# -- required test 11: corruption is rejected, never delivered -------------- #

def test_a_corrupted_payload_is_rejected_by_its_crc() -> None:
    payload = _payload()
    waveform = _burst(payload)
    aired = Channel(BENCH, ChannelSpec(snr_db=25.0, delay=500,
                                       trailing=2000), seed=12)(waveform)
    # Wreck the middle of the data section, past the header, hard enough that the
    # code cannot repair it.
    damaged = aired.copy()
    start = len(aired) // 2
    damaged[start: start + 20000] += np.random.default_rng(1).normal(0.0, 0.5, 20000)
    decoded = decode_burst(BENCH, damaged)

    assert decoded.header is not None           # the header still read
    assert decoded.payload is None              # but no bytes were handed back
    assert not decoded.ok
    assert "CRC" in decoded.metrics.error


def test_a_corrupted_header_rejects_the_whole_burst() -> None:
    payload = _payload(128)
    waveform = _burst(payload)
    aired = Channel(BENCH, ChannelSpec(snr_db=25.0, delay=500,
                                       trailing=2000), seed=13)(waveform)
    # The header sits right after the preamble and training block.
    damaged = aired.copy()
    start = 500 + 4 * BENCH.symbol_samples
    damaged[start: start + 7 * BENCH.symbol_samples] += np.random.default_rng(2).normal(
        0.0, 0.8, 7 * BENCH.symbol_samples)
    decoded = decode_burst(BENCH, damaged)

    assert decoded.header is None
    assert decoded.payload is None
    assert not decoded.ok


def test_no_amount_of_damage_ever_produces_wrong_bytes() -> None:
    # The invariant that matters most: an operational message that arrives
    # silently corrupted is worse than one that does not arrive.
    payload = _payload(256)
    waveform = _burst(payload)
    rng = np.random.default_rng(SEED)
    delivered = 0
    for trial in range(30):
        aired = Channel(BENCH, ChannelSpec(snr_db=20.0, delay=400,
                                           trailing=2000), seed=trial)(waveform)
        damaged = aired.copy()
        start = int(rng.integers(0, max(1, len(damaged) - 8000)))
        damaged[start: start + 8000] += rng.normal(0.0, rng.uniform(0.05, 1.0), 8000)
        decoded = decode_burst(BENCH, damaged)
        if decoded.payload is not None:
            assert decoded.payload == payload, "delivered bytes that were not sent"
            delivered += 1
    # The point is not that everything failed -- some damage the code repairs.
    assert 0 < delivered < 30


def test_a_burst_from_a_different_profile_does_not_decode_as_this_one() -> None:
    import dataclasses
    other = dataclasses.replace(BENCH, name="OTHER", first_carrier=20, num_carriers=40)
    payload = _payload(64)
    waveform = build_burst(other, PhyHeader(OfdmFrameType.DATA, 1, mcs=1,
                                            payload_len=len(payload)), payload)
    assert decode_burst(BENCH, waveform).payload is None


# -- the everything-at-once path used by the bench -------------------------- #

@pytest.mark.parametrize("index", [0, 1, 2])
def test_the_realistic_channel_carries_every_practical_mcs(index: int) -> None:
    payload = _payload(256)
    waveform = _burst(payload, index=index)
    decoded = decode_burst(BENCH, realistic(BENCH, snr_db=20.0, seed=14)(waveform))
    assert decoded.payload == payload
    assert decoded.metrics.mcs == index
    assert mcs(index).modulation in {"bpsk", "qpsk", "qam16"}


def test_the_realistic_channel_switches_everything_on_at_once() -> None:
    described = realistic(BENCH, snr_db=15.0).spec.describe()
    for expected in ("SNR", "gain", "delay", "CFO", "multipath", "clock"):
        assert expected in described
