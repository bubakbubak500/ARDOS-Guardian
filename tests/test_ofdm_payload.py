"""The OFDM VHF payload backend: selection, configuration, codec, and PTT.

The tests that matter most here are the keying ones. Everything else in this
feature can fail and cost a transfer; a transmitter left keyed costs the band.
"""

from __future__ import annotations

import threading

import numpy as np
import pytest

from guardian.config import PAYLOAD_BACKENDS, StationConfig
from guardian.ofdm import BENCH, OfdmStatus, simulated_pair
from guardian.ofdm.channel import Channel, ChannelSpec
from guardian.payload import OfdmVhfBackend, VaraP2PBackend, make_backend
from guardian.payload.negotiated import NegotiatedPayload
from guardian.payload.ofdm_vhf import RadioAudioPipe
from guardian.session import Message

SEED = 0xA5
#: The pre-trigger history plus a block of scheduling slack, for the window-size
#: assertions below.
SLACK = 0.6


def _payload(size: int, seed: int = SEED) -> bytes:
    return np.random.default_rng(seed).integers(0, 256, size, dtype=np.uint8).tobytes()


# -- required test 13: backend selection ------------------------------------ #

def test_the_factory_dispatches_on_the_configured_name() -> None:
    assert isinstance(make_backend("vara_p2p"), VaraP2PBackend)
    assert isinstance(make_backend("ofdm_vhf"), OfdmVhfBackend)
    # An unknown name must not leave a station with no transport at all.
    assert isinstance(make_backend("some_future_transport"), VaraP2PBackend)
    assert isinstance(make_backend(), VaraP2PBackend)


def test_each_backend_reports_the_name_the_config_selects_it_by() -> None:
    assert make_backend("vara_p2p").name == "vara_p2p"
    assert make_backend("ofdm_vhf").name == "ofdm_vhf"
    assert set(PAYLOAD_BACKENDS) == {"vara_p2p", "ofdm_vhf"}


def test_the_ofdm_backend_is_built_without_a_vara_client() -> None:
    # Required condition 16, at the factory level: the whole point of this
    # transport is that VARA is not involved.
    backend = make_backend("ofdm_vhf", vara=None, audio_input="Mic",
                           audio_output="Speaker")
    assert isinstance(backend, OfdmVhfBackend)
    assert not hasattr(backend, "vara")


def test_the_vara_backend_ignores_the_dependencies_it_has_no_use_for() -> None:
    # make_backend("vara_p2p") has to behave exactly as it did before the OFDM
    # dependencies joined the bag.
    backend = make_backend("vara_p2p", vara=None, audio_input="Mic",
                           audio_output="Speaker", ptt=lambda on: None,
                           ofdm_profile="BENCH", ofdm_mcs=3,
                           prompt=lambda text: True)
    assert isinstance(backend, VaraP2PBackend)


def test_the_backend_carries_the_configured_waveform_settings() -> None:
    backend = make_backend("ofdm_vhf", ofdm_profile="BENCH", ofdm_mcs=2,
                           ofdm_tx_lead_ms=250, ofdm_tx_tail_ms=150,
                           ofdm_max_retries=7)
    assert backend.profile is BENCH
    assert backend.mcs_index == 2
    assert backend.tx_lead_ms == 250
    assert backend.tx_tail_ms == 150
    assert backend.max_retries == 7
    assert isinstance(backend.status, OfdmStatus)


def test_each_ofdm_transfer_starts_with_clean_directional_progress() -> None:
    backend = make_backend("ofdm_vhf", ofdm_profile="BENCH", ofdm_mcs=2)
    backend.status.tx_bytes = 4096
    backend.status.total_bytes = 8192

    backend._reset_status("receive")

    assert backend.status.direction == "receive"
    assert backend.status.tx_bytes == 0
    assert backend.status.rx_bytes == 0
    assert backend.status.total_bytes == 0
    assert backend.status.profile == "BENCH"


def test_a_profile_this_build_does_not_have_falls_back_and_says_so() -> None:
    logged = []
    backend = make_backend("ofdm_vhf", ofdm_profile="VHF_NARROW_50K",
                           on_log=logged.append)
    assert backend.profile is BENCH
    assert any("VHF_NARROW_50K" in line for line in logged)


