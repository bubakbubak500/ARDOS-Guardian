import numpy as np

from guardian.modem import make_modem
from guardian.modem.mfsk import DEFAULT_SPACING, M, MFSKModem
from guardian.protocol import ControlFrame, FrameType

# A conservative SSB passband. An IC-705 on USB passes roughly this; anything
# outside it never leaves the transmitter or never reaches the demodulator.
SSB_LOW, SSB_HIGH = 300.0, 2700.0


def _frame() -> bytes:
    return ControlFrame(
        type=FrameType.HAVE_MSG,
        source="OK7PS",
        destination="OK2IPW",
        next_hop="OK2IPW",
        message_id=270532649,
    ).encode()


def test_tone_geometry_is_independent_of_the_device_sample_rate() -> None:
    # The bug: n_per_symbol was a fixed 256, but spacing = sample_rate /
    # n_per_symbol. At the 48 kHz the sound card actually runs at, that made
    # the spacing 187.5 Hz instead of 31.25 and spread the tones to 3412 Hz.
    reference = MFSKModem(sample_rate=8000)
    for fs in (8000, 11025, 16000, 44100, 48000):
        modem = make_modem("mfsk16", sample_rate=fs)
        # N must be a whole number of samples, so a rate that is not a multiple
        # of the spacing leaves a fraction of a hertz behind (44.1 kHz gives
        # 31.232 Hz). Two stations on different cards still land within a
        # hertz of each other at the top tone, far inside any dial offset.
        assert abs(modem.spacing - DEFAULT_SPACING) < 0.1
        assert modem.baud == modem.spacing
        assert np.allclose(modem.tones, reference.tones, atol=1.0)

    # The rate the sound card actually runs at divides exactly.
    assert make_modem("mfsk16", sample_rate=48000).spacing == DEFAULT_SPACING


def test_every_tone_fits_inside_an_ssb_passband() -> None:
    # Measured on air 2026-07-29: tones above 2900 Hz simply did not arrive,
    # and since the preamble alternates tone 0 and tone M-1, sync died with
    # them. No tone may sit near the edge of the filter.
    modem = make_modem("mfsk16", sample_rate=48000)

    assert len(modem.tones) == M
    assert modem.tones[0] >= SSB_LOW
    assert modem.tones[-1] <= SSB_HIGH
    occupied = modem.tones[-1] - modem.tones[0]
    assert occupied < 600.0, f"{occupied:.0f} Hz is too wide for a narrow HF channel"


def test_round_trip_at_the_real_device_rate() -> None:
    payload = _frame()
    modem = make_modem("mfsk16", sample_rate=48000)

    audio = modem.modulate(payload)
    assert modem.demodulate(audio) == [payload]


def test_round_trip_survives_noise_and_a_ragged_start() -> None:
    payload = _frame()
    modem = make_modem("mfsk16", sample_rate=48000)
    rng = np.random.default_rng(7)

    audio = modem.modulate(payload)
    lead = rng.normal(0.0, 0.02, size=modem.N // 3).astype(np.float32)
    noisy = np.concatenate([lead, audio + rng.normal(0.0, 0.05, size=len(audio))])

    assert payload in modem.demodulate(noisy)


def test_airtime_matches_what_is_actually_transmitted() -> None:
    # Session timeouts are derived from airtime(); if it drifts from reality
    # the HF handshake starts timing out again.
    for fs in (8000, 48000):
        modem = make_modem("mfsk16", sample_rate=fs)
        for size in (8, 22, 34, 48):
            payload = b"\x5a" * size
            measured = len(modem.modulate(payload)) / modem.fs
            assert abs(modem.airtime(size) - measured) < 1e-9


def test_afsk_airtime_matches_too() -> None:
    modem = make_modem("afsk1200", sample_rate=48000)
    for size in (8, 34, 48):
        measured = len(modem.modulate(b"\x5a" * size)) / modem.fs
        assert abs(modem.airtime(size) - measured) < 0.01


def test_hf_control_frames_are_slow_enough_to_need_longer_timeouts() -> None:
    # The point of the timeout scaling: a fixed 8 s ACK budget cannot cover one
    # MFSK exchange, while AFSK is unaffected.
    afsk = make_modem("afsk1200", sample_rate=48000)
    mfsk = make_modem("mfsk16", sample_rate=48000)

    assert afsk.airtime(48) < 2.0
    assert mfsk.airtime(48) > 5.0
    assert 2 * mfsk.airtime(48) > 8.0
