"""Constellation mapping, Gray coding, soft output, and the interleaver."""

from __future__ import annotations

import math

import numpy as np
import pytest

from guardian.modem.fec import K as FEC_K
from guardian.ofdm.constellation import (BITS_PER_SYMBOL, MODULATIONS,
                                         bits_per_symbol, constellation,
                                         demap_hard, demap_llr, evm,
                                         gray_to_binary, map_bits)
from guardian.ofdm.interleaving import deinterleave, interleave, stride_for

SEED = 0xA5


@pytest.mark.parametrize("modulation", MODULATIONS)
def test_every_bit_pattern_maps_and_demaps_back(modulation: str) -> None:
    # Exhaustive over the constellation: every symbol the mapper can produce
    # must come back as the bits that produced it.
    bits_each = bits_per_symbol(modulation)
    count = 1 << bits_each
    shifts = np.arange(bits_each - 1, -1, -1)
    patterns = ((np.arange(count)[:, None] >> shifts) & 1).astype(np.int8)

    symbols = map_bits(patterns.reshape(-1), modulation)
    assert len(symbols) == count
    assert np.array_equal(demap_hard(symbols, modulation), patterns.reshape(-1))


@pytest.mark.parametrize("modulation", MODULATIONS)
def test_random_bit_streams_round_trip(modulation: str) -> None:
    rng = np.random.default_rng(SEED)
    bits_each = bits_per_symbol(modulation)
    bits = rng.integers(0, 2, 10_000 * bits_each).astype(np.int8)
    assert np.array_equal(demap_hard(map_bits(bits, modulation), modulation), bits)


@pytest.mark.parametrize("modulation", MODULATIONS)
def test_average_symbol_energy_is_exactly_one(modulation: str) -> None:
    # Every measurement downstream -- SNR, EVM, the LLR scaling -- assumes this,
    # so a constellation whose power drifted would quietly corrupt all of them.
    points = constellation(modulation)
    assert float(np.mean(np.abs(points) ** 2)) == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize(
    "modulation,expected_scale",
    [("bpsk", 1.0), ("qpsk", math.sqrt(2.0)), ("qam16", math.sqrt(10.0)),
     ("qam64", math.sqrt(42.0))],
)
def test_normalisation_matches_the_textbook_factor(modulation: str,
                                                   expected_scale: float) -> None:
    # The odd-integer lattice scaled by 1, 1/sqrt2, 1/sqrt10, 1/sqrt42.
    points = constellation(modulation) * expected_scale
    lattice = np.round(np.real(points)).astype(int)
    assert np.allclose(np.real(points), lattice, atol=1e-9)
    assert set(np.abs(np.unique(lattice))) <= set(range(1, 16, 2))


@pytest.mark.parametrize("modulation", ["qpsk", "qam16", "qam64"])
def test_neighbouring_points_differ_in_exactly_one_bit(modulation: str) -> None:
    # This is the whole point of Gray mapping: the mistakes noise actually makes
    # are single-bit ones, which is what the convolutional code can absorb.
    points = constellation(modulation)
    spacing = float(np.min(np.diff(np.unique(np.round(np.real(points), 9)))))

    for index, point in enumerate(points):
        for offset in (spacing, -spacing, 1j * spacing, -1j * spacing):
            match = np.flatnonzero(np.abs(points - (point + offset)) < spacing / 100)
            if not len(match):
                continue  # an edge point has no neighbour that way
            differing = bin(index ^ int(match[0])).count("1")
            assert differing == 1, (
                f"{modulation}: points {index} and {int(match[0])} are adjacent "
                f"but differ in {differing} bits"
            )


def test_gray_decoding_is_a_permutation_whose_neighbours_differ_in_one_bit() -> None:
    decoded = gray_to_binary(np.arange(256))
    assert sorted(decoded.tolist()) == list(range(256))
    # Walk the integers in order and check the codes that produce them: each
    # step must flip exactly one bit. That is the defining Gray property, and it
    # is what makes the amplitude ordering in `_pam_levels` correct.
    codes = np.empty(256, dtype=int)
    codes[decoded] = np.arange(256)
    flips = [bin(int(a) ^ int(b)).count("1") for a, b in zip(codes[:-1], codes[1:])]
    assert flips == [1] * 255


@pytest.mark.parametrize("modulation", MODULATIONS)
def test_llr_signs_agree_with_the_hard_decision(modulation: str) -> None:
    # Positive LLR must mean "bit is 1" -- the convention viterbi_decode_soft
    # expects. A flipped sign here decodes to noise and nothing else complains.
    rng = np.random.default_rng(SEED)
    bits = rng.integers(0, 2, 1000 * bits_per_symbol(modulation)).astype(np.int8)
    symbols = map_bits(bits, modulation)
    llr = demap_llr(symbols, modulation, noise_var=0.05)
    assert np.array_equal((llr > 0).astype(np.int8), bits)


def test_llr_magnitude_grows_as_the_channel_gets_quieter() -> None:
    symbols = map_bits(np.array([0, 1, 1, 0]), "qpsk")
    loud = np.abs(demap_llr(symbols, "qpsk", noise_var=1.0))
    quiet = np.abs(demap_llr(symbols, "qpsk", noise_var=0.01))
    assert np.all(quiet > loud)


def test_per_symbol_noise_variance_discounts_the_bad_carriers() -> None:
    # A notched carrier comes out of the equaliser with its noise amplified.
    # Passing that per carrier is what stops the decoder trusting it.
    symbols = map_bits(np.array([1, 0, 1, 0, 1, 0]), "qpsk")
    variance = np.array([0.01, 1.0, 100.0])
    llr = np.abs(demap_llr(symbols, "qpsk", variance)).reshape(3, 2)
    assert llr[0].min() > llr[1].min() > llr[2].max()