# -- required tests 14 and 15: configuration -------------------------------- #

def test_a_config_from_before_ofdm_existed_still_loads(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        '{"callsign": "OK7PS", "vara_mode": "FM", "payload_backend": "vara_p2p"}',
        encoding="utf-8",
    )
    config = StationConfig.load(path)

    assert config.callsign == "OK7PS"
    assert config.payload_backend == "vara_p2p"
    assert config.ofdm_profile == "BENCH"
    assert config.ofdm_mcs == 1
    assert config.ofdm_tx_lead_ms == 300
    assert config.ofdm_tx_tail_ms == 100
    assert config.ofdm_max_retries == 4


def test_the_dropped_winlink_transport_is_still_coerced_to_vara(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"payload_backend": "winlink_manual"}', encoding="utf-8")
    assert StationConfig.load(path).payload_backend == "vara_p2p"


def test_an_ofdm_selection_survives_a_restart(tmp_path) -> None:
    # The loader used to rewrite anything that was not "vara_p2p", so a station
    # configured for OFDM would silently be back on VARA after every restart.
    path = tmp_path / "config.json"
    StationConfig(callsign="OK7PS", payload_backend="ofdm_vhf", ofdm_mcs=2,
                  ofdm_max_retries=6).save(path)
    config = StationConfig.load(path)

    assert config.payload_backend == "ofdm_vhf"
    assert config.ofdm_mcs == 2
    assert config.ofdm_max_retries == 6


@pytest.mark.parametrize("stored", ["", "nonsense", "OFDM_VHF", None, 7])
def test_a_transport_guardian_does_not_have_falls_back_to_vara(tmp_path,
                                                               stored) -> None:
    import json
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"payload_backend": stored}), encoding="utf-8")
    assert StationConfig.load(path).payload_backend == "vara_p2p"


def test_vara_remains_the_default_for_a_fresh_station() -> None:
    assert StationConfig().payload_backend == "vara_p2p"
    assert PAYLOAD_BACKENDS[0] == "vara_p2p"


# -- the negotiated router -------------------------------------------------- #

