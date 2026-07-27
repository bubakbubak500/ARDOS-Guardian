import numpy as np

from guardian.modem.afsk import AFSKModem, FEC_SYNC, PREAMBLE
from guardian.protocol import ControlFrame, FrameType


def _control_payload() -> bytes:
    return ControlFrame(
        FrameType.HAVE_MSG,
        source="OK6LZ",
        destination="OK2IPW",
        next_hop="OK2IPW",
        message_id=123456,
    ).encode()


def _radio_window(
    samples: np.ndarray,
    *,
    clock_error_ppm: int = 0,
    noise: float = 0.0,
) -> np.ndarray:
    ratio = 1.0 + clock_error_ppm / 1_000_000
    source_axis = np.arange(samples.size)
    resampled = np.interp(
        np.arange(0, samples.size - 1, ratio),
        source_axis,
        samples,
    ).astype(np.float32)
    rng = np.random.default_rng(20260727)
    prefix = rng.normal(0.0, noise, 48_000)
    suffix = rng.normal(0.0, noise, 96_000)
    return np.concatenate((prefix, resampled * 0.2, suffix)).astype(np.float32)


def test_afsk_decodes_a_clean_control_frame() -> None:
    modem = AFSKModem()
    payload = _control_payload()

    decoded = modem.demodulate(_radio_window(modem.modulate(payload)))

    assert payload in decoded


def test_afsk_tracks_realistic_soundcard_clock_error_and_noise() -> None:
    modem = AFSKModem()
    payload = _control_payload()
    audio = _radio_window(
        modem.modulate(payload),
        clock_error_ppm=5_000,
        noise=0.02,
    )

    decoded = modem.demodulate(audio)

    assert payload in decoded


def test_afsk_rejects_unrelated_vara_like_audio() -> None:
    modem = AFSKModem()
    time = np.arange(192_000) / modem.fs
    sweep = np.sin(
        2
        * np.pi
        * (500.0 * time + (2_900.0 - 500.0) * time**2 / 8.0)
    ).astype(np.float32)

    assert modem.demodulate(sweep) == []


def test_afsk_normalizes_a_severely_unbalanced_radio_audio_path() -> None:
    modem = AFSKModem()
    payload = _control_payload()
    audio = modem.modulate(payload)
    spectrum = np.fft.rfft(audio)
    frequencies = np.fft.rfftfreq(audio.size, 1.0 / modem.fs)
    # Model a radio/audio path that suppresses the 1200 Hz tone by 35 dB.
    gain = 1.0 - (1.0 - 10 ** (-35 / 20)) * np.exp(
        -0.5 * ((frequencies - 1_200.0) / 250.0) ** 2
    )
    filtered = np.fft.irfft(spectrum * gain, audio.size).astype(np.float32)

    decoded = modem.demodulate(_radio_window(filtered, noise=0.01))

    assert payload in decoded


def test_afsk_prefers_crc_valid_timing_hypothesis_over_higher_energy() -> None:
    modem = AFSKModem()
    payload = _control_payload()
    invalid = payload[:-2] + b"\xff\xff"

    # Exercise the burst-selection policy without depending on a particular
    # synthetic distortion: the stronger candidate is corrupt, while another
    # clock/phase hypothesis for the same physical burst has a valid CRC.
    candidates = [
        (0.95, 48_000.0, invalid),
        (0.80, 48_020.0, payload),
    ]

    selected = modem._select_candidates(  # type: ignore[attr-defined]
        candidates,
        validator=lambda candidate: candidate == payload,
    )

    assert selected == [payload]


def test_afsk_fec_recovers_when_one_complete_payload_copy_is_lost() -> None:
    modem = AFSKModem()
    payload = _control_payload()
    audio = modem.modulate(payload)
    first_copy = (
        len(PREAMBLE) * 8
        + len(FEC_SYNC) * 8
        + 3 * 8
    )
    start = int(first_copy * modem.sps)
    end = start + int(len(payload) * 8 * modem.sps)
    audio[start:end] = 0.0

    decoded = modem.demodulate(_radio_window(audio, noise=0.005))

    assert payload in decoded
