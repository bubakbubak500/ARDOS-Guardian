import hashlib
import platform

import pytest

from guardian.compression import (
    CODECS,
    _HEADER,
    _compress_with,
    decompress_guardian_envelope,
    external_codecs_available,
    is_guardian_envelope,
)


@pytest.mark.skipif(platform.system() != "Windows", reason="bundled Windows codecs")
@pytest.mark.parametrize("codec_index", [0, 2], ids=["zpaq", "lpaq8"])
def test_bundled_high_ratio_codecs_round_trip(codec_index: int) -> None:
    original = (b"SITUATION NORMAL\n" * 1000) + bytes(range(256))

    encoded = _compress_with(CODECS[codec_index], original)

    assert external_codecs_available()
    assert is_guardian_envelope(encoded)
    assert len(encoded) < len(original)
    assert decompress_guardian_envelope(encoded, timeout=15.0) == original


@pytest.mark.skipif(platform.system() != "Windows", reason="bundled Windows codecs")
def test_paq8px_is_really_attempted_and_round_trips() -> None:
    original = b"PAQ8PX candidate" * 1000

    encoded = _compress_with(CODECS[1], original)
    magic, codec_id, profile, size, digest = _HEADER.unpack_from(encoded)

    assert magic == b"GCP1"
    assert codec_id == 2
    assert profile == 1
    assert size == len(original)
    assert digest == hashlib.sha256(original).digest()
    assert decompress_guardian_envelope(encoded, timeout=15.0) == original


def test_corrupt_guardian_envelope_is_rejected_before_zip_parsing() -> None:
    original = b"A" * 5000
    if platform.system() != "Windows":
        pytest.skip("bundled Windows codecs")
    encoded = bytearray(_compress_with(CODECS[2], original))
    # Corrupt the authenticated SHA-256 field (after magic/id/profile/size).
    encoded[14] ^= 0x01

    with pytest.raises((ValueError, RuntimeError)):
        decompress_guardian_envelope(bytes(encoded), timeout=15.0)
