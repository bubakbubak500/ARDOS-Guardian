"""Variable-size coded sections must not accumulate unbounded index arrays."""

import numpy as np

from guardian.ofdm.interleaving import _permutation, deinterleave, interleave, stride_for


def test_variable_length_interleaving_roundtrips_after_cache_eviction():
    _permutation.cache_clear()
    stride_for.cache_clear()
    original = np.arange(4096, dtype=np.float64) / 7
    encoded = interleave(original)
    for length in range(65, 145):
        values = np.arange(length, dtype=np.int8) % 2
        assert np.array_equal(deinterleave(interleave(values)), values)
    assert _permutation.cache_info().currsize <= 64
    assert stride_for.cache_info().currsize <= 64
    assert np.array_equal(deinterleave(encoded), original)
    assert np.array_equal(interleave(original), encoded)
