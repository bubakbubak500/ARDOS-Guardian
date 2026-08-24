import numpy as np

from guardian.modem.morse import modulate_morse, normalise_morse_text


def test_morse_normalises_callsigns_and_discards_unsupported_punctuation() -> None:
    assert normalise_morse_text("ok2xxx de ok7ps/p!") == "OK2XXX DE OK7PS/P"


def test_default_40_wpm_morse_has_standard_timing_and_audible_energy() -> None:
    sample_rate = 48_000
    samples = modulate_morse("E E", sample_rate=sample_rate)

    assert len(samples) == round(sample_rate * 1.2 / 40) * 9
    assert samples.dtype == np.float64
    assert 0.1 < float(np.max(np.abs(samples))) <= 0.55
