"""Profiles, the symbol engine, and the noiseless waveform round trip."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from guardian.ofdm import (BENCH, MCS_TABLE, FecProfile, PhyHeader, mcs,
                           profile, profile_names)
from guardian.ofdm.config import DEFAULT_MCS_INDEX, HEADER_MCS, OfdmConfigError
from guardian.ofdm.framing import (HEADER_BYTES, OfdmFrameError, OfdmFrameType,
                                   build_burst, burst_duration, decode_burst,
                                   header_symbols, section_symbols, split_blocks)
from guardian.ofdm.phy import BurstReceiver, OfdmModulator, analytic, band_analytic

SEED = 0xA5


def _payload(count: int, seed: int = SEED) -> bytes:
    return np.random.default_rng(seed).integers(0, 256, count, dtype=np.uint8).tobytes()


# -- the profile object ----------------------------------------------------- #

def test_bench_profile_geometry_is_what_the_documentation_claims() -> None:
    assert BENCH.sample_rate == 48000
    assert BENCH.fft_size == 1024
    assert BENCH.cp_length == 128
    assert BENCH.subcarrier_spacing == pytest.approx(46.875)
    assert BENCH.symbol_samples == 1152
    assert BENCH.symbol_duration == pytest.approx(0.024)
    assert BENCH.cp_duration == pytest.approx(0.0026667, abs=1e-6)
    assert BENCH.num_carriers == 52
    assert BENCH.num_pilots == 8
    assert BENCH.num_data_carriers == 44
    assert BENCH.block_size == 512
    # About 2.4 kHz, which fits a stock FM voice channel. Not an RF claim.
    assert BENCH.occupied_bandwidth == pytest.approx(2437.5)
    low, high = BENCH.occupied_band
    assert (low, high) == pytest.approx((539.06, 2976.56), abs=0.1)


def test_sample_rate_and_occupied_bandwidth_are_not_the_same_number() -> None:
    # The mistake the whole design is arranged to prevent.
    assert BENCH.occupied_bandwidth < BENCH.sample_rate / 10
    assert BENCH.bandwidth_fraction == pytest.approx(2437.5 / 24000)


def test_carrier_sets_partition_the_active_band_and_avoid_dc_and_nyquist() -> None:
    active = BENCH.carriers
    assert active[0] == BENCH.first_carrier
    assert active[-1] == BENCH.first_carrier + BENCH.num_carriers - 1
    assert 0 not in active                      # DC stays empty
    assert active[-1] < BENCH.fft_size // 2     # so does the Nyquist bin
    assert set(BENCH.pilot_carriers) | set(BENCH.data_carriers) == set(active.tolist())
    assert not set(BENCH.pilot_carriers) & set(BENCH.data_carriers)
    # Pilots are evenly spaced across the active set, starting at its edge.
    assert list(BENCH.pilot_positions) == list(range(0, 52, 7))


def test_the_preamble_uses_only_even_bins() -> None:
    # This is what makes the time-domain symbol repeat after fft_size/2 samples,
    # which is the only thing the burst detector has to hold on to.
    assert all(bin_index % 2 == 0 for bin_index in BENCH.preamble_carriers)
    assert len(BENCH.preamble_carriers) == 26


def test_registry_lookup_and_unknown_names() -> None:
    # There was one profile when this was written. There is now a ladder of six,
    # so the assertion is that BENCH is still in it and still the object the name
    # resolves to -- the rest of the ladder is pinned in tests/test_bench.py.
    assert profile("BENCH") is BENCH
    assert "BENCH" in profile_names()
    assert profile_names() == sorted(profile_names())
    with pytest.raises(OfdmConfigError):
        profile("VHF_NARROW_50K")


@pytest.mark.parametrize("change,reason", [
    ({"fft_size": 1000}, "not a power of two"),
    ({"fft_size": 8}, "too small"),
    ({"cp_length": 0}, "no guard at all"),
    ({"cp_length": 4096}, "guard longer than the symbol"),
    ({"first_carrier": 0}, "DC would carry data"),
    ({"num_carriers": 3}, "not enough carriers"),
    ({"first_carrier": 500, "num_carriers": 52}, "runs past Nyquist"),
    ({"pilot_spacing": 1}, "every carrier a pilot"),
    ({"pilot_spacing": 52}, "only one pilot, so no phase slope"),
    ({"training_symbols": 1}, "nothing to measure noise against"),
    ({"preamble_symbols": 0}, "nothing to detect"),
    ({"block_size": 0}, "no payload possible"),
    ({"detection_threshold": 1.5}, "not a correlation"),
    ({"tx_rms": 0.0}, "silence"),
])
def test_a_profile_that_cannot_work_is_rejected_on_construction(change: dict,
                                                               reason: str) -> None:
    with pytest.raises(OfdmConfigError):
        dataclasses.replace(BENCH, **change)


def test_two_pilots_is_the_documented_minimum() -> None:
    # A straight line needs two points; one pilot could only ever give a constant
    # phase correction, never the slope that a clock offset produces.
    assert dataclasses.replace(BENCH, pilot_spacing=51).num_pilots == 2
    with pytest.raises(OfdmConfigError, match="phase slope"):
        dataclasses.replace(BENCH, pilot_spacing=52)


# -- the MCS table ---------------------------------------------------------- #

def test_the_mcs_table_covers_all_four_constellations_at_rate_one_half() -> None:
    assert [entry.modulation for entry in MCS_TABLE] == ["bpsk", "qpsk", "qam16", "qam64"]
    assert [entry.bits_per_symbol for entry in MCS_TABLE] == [1, 2, 4, 6]
    # Phase 1 has exactly one code rate because that is what fec.py provides.
    assert {entry.code_rate.numerator / entry.code_rate.denominator
            for entry in MCS_TABLE} == {0.5}
    assert [entry.index for entry in MCS_TABLE] == [0, 1, 2, 3]


def test_the_bootstrap_mode_is_the_most_robust_one() -> None:
    # The header and every ACK ride on this, so it has to be the mode that
    # survives the worst channel the link can hold.
    assert HEADER_MCS.index == 0
    assert HEADER_MCS.modulation == "bpsk"
    assert DEFAULT_MCS_INDEX == 1


def test_mcs_labels_read_the_way_the_bench_prints_them() -> None:
    assert mcs(1).label == "MCS1 QPSK r=1/2"
    assert mcs(3).label == "MCS3 64-QAM r=1/2"
    with pytest.raises(OfdmConfigError):
        mcs(9)


# -- the symbol engine ------------------------------------------------------ #

def test_symbols_are_real_by_construction() -> None:
    # Not "real after discarding an imaginary part" -- irfft cannot produce a
    # complex sample, which is the entire reason for building it this way.
    modulator = OfdmModulator(BENCH)
    for waveform in (modulator.preamble_symbol(), modulator.training_symbol(),
                     modulator.data_symbol(np.ones(BENCH.num_data_carriers))):
        assert waveform.dtype == np.float64
        assert len(waveform) == BENCH.symbol_samples


def test_the_cyclic_prefix_is_a_copy_of_the_symbol_tail() -> None:
    waveform = OfdmModulator(BENCH).training_symbol()
    prefix, body = waveform[:BENCH.cp_length], waveform[BENCH.cp_length:]
    assert np.allclose(prefix, body[-BENCH.cp_length:])


def test_the_preamble_repeats_after_half_an_fft() -> None:
    body = OfdmModulator(BENCH).preamble_symbol()[BENCH.cp_length:]
    half = BENCH.fft_size // 2
    assert np.allclose(body[:half], body[half:], atol=1e-12)


def test_a_symbol_only_puts_energy_on_its_active_carriers() -> None:
    modulator = OfdmModulator(BENCH)
    spectrum = np.abs(np.fft.rfft(modulator.training_symbol()[BENCH.cp_length:]))
    inactive = np.setdiff1d(np.arange(len(spectrum)), BENCH.carriers)
    assert spectrum[inactive].max() < 1e-9 * spectrum[BENCH.carriers].min()


def test_a_burst_is_scaled_to_the_profile_level_and_never_clips() -> None:
    modulator = OfdmModulator(BENCH)
    grid = np.random.default_rng(SEED).normal(size=(20, BENCH.num_data_carriers))
    waveform = modulator.burst(grid.astype(np.complex128))
    assert np.sqrt(np.mean(waveform ** 2)) == pytest.approx(BENCH.tx_rms, rel=1e-9)
    assert np.max(np.abs(waveform)) < 1.0
    # OFDM peaks well above its RMS; the level exists to leave room for that.
    assert 6.0 < modulator.crest_factor_db(waveform) < 16.0


def test_burst_length_matches_the_predicted_geometry() -> None:
    modulator = OfdmModulator(BENCH)
    grid = np.zeros((13, BENCH.num_data_carriers), dtype=np.complex128)
    assert len(modulator.burst(grid)) == modulator.burst_samples(13)
    assert modulator.overhead_symbols() == BENCH.preamble_symbols + BENCH.training_symbols


def test_a_grid_with_the_wrong_carrier_count_is_refused() -> None:
    with pytest.raises(ValueError):
        OfdmModulator(BENCH).burst(np.zeros((2, BENCH.num_carriers)))


def test_the_analytic_signal_keeps_the_real_part_it_started_with() -> None:
    x = np.random.default_rng(SEED).normal(size=4096)
    assert np.allclose(np.real(analytic(x)), x, atol=1e-9)


def test_the_band_limited_analytic_signal_drops_out_of_band_noise() -> None:
    # The 10 dB that acquisition would otherwise give away: noise fills the whole
    # audio band, the burst occupies a tenth of it.
    rng = np.random.default_rng(SEED)
    noise = rng.normal(size=1 << 15)
    filtered = np.real(band_analytic(noise, BENCH))
    kept = np.var(filtered) / np.var(noise)
    assert kept == pytest.approx(BENCH.bandwidth_fraction, rel=0.35)


def test_band_limiting_leaves_a_burst_essentially_intact() -> None:
    modulator = OfdmModulator(BENCH)
    grid = np.random.default_rng(SEED).normal(size=(8, BENCH.num_data_carriers))
    waveform = modulator.burst(grid.astype(np.complex128))
    kept = np.real(band_analytic(waveform, BENCH))
    assert np.var(kept) / np.var(waveform) > 0.99


# -- the noiseless round trip (required test 5) ----------------------------- #

@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_a_full_block_survives_an_ideal_channel_at_every_mcs(index: int) -> None:
    payload = _payload(BENCH.block_size)
    header = PhyHeader(OfdmFrameType.DATA, msg_id=7, block_seq=0, block_count=1,
                       mcs=index, payload_len=len(payload))
    decoded = decode_burst(BENCH, build_burst(BENCH, header, payload))

    assert decoded.payload == payload
    assert decoded.ok
    assert decoded.header == header
    assert decoded.metrics.evm_rms < 0.01          # under 1 %
    assert decoded.metrics.snr_db > 40.0
    assert decoded.metrics.sync_confidence > 0.99
    assert abs(decoded.metrics.cfo_hz) < 0.1
    assert decoded.metrics.mcs == index
    assert decoded.metrics.error is None
    assert len(decoded.metrics.channel_response) == BENCH.num_carriers


def test_an_ideal_channel_is_flat_across_the_band() -> None:
    payload = _payload(64)
    header = PhyHeader(OfdmFrameType.DATA, 1, payload_len=len(payload))
    decoded = decode_burst(BENCH, build_burst(BENCH, header, payload))
    power = np.abs(decoded.metrics.channel_response) ** 2
    assert power.max() / power.min() < 1.01


@pytest.mark.parametrize("size", [0, 1, 2, 511, 512])
def test_blocks_of_every_size_up_to_the_limit_round_trip(size: int) -> None:
    payload = _payload(size)
    header = PhyHeader(OfdmFrameType.DATA, 3, payload_len=size)
    assert decode_burst(BENCH, build_burst(BENCH, header, payload)).payload == payload


def test_acknowledgements_are_header_only_and_much_shorter_than_data() -> None:
    ack = PhyHeader(OfdmFrameType.ACK, 5, block_seq=2)
    data = PhyHeader(OfdmFrameType.DATA, 5, payload_len=BENCH.block_size)
    decoded = decode_burst(BENCH, build_burst(BENCH, ack))

    assert decoded.ok
    assert decoded.header.frame_type is OfdmFrameType.ACK
    assert decoded.header.block_seq == 2
    assert decoded.payload == b""
    assert burst_duration(BENCH, ack) < burst_duration(BENCH, data) / 5


# -- the header ------------------------------------------------------------- #

def test_the_header_is_sixteen_bytes_and_survives_a_byte_round_trip() -> None:
    header = PhyHeader(OfdmFrameType.DATA, msg_id=0xDEADBEEF, block_seq=513,
                       block_count=1024, mcs=3, payload_len=512, flags=0x40,
                       fec=FecProfile.FEC_7_8, subblock_count=17,
                       retransmission=True)
    raw = header.encode()
    assert len(raw) == HEADER_BYTES == 16
    assert PhyHeader.decode(raw) == header


@pytest.mark.parametrize("index", range(HEADER_BYTES))
def test_a_single_corrupted_header_byte_is_always_caught(index: int) -> None:
    raw = bytearray(PhyHeader(OfdmFrameType.DATA, 9, payload_len=4).encode())
    raw[index] ^= 0xFF
    with pytest.raises(OfdmFrameError):
        PhyHeader.decode(bytes(raw))


def test_a_header_from_a_future_frame_format_is_refused_not_guessed_at() -> None:
    from guardian.protocol import crc16
    body = bytearray(PhyHeader(OfdmFrameType.DATA, 1, payload_len=0).encode()[:14])
    body[0] = 99
    raw = bytes(body) + crc16(bytes(body)).to_bytes(2, "big")
    with pytest.raises(OfdmFrameError, match="version"):
        PhyHeader.decode(raw)


def test_an_unknown_frame_type_or_mcs_is_refused() -> None:
    from guardian.protocol import crc16

    def framed(**fields):
        body = bytearray(PhyHeader(OfdmFrameType.DATA, 1, payload_len=0).encode()[:14])
        for offset, value in fields.items():
            body[int(offset)] = value
        return bytes(body) + crc16(bytes(body)).to_bytes(2, "big")

    with pytest.raises(OfdmFrameError, match="frame type"):
        PhyHeader.decode(framed(**{"1": 77}))
    with pytest.raises(OfdmFrameError):
        PhyHeader.decode(framed(**{"10": 42}))       # MCS byte


def test_a_truncated_header_is_refused() -> None:
    with pytest.raises(OfdmFrameError):
        PhyHeader.decode(b"\x01\x01\x00")


def test_the_header_never_promises_more_payload_than_the_block_allows() -> None:
    header = PhyHeader(OfdmFrameType.DATA, 1, payload_len=1025)
    with pytest.raises(ValueError, match="protocol ARQ block size"):
        build_burst(BENCH, header, _payload(1025))


def test_a_header_that_disagrees_with_its_payload_is_a_programming_error() -> None:
    header = PhyHeader(OfdmFrameType.DATA, 1, payload_len=10)
    with pytest.raises(ValueError, match="payload length"):
        build_burst(BENCH, header, b"short")


# -- section geometry ------------------------------------------------------- #

def test_the_header_section_is_a_fixed_size_the_receiver_can_count_on() -> None:
    # The receiver has to demodulate the header before it knows anything, so its
    # length must follow from the profile alone.
    assert header_symbols(BENCH) == section_symbols(BENCH, HEADER_BYTES, "bpsk")
    assert header_symbols(BENCH) == 7


@pytest.mark.parametrize("modulation,expected", [
    ("bpsk", 188), ("qpsk", 94), ("qam16", 47), ("qam64", 32),
])
def test_a_denser_constellation_needs_fewer_symbols(modulation: str,
                                                    expected: int) -> None:
    assert section_symbols(BENCH, BENCH.block_size + 2, modulation) == expected


def test_airtime_follows_the_profile_rather_than_a_stored_constant() -> None:
    header = PhyHeader(OfdmFrameType.DATA, 1, mcs=1, payload_len=BENCH.block_size)
    symbols = (header_symbols(BENCH)
               + section_symbols(BENCH, 6, "bpsk")
               + section_symbols(BENCH, BENCH.block_size + 2, "qpsk"))
    overhead = BENCH.preamble_symbols + BENCH.training_symbols
    assert burst_duration(BENCH, header) == pytest.approx(
        (symbols + overhead) * BENCH.symbol_duration
    )


# -- segmentation ----------------------------------------------------------- #

@pytest.mark.parametrize("size,blocks", [
    (0, 1), (1, 1), (512, 1), (513, 2), (4096, 8), (4097, 9),
])
def test_segmentation_covers_the_message_exactly(size: int, blocks: int) -> None:
    payload = _payload(size)
    pieces = split_blocks(payload, BENCH.block_size)
    assert len(pieces) == blocks
    assert b"".join(pieces) == payload
    assert all(len(piece) <= BENCH.block_size for piece in pieces)


def test_a_zero_block_size_is_refused() -> None:
    with pytest.raises(ValueError):
        split_blocks(b"x", 0)


# -- receiver geometry ------------------------------------------------------ #

def test_the_receiver_reports_how_many_symbols_a_short_buffer_holds() -> None:
    payload = _payload(BENCH.block_size)
    header = PhyHeader(OfdmFrameType.DATA, 1, mcs=1, payload_len=len(payload))
    waveform = build_burst(BENCH, header, payload)
    full = BurstReceiver(BENCH, waveform, 0)
    expected = (header_symbols(BENCH)
                + section_symbols(BENCH, 6, "bpsk")
                + section_symbols(BENCH, BENCH.block_size + 2, "qpsk"))
    assert full.available_data_symbols() == expected

    clipped = BurstReceiver(BENCH, waveform[: len(waveform) // 2], 0)
    assert 0 < clipped.available_data_symbols() < expected


def test_a_buffer_that_ends_inside_the_training_block_is_rejected_cleanly() -> None:
    header = PhyHeader(OfdmFrameType.DATA, 1, payload_len=4)
    waveform = build_burst(BENCH, header, _payload(4))
    truncated = waveform[: 3 * BENCH.symbol_samples]
    with pytest.raises(IndexError):
        BurstReceiver(BENCH, truncated, 0)


def test_a_truncated_burst_reports_a_reason_instead_of_returning_bytes() -> None:
    payload = _payload(BENCH.block_size)
    header = PhyHeader(OfdmFrameType.DATA, 1, mcs=1, payload_len=len(payload))
    waveform = build_burst(BENCH, header, payload)
    decoded = decode_burst(BENCH, waveform[: len(waveform) // 2])

    assert decoded.payload is None
    assert not decoded.ok
    assert "truncated" in decoded.metrics.error


def test_pure_noise_never_produces_a_payload() -> None:
    # The detector has a false-alarm rate, as any detector does, so noise does
    # sometimes look like a burst -- and the header CRC is what stops it there.
    # What must hold at every seed is that no bytes come back.
    rng = np.random.default_rng(SEED)
    reasons = set()
    for _ in range(12):
        decoded = decode_burst(BENCH, rng.normal(0.0, 0.1, 2 * BENCH.sample_rate))
        assert decoded.header is None
        assert decoded.payload is None
        assert not decoded.ok
        assert decoded.metrics.error
        reasons.add(decoded.metrics.error.split(":")[0])
    assert reasons <= {"no burst detected", "header rejected",
                       "burst truncated before the header",
                       "burst truncated before the training block"}


def test_silence_is_reported_as_no_burst() -> None:
    decoded = decode_burst(BENCH, np.zeros(4 * BENCH.symbol_samples))
    assert decoded.header is None
    assert not decoded.ok


def test_a_buffer_shorter_than_one_symbol_does_not_raise() -> None:
    assert decode_burst(BENCH, np.zeros(10)).header is None


# -- what the reported SNR is actually measuring ---------------------------- #
#
# The whole 2026-08-09 air test turned on this distinction. The training-symbol
# estimate read 18-20 dB while the link was delivering 9-12 dB, because the two
# training symbols are identical and every deterministic distortion in a radio
# path cancels between them. These pin the behaviour so the honest figure cannot
# quietly go back to being the flattering one.

def _distorted(waveform: np.ndarray, strength: float) -> np.ndarray:
    """A memoryless nonlinearity: deterministic, and invisible to a noise estimate."""
    return np.tanh(waveform * strength) / strength


def test_distortion_leaves_the_noise_only_snr_untouched() -> None:
    header = PhyHeader(OfdmFrameType.DATA, 5, mcs=1, payload_len=256)
    clean = build_burst(BENCH, header, _payload(256))
    pad = np.zeros(BENCH.symbol_samples)

    honest = decode_burst(BENCH, np.concatenate([pad, clean, pad]))
    hurt = decode_burst(BENCH, np.concatenate([pad, _distorted(clean, 12.0), pad]))

    # Both still decode; the point is not that distortion breaks the link.
    assert honest.ok and hurt.ok
    # The training symbols cannot see it -- that is the trap, stated as a test.
    assert hurt.metrics.snr_db > 30.0
    # The post-equalisation figure can, and drops by a lot.
    assert hurt.metrics.residual_snr_db < honest.metrics.residual_snr_db - 6.0


def test_the_error_vector_is_measured_against_the_reference_not_the_decision() -> None:
    # 64-QAM is where a decision-directed measurement flatters hardest: the
    # points are close together, so a symbol that has landed on the wrong one is
    # reported as barely wrong at all.
    header = PhyHeader(OfdmFrameType.DATA, 5, mcs=3, payload_len=256)
    burst = build_burst(BENCH, header, _payload(256))
    pad = np.zeros(BENCH.symbol_samples)
    decoded = decode_burst(BENCH, np.concatenate([pad, burst, pad]))

    assert decoded.ok
    assert decoded.metrics.evm_source == "reference"
    assert decoded.metrics.residual_snr_db is not None


def test_a_failed_payload_still_reports_an_honest_snr_from_its_header() -> None:
    # The case that matters most: the burst that will not decode is exactly the
    # one whose SNR the operator needs, and the header carries a known-good
    # reference even when the payload does not.
    from guardian.ofdm.channel import Channel, ChannelSpec

    header = PhyHeader(OfdmFrameType.DATA, 5, mcs=3, payload_len=256)
    burst = build_burst(BENCH, header, _payload(256))
    pad = np.zeros(BENCH.symbol_samples)
    # 11 dB in band: comfortably above what the MCS0 header needs and well below
    # the 15.5 dB the MCS3 payload does, which is the gap the whole row exists for.
    aired = Channel(BENCH, ChannelSpec(snr_db=11.0), seed=SEED)(burst)
    decoded = decode_burst(BENCH, np.concatenate([pad, aired, pad]))

    assert decoded.header is not None and not decoded.ok
    assert decoded.metrics.evm_source == "reference_header"
    assert decoded.metrics.residual_snr_db is not None


def test_the_mcs_table_carries_measured_thresholds_in_ascending_order() -> None:
    thresholds = [entry.min_snr_db for entry in MCS_TABLE]
    assert thresholds == sorted(thresholds)
    # MCS0 and MCS1 carried 512-byte blocks at the bottom of the swept range;
    # the two dense constellations are the ones with a real floor under them.
    assert mcs(2).min_snr_db > mcs(1).min_snr_db
    assert mcs(3).min_snr_db > mcs(2).min_snr_db
