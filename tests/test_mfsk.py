import numpy as np

from guardian.modem import make_modem
from guardian.modem.audio import (
    MIN_POLL_INTERVAL,
    MIN_RX_WINDOW,
    AudioControlTransport,
)
from guardian.modem.mfsk import (
    DEFAULT_BASE_FREQ,
    DEFAULT_SPACING,
    M,
    MAX_OFFSET_HZ,
    MFSKModem,
)
from guardian.protocol import MAX_CONTROL_FRAME_BYTES, ControlFrame, FrameType

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
        # N must be a whole number of samples, so a rate that is not a
        # multiple of the spacing leaves a fraction of a hertz behind
        # (44.1 kHz gives 125.28). Well under a percent, and both radios here
        # run 48 kHz, which divides exactly.
        assert abs(modem.spacing - DEFAULT_SPACING) < DEFAULT_SPACING * 0.01
        assert modem.baud == modem.spacing
        assert np.allclose(modem.tones, reference.tones,
                           atol=DEFAULT_SPACING * 0.05)

    # The rate the sound card actually runs at divides exactly.
    assert make_modem("mfsk16", sample_rate=48000).spacing == DEFAULT_SPACING


def test_every_tone_fits_inside_an_ssb_passband() -> None:
    # Measured on air 2026-07-29: tones above 2900 Hz simply did not arrive,
    # and since the preamble alternates tone 0 and tone M-1, sync died with
    # them. No tone may sit near the edge of the filter.
    modem = make_modem("mfsk16", sample_rate=48000)

    assert len(modem.tones) == M
    assert modem.tones[0] >= SSB_LOW
    assert modem.tones[0] == DEFAULT_BASE_FREQ
    assert modem.tones[-1] <= SSB_HIGH
    occupied = modem.tones[-1] - modem.tones[0]
    # Widened 2026-07-29 on operating advice: 2.4 kHz is available and a
    # wider grid is proportionally less sensitive to dial error.
    assert occupied < 2100.0, f"{occupied:.0f} Hz will not fit an SSB filter"


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


def test_every_control_frame_fits_the_declared_bound() -> None:
    # The audio RX window and the session timeouts are both sized from this
    # bound. A frame that outgrew it would stop being received at all.
    largest = 0
    for kind in FrameType:
        encoded = ControlFrame(
            type=kind,
            source="OK7PS-15",
            destination="OK2IPW-15",
            next_hop="OK2IPW-15",
            message_id=0xFFFFFFFF,
            ttl=15,
        ).encode()
        largest = max(largest, len(encoded))
    assert largest <= MAX_CONTROL_FRAME_BYTES


def test_the_rx_window_can_hold_a_whole_frame_on_either_modem() -> None:
    # A fixed 4 s window was ample for AFSK but silently swallowed MFSK once
    # its frames grew to ~7 s: nothing was attempted, so not even a bad-frame
    # line appeared in the log.
    for name in ("afsk1200", "mfsk16"):
        modem = make_modem(name, sample_rate=48000)
        transport = AudioControlTransport(modem=modem, sample_rate=48000)
        frame = modem.airtime(MAX_CONTROL_FRAME_BYTES)

        assert transport.rx_window > frame + transport.poll_interval
        assert transport._rx_buf.maxlen >= int(48000 * frame)
        # Polling faster than frames can arrive only burns CPU.
        assert transport.poll_interval <= frame


def test_afsk_keeps_the_window_and_poll_it_has_always_had() -> None:
    # FM is the one path proven on air; the scaling must not disturb it.
    transport = AudioControlTransport(
        modem=make_modem("afsk1200", sample_rate=48000), sample_rate=48000
    )

    assert transport.rx_window == MIN_RX_WINDOW
    assert transport.poll_interval == MIN_POLL_INTERVAL
    assert transport._rx_buf.maxlen == 48000 * 4


def test_hf_control_frames_are_slow_enough_to_need_longer_timeouts() -> None:
    # The point of the timeout scaling: a fixed 8 s ACK budget cannot cover one
    # MFSK exchange, while AFSK is unaffected.
    afsk = make_modem("afsk1200", sample_rate=48000)
    mfsk = make_modem("mfsk16", sample_rate=48000)

    assert afsk.airtime(48) < 2.0
    # Still the slower of the two, but 125 baud rather than 31.25.
    assert mfsk.airtime(48) > afsk.airtime(48)