class _Recorder:
    """A backend that records what it was asked to do."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.sent: list[int] = []
        self.received: list[int] = []

    def start_send(self, msg, done) -> None:
        self.sent.append(msg.msg_id)
        done(True)

    def start_receive(self, msg, done) -> None:
        self.received.append(msg.msg_id)
        done(True)

    def cancel(self, msg) -> None:
        pass


def _router() -> tuple[NegotiatedPayload, _Recorder, _Recorder, list[str]]:
    vara, ofdm, logged = _Recorder("vara_p2p"), _Recorder("ofdm_vhf"), []
    return (NegotiatedPayload(default=vara, backends={"ofdm_vhf": ofdm},
                              on_log=logged.append),
            vara, ofdm, logged)


def test_the_router_sends_over_the_transport_the_hop_agreed_on() -> None:
    router, vara, ofdm, _ = _router()
    agreed = Message(1, "OK7PS", "OK1AAA", "OK1AAA")
    agreed.payload_transport = "ofdm_vhf"
    fell_back = Message(2, "OK7PS", "OK1AAA", "OK1AAA")
    fell_back.payload_transport = "vara_p2p"

    router.start_send(agreed, lambda ok: None)
    router.start_send(fell_back, lambda ok: None)
    router.start_receive(agreed, lambda ok: None)

    assert ofdm.sent == [1]
    assert vara.sent == [2]
    assert ofdm.received == [1]


def test_the_router_falls_back_and_logs_when_the_transport_is_missing() -> None:
    router, vara, _, logged = _router()
    router.backends.clear()
    message = Message(3, "OK7PS", "OK1AAA", "OK1AAA")
    message.payload_transport = "ofdm_vhf"

    router.start_send(message, lambda ok: None)

    assert vara.sent == [3]
    assert any("ofdm_vhf" in line for line in logged)


def test_a_message_that_never_negotiated_uses_the_default() -> None:
    router, vara, ofdm, _ = _router()
    router.start_send(Message(4, "OK7PS", "OK1AAA", "OK1AAA"), lambda ok: None)
    assert vara.sent == [4]
    assert ofdm.sent == []


# -- a fake soundcard ------------------------------------------------------- #

class FakeSounddevice:
    """Enough of sounddevice to exercise keying and codec ordering.

    `play` hands whatever was transmitted to a sink, so a pair of these can be
    wired into a loopback without any hardware.
    """

    def __init__(self, on_play=None, fail_on_play: Exception | None = None) -> None:
        self.on_play = on_play
        self.fail_on_play = fail_on_play
        self.played: list[np.ndarray] = []
        self.streams: list["FakeStream"] = []
        self.checked: list[tuple[str, object]] = []

    def check_input_settings(self, **kwargs) -> None:
        self.checked.append(("input", kwargs.get("device")))

    def check_output_settings(self, **kwargs) -> None:
        self.checked.append(("output", kwargs.get("device")))

    def InputStream(self, **kwargs):  # noqa: N802 - mirrors sounddevice
        stream = FakeStream(kwargs.get("callback"))
        self.streams.append(stream)
        return stream

    def play(self, samples, samplerate=None, device=None) -> None:  # noqa: ARG002
        if self.fail_on_play is not None:
            raise self.fail_on_play
        self.played.append(np.asarray(samples, dtype=np.float64))
        if self.on_play is not None:
            self.on_play(np.asarray(samples, dtype=np.float64))

    def wait(self) -> None:
        pass


class FakeStream:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True

    def feed(self, samples: np.ndarray) -> None:
        block = np.asarray(samples, dtype=np.float32).reshape(-1, 1)
        self.callback(block, len(block), None, None)


class RecordingPtt:
    """A PTT line that remembers every transition, in order."""

    def __init__(self) -> None:
        self.transitions: list[bool] = []

    def __call__(self, enabled: bool) -> None:
        self.transitions.append(bool(enabled))

    @property
    def keyed(self) -> bool:
        return bool(self.transitions) and self.transitions[-1]


def _feed(stream: "FakeStream", *, quiet_blocks: int, burst: np.ndarray,
          trailing_blocks: int, seed: int) -> None:
    """Push quiet, then a burst, then quiet, in stream-sized blocks.

    Synchronous on purpose. Everything these tests assert is about what the
    receive loop does with audio that has arrived; feeding it from a thread only
    adds the scheduler's timing to the outcome, which made two of them flaky.
    """
    block = int(BENCH.sample_rate * 0.05)
    quiet = np.random.default_rng(seed).normal(0.0, 1e-4, block)
    for _ in range(quiet_blocks):
        stream.feed(quiet)
    for start in range(0, len(burst), block):
        stream.feed(burst[start: start + block])
    for _ in range(trailing_blocks):
        stream.feed(quiet)


def _pipe(sd: FakeSounddevice, ptt, monkeypatch) -> RadioAudioPipe:
    monkeypatch.setattr("guardian.payload.ofdm_vhf._import_sounddevice", lambda: sd)
    return RadioAudioPipe(BENCH, input_device=1, output_device=2, ptt=ptt,
                          tx_lead_ms=0, tx_tail_ms=0)


# -- required tests 17 and 18: PTT is always released ----------------------- #

def test_keying_strictly_brackets_the_playback(monkeypatch) -> None:
    events = []
    ptt = RecordingPtt()
    sd = FakeSounddevice(on_play=lambda samples: events.append("play"))

    def keying(enabled: bool) -> None:
        events.append("key" if enabled else "unkey")
        ptt(enabled)

    pipe = _pipe(sd, keying, monkeypatch)
    pipe.start()
    timing = pipe.send(np.zeros(1000))
    pipe.stop()

    assert events == ["key", "play", "unkey"]
    assert not ptt.keyed
    assert timing.waveform == pytest.approx(1000 / BENCH.sample_rate)
    assert timing.lead >= 0.15
    assert timing.guard == pytest.approx(0.4)
    assert timing.tail >= 0.25
    assert timing.keyed_total == pytest.approx(
        timing.lead + timing.waveform + timing.guard + timing.tail
    )


def test_the_transmitter_is_released_when_playback_raises(monkeypatch) -> None:
    ptt = RecordingPtt()
    sd = FakeSounddevice(fail_on_play=OSError("PortAudio fell over"))
    pipe = _pipe(sd, ptt, monkeypatch)
    pipe.start()

    with pytest.raises(OSError):
        pipe.send(np.zeros(1000))

    assert ptt.transitions == [True, False]
    assert not ptt.keyed


def test_a_pipe_that_was_never_started_does_not_key_at_all(monkeypatch) -> None:
    ptt = RecordingPtt()
    pipe = _pipe(FakeSounddevice(), ptt, monkeypatch)
    with pytest.raises(RuntimeError):
        pipe.send(np.zeros(10))
    assert ptt.transitions == []


def test_unusable_devices_are_refused_before_anything_is_keyed(monkeypatch) -> None:
    ptt = RecordingPtt()
    sd = FakeSounddevice()
    monkeypatch.setattr("guardian.payload.ofdm_vhf._import_sounddevice", lambda: sd)
    # resolve_device hands back the name it was given when it cannot resolve it.
    pipe = RadioAudioPipe(BENCH, input_device="No such microphone",
                          output_device=2, ptt=ptt)
    with pytest.raises(RuntimeError, match="RX audio input"):
        pipe.start()
    assert ptt.transitions == []

    pipe = RadioAudioPipe(BENCH, input_device=1,
                          output_device="No such speaker", ptt=ptt)
    with pytest.raises(RuntimeError, match="TX audio output"):
        pipe.start()
    assert ptt.transitions == []


def test_the_backend_never_leaves_the_radio_keyed_on_any_failure_path() -> None:
    # Every way a transfer can fail, in one place, because "PTT stuck on" is the
    # failure that stops being about one message and starts being about the band.
    def failing_pipe(profile):
        raise OSError("soundcard vanished")

    for label, kwargs in (
        ("acquire raises", {"on_acquire": lambda: (_ for _ in ()).throw(
            RuntimeError("codec busy"))}),
        ("qsy refuses", {"on_qsy": lambda msg: False}),
        ("pipe cannot open", {"pipe_factory": failing_pipe}),
    ):
        ptt = RecordingPtt()
        results: list[bool] = []
        released: list[str] = []
        backend = OfdmVhfBackend(
            ptt=ptt,
            on_release=lambda: released.append("release"),
            on_unqsy=lambda: released.append("unqsy"),
            **kwargs,
        )
        backend._send(Message(1, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"x"),
                      results.append)

        assert results == [False], label
        assert not ptt.keyed, label
        assert "unqsy" in released, label


# -- required test 19: the codec goes back before the confirmation ---------- #

def test_the_send_lifecycle_matches_the_vara_backend_hook_for_hook() -> None:
    # Ported from the VARA tests: done() may immediately key the radio to send
    # RECEIVED over AFSK, so the codec must already be back with that modem.
    events = []

    class LoggingPipe:
        def __init__(self, profile) -> None:
            self.profile = profile

        def start(self) -> None:
            events.append("pipe-open")

        def stop(self) -> None:
            events.append("pipe-close")

        def send(self, samples) -> None:
            events.append("air")

        def receive(self, timeout):  # noqa: ARG002
            from guardian.ofdm.framing import (AckBitmap, OfdmFrameType,
                                               PhyHeader, build_burst)
            answer = AckBitmap(1, frozenset({0})).encode()
            return build_burst(
                BENCH,
                PhyHeader(OfdmFrameType.ACK, 1, block_seq=0,
                          payload_len=len(answer)),
                answer,
            )

    backend = OfdmVhfBackend(
        pipe_factory=LoggingPipe,
        on_acquire=lambda: events.append("acquire"),
        on_release=lambda: events.append("release"),
        on_qsy=lambda msg: events.append(("qsy", msg.next_hop)) or True,
        on_unqsy=lambda: events.append("restore"),
    )
    backend._send(Message(1, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"hi"),
                  lambda ok: events.append(("done", ok)))

    assert events == [
        "acquire", ("qsy", "OK1AAA"), "pipe-open", "air",
        "pipe-close", "restore", "release", ("done", True),
    ]


def test_the_receive_lifecycle_releases_the_codec_before_the_callback() -> None:
    events = []

    class SilentPipe:
        def __init__(self, profile) -> None:
            pass

        def start(self) -> None:
            pass

        def stop(self) -> None:
            events.append("pipe-close")

        def send(self, samples) -> None:
            pass

        def receive(self, timeout):  # noqa: ARG002
            return None

    backend = OfdmVhfBackend(
        pipe_factory=SilentPipe,
        on_acquire=lambda: events.append("acquire"),
        on_release=lambda: events.append("release"),
        on_receive_qsy=lambda msg: events.append(("qsy", msg.source)) or True,
        on_unqsy=lambda: events.append("restore"),
    )
    backend._receive(Message(1, "OK1AAA", "OK7PS", "OK7PS"),
                     lambda ok: events.append(("done", ok)))

    assert events == [
        "acquire", ("qsy", "OK1AAA"), "pipe-close", "restore", "release",
        ("done", False),
    ]


def test_a_refused_receive_qsy_never_opens_the_soundcard() -> None:
    opened = []
    backend = OfdmVhfBackend(
        pipe_factory=lambda profile: opened.append("open"),
        on_receive_qsy=lambda msg: False,
    )
    results: list[bool] = []
    backend._receive(Message(1, "OK1AAA", "OK7PS", "OK7PS"), results.append)
    assert results == [False]
    assert opened == []


# -- an end-to-end transfer through the backend ----------------------------- #

def test_two_backends_move_a_real_payload_over_a_simulated_channel() -> None:
    # The whole feature end to end, minus the soundcard: two OfdmVhfBackends,
    # the real ARQ, the real waveform, a channel with noise and a delay.
    payload = _payload(1500)
    spec = ChannelSpec(snr_db=15.0, delay=400, trailing=1500)
    near, far = simulated_pair(BENCH, spec, seed=SEED)

    class OpenPipe:
        """A simulated pipe wearing the start/stop lifecycle the backend expects."""

        def __init__(self, profile, wired) -> None:
            self.profile = profile
            self.wired = wired

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def send(self, samples) -> None:
            self.wired.send(samples)

        def receive(self, timeout):
            return self.wired.receive(timeout)

    sender = OfdmVhfBackend(pipe_factory=lambda profile: OpenPipe(profile, near))
    receiver = OfdmVhfBackend(pipe_factory=lambda profile: OpenPipe(profile, far))

    outgoing = Message(31, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=payload)
    incoming = Message(31, "OK7PS", "OK1AAA", "OK1AAA")
    verdicts: dict[str, bool] = {}
    listener = threading.Thread(
        target=lambda: receiver._receive(
            incoming, lambda ok: verdicts.__setitem__("received", ok)),
        daemon=True)
    listener.start()
    sent_ok: list[bool] = []
    sender._send(outgoing, sent_ok.append)
    listener.join(timeout=180)

    assert sent_ok == [True]
    assert verdicts.get("received") is True
    assert incoming.payload_bytes == payload
    assert incoming.body == ""
    assert sender.status.tx_bytes == len(payload)


# -- the receive side of the audio pipe ------------------------------------- #

def test_the_pipe_returns_nothing_when_the_band_is_quiet(monkeypatch) -> None:
    sd = FakeSounddevice()
    pipe = _pipe(sd, RecordingPtt(), monkeypatch)
    pipe.start()
    assert pipe.receive(timeout=0.05) is None


def test_the_pipe_hands_over_a_window_containing_the_whole_burst(monkeypatch) -> None:
    from guardian.ofdm import decode_burst
    from guardian.ofdm.framing import OfdmFrameType, PhyHeader, build_burst

    payload = _payload(200)
    header = PhyHeader(OfdmFrameType.DATA, 55, mcs=1, payload_len=len(payload))
    burst = build_burst(BENCH, header, payload)
    aired = Channel(BENCH, ChannelSpec(snr_db=20.0), seed=1)(burst)

    sd = FakeSounddevice()
    pipe = _pipe(sd, RecordingPtt(), monkeypatch)
    pipe.start()

    # A quiet stretch first, so the noise floor is tracked before the burst --
    # exactly the order a real receiver sees it in. Fed synchronously rather than
    # from a thread: a feeder thread makes the result depend on how promptly the
    # scheduler runs it, and none of what this test is about needs that.
    _feed(sd.streams[0], quiet_blocks=6, burst=aired, trailing_blocks=20, seed=2)
    window = pipe.receive(timeout=20.0)

    assert window is not None
    assert decode_burst(BENCH, window).payload == payload


def test_the_pipe_drops_audio_captured_during_its_own_transmission(monkeypatch) -> None:
    sd = FakeSounddevice()
    pipe = _pipe(sd, RecordingPtt(), monkeypatch)
    pipe.start()
    stream = sd.streams[0]

    # Half duplex: audio that arrives while the transmitter is up is our own
    # sidetone, and splicing it into a receive window would corrupt it.
    pipe._tx_lock.acquire()
    stream.feed(np.ones(100))
    pipe._tx_lock.release()
    assert pipe.receive(timeout=0.05) is None


def test_stopping_the_pipe_closes_the_stream_and_never_raises(monkeypatch) -> None:
    sd = FakeSounddevice()
    pipe = _pipe(sd, RecordingPtt(), monkeypatch)
    pipe.start()
    pipe.stop()
    assert sd.streams[0].closed
    pipe.stop()          # idempotent, because it runs in `finally` blocks


def test_the_longest_burst_is_derived_from_the_profile(monkeypatch) -> None:
    pipe = _pipe(FakeSounddevice(), RecordingPtt(), monkeypatch)
    longest = pipe.longest_burst_samples()
    # A full block at the most robust MCS, which is the slowest one.
    assert longest > BENCH.sample_rate * 4
    assert longest == pipe.longest_burst_samples()


def test_the_transmitted_waveform_carries_a_tail_guard(monkeypatch) -> None:
    from guardian.payload.ofdm_vhf import TX_GUARD_SECONDS

    sd = FakeSounddevice()
    pipe = _pipe(sd, RecordingPtt(), monkeypatch)
    pipe.start()
    pipe.send(np.ones(1000))

    expected = 1000 + int(TX_GUARD_SECONDS * BENCH.sample_rate)
    assert len(sd.played[0]) == expected
    assert not sd.played[0][-10:].any(), "the guard must be silence"


def test_a_short_burst_is_handed_over_promptly_not_after_the_longest_one(
    monkeypatch,
) -> None:
    # The bug this pins: the hangover used to be checked only when the audio
    # stream stalled. A live stream never stalls, so every receive waited out the
    # longest burst the profile allows -- about five seconds on BENCH, which is
    # longer than the ARQ waits for an answer. Every acknowledgement would have
    # been too late and the link would have stalled on every block.
    from guardian.ofdm.framing import OfdmFrameType, PhyHeader, build_burst
    from guardian.payload.ofdm_vhf import HANGOVER_SECONDS

    ack = build_burst(BENCH, PhyHeader(OfdmFrameType.ACK, 1, block_seq=0))
    aired = Channel(BENCH, ChannelSpec(snr_db=20.0), seed=1)(ack)

    sd = FakeSounddevice()
    pipe = _pipe(sd, RecordingPtt(), monkeypatch)
    pipe.start()
    stream = sd.streams[0]

    # Enough trailing quiet to satisfy the hangover, and a great deal more on top.
    _feed(stream, quiet_blocks=6, burst=aired, trailing_blocks=80, seed=3)
    window = pipe.receive(timeout=20.0)

    assert window is not None
    # The window covers the burst plus the hangover, not the longest burst the
    # profile can produce.
    budget = len(aired) + int(BENCH.sample_rate * (HANGOVER_SECONDS + SLACK))
    assert len(window) < budget
    assert len(window) < pipe.longest_burst_samples()


def test_the_squelch_waits_for_the_noise_floor_before_it_can_fire(monkeypatch) -> None:
    # The floor starts at zero, so without a settling period the very first block
    # is above it and the receiver hands back a window of silence.
    from guardian.payload.ofdm_vhf import FLOOR_SETTLE_SECONDS

    sd = FakeSounddevice()
    pipe = _pipe(sd, RecordingPtt(), monkeypatch)
    pipe.start()
    stream = sd.streams[0]

    block = int(BENCH.sample_rate * 0.05)
    quiet = np.random.default_rng(4).normal(0.0, 1e-3, block)
    for _ in range(3):
        stream.feed(quiet)

    assert pipe.receive(timeout=0.2) is None
    assert pipe._floor > 0.0
    assert FLOOR_SETTLE_SECONDS < 1.0, "must be well inside a PTT turnaround"


def test_a_stream_that_stops_mid_burst_ends_it_rather_than_waiting_for_ever(
    monkeypatch,
) -> None:
    # The samples-based hangover can never be satisfied once the device stops
    # delivering, so the wall clock is the backstop. It is deliberately far more
    # generous than the hangover: a live stream held up for a few hundred
    # milliseconds by a Viterbi decode on another thread is ordinary, and cutting
    # the burst short there would throw away a transfer that was arriving fine.
    from guardian.payload.ofdm_vhf import (HANGOVER_SECONDS,
                                           STREAM_STALL_SECONDS)

    assert STREAM_STALL_SECONDS > 2 * HANGOVER_SECONDS

    sd = FakeSounddevice()
    pipe = _pipe(sd, RecordingPtt(), monkeypatch)
    pipe.start()
    stream = sd.streams[0]

    block = int(BENCH.sample_rate * 0.05)
    quiet = np.random.default_rng(6).normal(0.0, 1e-4, block)
    for _ in range(6):
        stream.feed(quiet)
    # Loud, and then nothing at all -- no trailing quiet to count.
    for _ in range(4):
        stream.feed(np.full(block, 0.15))

    window = pipe.receive(timeout=10.0)
    assert window is not None
    assert len(window) > 0


def test_a_loud_first_block_does_not_leave_the_squelch_deaf(monkeypatch) -> None:
    """The failure mode this asymmetry was found by, stated as a test.

    A station only ever listens for a payload answer straight after it has
    transmitted, and the first audio back is the receiver's AGC recovering and
    whatever the radio makes of the carrier dropping -- far louder than the real
    noise. Seed the floor from that and the trigger sits three times higher
    again, so the acknowledgement that follows never opens the squelch and the
    station retransmits into a channel it has made itself deaf to.
    """
    sd = FakeSounddevice()
    pipe = _pipe(sd, RecordingPtt(), monkeypatch)
    pipe.start()
    stream = sd.streams[0]

    block = int(BENCH.sample_rate * 0.05)
    rng = np.random.default_rng(11)
    stream.feed(rng.normal(0.0, 0.2, block))          # the thump after unkeying
    for _ in range(8):
        stream.feed(rng.normal(0.0, 1e-3, block))     # the real noise floor

    assert pipe.receive(timeout=0.3) is None
    # Settled onto the quiet part, not stuck up at the thump.
    assert pipe._floor < 0.01, f"floor stuck at {pipe._floor}"
    assert pipe._trigger_level() < 0.03


def test_the_squelch_can_say_what_it_is_doing(monkeypatch) -> None:
    # The sender logs this when a reply window comes up empty, so "nothing was
    # heard" can be told apart from "the squelch never opened".
    sd = FakeSounddevice()
    pipe = _pipe(sd, RecordingPtt(), monkeypatch)
    pipe.start()
    stream = sd.streams[0]
    block = int(BENCH.sample_rate * 0.05)
    for _ in range(4):
        stream.feed(np.random.default_rng(5).normal(0.0, 1e-3, block))
    pipe.receive(timeout=0.2)

    note = pipe.describe_squelch()
    assert "dBFS" in note and "opens at" in note