def test_demap_llr_rejects_a_mismatched_variance_length() -> None:
    symbols = map_bits(np.array([1, 0, 1, 0]), "qpsk")
    with pytest.raises(ValueError):
        demap_llr(symbols, "qpsk", np.array([1.0, 2.0, 3.0]))


def test_mapping_rejects_bits_that_do_not_fill_whole_symbols() -> None:
    with pytest.raises(ValueError):
        map_bits(np.array([1, 0, 1]), "qam16")


def test_unknown_modulation_is_rejected_by_name() -> None:
    with pytest.raises(ValueError):
        bits_per_symbol("qam256")
    assert set(BITS_PER_SYMBOL) == {"bpsk", "qpsk", "qam16", "qam64"}


@pytest.mark.parametrize("modulation", MODULATIONS)
def test_evm_is_zero_on_exact_symbols(modulation: str) -> None:
    rng = np.random.default_rng(SEED)
    bits = rng.integers(0, 2, 400 * bits_per_symbol(modulation)).astype(np.int8)
    assert evm(map_bits(bits, modulation), modulation) == pytest.approx(0.0, abs=1e-12)


# -- interleaving ----------------------------------------------------------- #

@pytest.mark.parametrize("length", [1, 2, 3, 17, 88, 1000, 8272])
def test_interleaving_round_trips_at_every_length(length: int) -> None:
    rng = np.random.default_rng(SEED)
    bits = rng.integers(0, 2, length).astype(np.int8)
    assert np.array_equal(deinterleave(interleave(bits)), bits)


@pytest.mark.parametrize("length", [17, 88, 1000, 8272])
def test_the_interleaver_is_a_permutation(length: int) -> None:
    marked = interleave(np.arange(length))
    assert sorted(marked.tolist()) == list(range(length))


def test_interleaving_works_on_soft_values_too() -> None:
    # Deinterleaving happens on LLRs, not bits, so it must not assume integers.
    rng = np.random.default_rng(SEED)
    soft = rng.normal(0.0, 3.0, 500)
    assert np.array_equal(deinterleave(interleave(soft)), soft)


@pytest.mark.parametrize("length", [88, 176, 1000, 8272, 12408])
def test_the_stride_is_coprime_and_near_the_golden_ratio(length: int) -> None:
    stride = stride_for(length)
    assert math.gcd(stride, length) == 1
    assert 0 < stride < length
    # Stepping up to the nearest coprime value moves the ratio further at short
    # block lengths, which is why the tolerance is not tighter.
    assert stride / length == pytest.approx(1 / 1.618, abs=0.05)


def _sent_order(length: int) -> np.ndarray:
    """`result[j]` is the coded-bit index transmitted at position `j`.

    `interleave(arange(n))` is exactly that array, by definition of what
    interleaving does to a block of labels.
    """
    return interleave(np.arange(length))


def test_consecutive_coded_bits_end_up_far_apart() -> None:
    # The property the convolutional code depends on: neighbouring code bits
    # must not share a fate. Every step is the stride, or the stride wrapped
    # around the end of the block.
    length = 8272
    stride = stride_for(length)
    where = np.argsort(_sent_order(length))     # where each coded bit was sent
    gaps = set(np.abs(np.diff(where)).tolist())
    assert gaps == {stride, length - stride}
    assert min(gaps) > length // 4


@pytest.mark.parametrize(
    "bits_each,symbols,expected_minimum",
    # The four BENCH data sections: 44 data carriers, one 514-byte coded block.
    [(1, 188, 44), (2, 94, 39), (4, 47, 39), (6, 32, 9)],
)
def test_a_dead_carrier_leaves_errors_the_code_can_absorb(
    bits_each: int, symbols: int, expected_minimum: int
) -> None:
    # A notched carrier is wrong in every OFDM symbol, i.e. at `bits_each` fixed
    # offsets modulo the symbol length. Because the stride is coprime with the
    # block length (and the symbol length divides it), each of those becomes one
    # residue class in the coded stream, so the damage arrives as arithmetic
    # progressions rather than a clump. What matters is that the closest two
    # destroyed code bits stay clear of the code's 6-bit memory.
    carriers = 44
    symbol_bits = carriers * bits_each
    length = symbol_bits * symbols
    sent = _sent_order(length)

    worst = length
    for carrier in range(carriers):
        residues = set(range(carrier * bits_each, (carrier + 1) * bits_each))
        killed = sorted(int(sent[j]) for j in range(length) if j % symbol_bits in residues)
        worst = min(worst, int(np.diff(killed).min()))
    assert worst > FEC_K - 1, f"errors only {worst} apart, code memory is {FEC_K - 1}"
    # Pinned so a change to the stride rule has to be a deliberate one.
    assert worst == expected_minimum


@pytest.mark.parametrize("bits_each,symbols", [(1, 188), (2, 94), (4, 47), (6, 32)])
def test_one_destroyed_symbol_also_comes_apart(bits_each: int, symbols: int) -> None:
    # The other clumped failure: a whole OFDM symbol lost to a noise burst.
    symbol_bits = 44 * bits_each
    length = symbol_bits * symbols
    sent = _sent_order(length)
    middle = symbols // 2
    killed = sorted(int(sent[j]) for j in range(middle * symbol_bits,
                                                (middle + 1) * symbol_bits))
    assert int(np.diff(killed).min()) > FEC_K - 1