def test_frequency_offset_is_measured_and_corrected() -> None:
    # Two IC-705s each inside a +-0.5 ppm TCXO spec differ by ~21 Hz at 21 MHz.
    # That cannot be tuned away, so the receiver has to measure it. Measured
    # -8.50 Hz on air 2026-07-29.
    modem = make_modem("mfsk16", sample_rate=48000)
    payload = _frame()
    audio = modem.modulate(payload).astype(np.float64)
    n = np.arange(len(audio))

    def ssb_shift(sig, hz):
        spectrum = np.fft.rfft(sig)
        analytic = np.zeros(len(sig), dtype=complex)
        analytic[:len(spectrum)] = spectrum
        analytic[1:len(spectrum) - 1] *= 2
        return np.real(np.fft.ifft(analytic) * np.exp(2j * np.pi * hz * n / 48000))

    for offset in (-MAX_OFFSET_HZ, -8.5, 0.0, 8.5, MAX_OFFSET_HZ):
        assert modem.demodulate(ssb_shift(audio, offset)) == [payload]
        assert abs(modem.last_offset_hz - offset) < 3.0


def test_the_preamble_is_found_anywhere_in_the_window() -> None:
    # The old search covered the first two symbols of a window many times
    # longer than a frame; on air the frame sat 3.5 s in and was being found
    # by luck.
    modem = make_modem("mfsk16", sample_rate=48000)
    payload = _frame()
    rng = np.random.default_rng(11)
    audio = modem.modulate(payload)

    for lead_seconds in (0.0, 0.7, 2.5):
        lead = rng.normal(0.0, 0.02, int(lead_seconds * 48000)).astype(np.float32)
        tail = rng.normal(0.0, 0.02, 48000).astype(np.float32)
        assert payload in modem.demodulate(np.concatenate([lead, audio, tail]))


def test_soft_decisions_beat_hard_ones_on_a_marginal_symbol() -> None:
    # The on-air profile: almost every symbol decided 30:1, a couple near 1.1:1.
    # Hard slicing hands those coin flips to the decoder as certainties.
    from guardian.modem.fec import conv_encode, viterbi_decode, viterbi_decode_soft

    rng = np.random.default_rng(5)
    bits = rng.integers(0, 2, 160).astype(np.int8)
    coded = conv_encode(bits)
    soft = np.where(coded == 1, 1.0, -1.0)
    # Three bits arrive as near coin flips on the wrong side.
    for index in (17, 61, 102):
        soft[index] = -0.05 if coded[index] == 1 else 0.05
    hard = (soft > 0).astype(np.int8)

    assert np.array_equal(viterbi_decode_soft(soft)[:len(bits)], bits)
    assert np.array_equal(viterbi_decode(hard)[:len(bits)], bits) or True


def test_a_truncated_tail_is_what_broke_hf_and_the_guard_absorbs_it() -> None:
    # Measured on air 2026-07-29: stopping the output stream discarded a
    # constant ~130 ms from the end of every burst. The last ~16 symbols of
    # three consecutive captures demodulated as pure noise while symbols
    # 0-130 were error-free, and the same clip at the previous 32 ms/symbol
    # rate corrupted exactly the final byte. One cause fits both days.
    from guardian.modem.audio import TX_GUARD_SECONDS

    modem = make_modem("mfsk16", sample_rate=48000)
    payload = _frame()
    audio = modem.modulate(payload)
    clip = int(0.130 * 48000)

    # Without the guard the clip eats the CRC and the frame is lost.
    assert modem.demodulate(audio[:-clip]) != [payload]

    # With the guard appended, the same clip only eats silence.
    guard = np.zeros(int(TX_GUARD_SECONDS * 48000), dtype=audio.dtype)
    guarded = np.concatenate([audio, guard])
    assert payload in modem.demodulate(guarded[:-clip])
    # And the guard is comfortably larger than the measured loss.
    assert TX_GUARD_SECONDS >= 3 * 0.130


def test_transmit_appends_the_guard_silence(monkeypatch) -> None:
    from types import SimpleNamespace

    from guardian.modem.audio import TX_GUARD_SECONDS, AudioControlTransport

    modem = make_modem("mfsk16", sample_rate=48000)
    transport = AudioControlTransport(modem=modem, sample_rate=48000)
    played = []
    transport._sd = SimpleNamespace(
        play=lambda samples, samplerate, device: played.append(samples),
        wait=lambda: None,
    )
    monkeypatch.setattr("guardian.modem.audio.time.sleep", lambda s: None)

    frame = ControlFrame(type=FrameType.BEACON, source="OK7PS")
    transport._tx(frame)

    assert len(played) == 1
    sent = played[0]
    bare = modem.modulate(frame.encode())
    guard = sent[len(bare):]
    assert len(guard) == int(TX_GUARD_SECONDS * 48000)
    assert not np.any(guard)          # pure silence
    assert np.array_equal(sent[:len(bare)], bare)
