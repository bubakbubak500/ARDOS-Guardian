"""Recording received audio to a WAV file that the offline tools can read."""

from __future__ import annotations

import wave
from datetime import datetime

import numpy as np
import pytest

from guardian.modem.audio import DEFAULT_SAMPLE_RATE
from guardian.modem.recorder import (CLIP_THRESHOLD, SAMPLE_WIDTH, AudioCapture,
                                     RecordingSummary, WavRecorder, capture_path)
from guardian.ofdm import BENCH, PhyHeader, build_burst, decode_burst
from guardian.ofdm.channel import Channel, ChannelSpec
from guardian.ofdm.framing import OfdmFrameType

SEED = 0xA5


def _read(path) -> tuple[np.ndarray, int, int]:
    with wave.open(str(path), "rb") as handle:
        channels, width, rate = (handle.getnchannels(), handle.getsampwidth(),
                                 handle.getframerate())
        raw = handle.readframes(handle.getnframes())
    data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32767.0
    return data, rate, channels * width


def _record(tmp_path, blocks, rate: int = DEFAULT_SAMPLE_RATE) -> RecordingSummary:
    recorder = WavRecorder(tmp_path / "capture.wav", sample_rate=rate)
    recorder.start()
    for block in blocks:
        recorder.write(block)
    return recorder.stop()


# -- the file format the offline tools expect -------------------------------- #

def test_the_file_is_mono_sixteen_bit_at_the_paths_own_rate(tmp_path) -> None:
    # tools/ofdm_bench.py --read-wav refuses anything else, and resampling a
    # capture by hand is how a good recording becomes a misleading one.
    summary = _record(tmp_path, [np.zeros(1000)])
    data, rate, frame_bytes = _read(summary.path)

    assert rate == DEFAULT_SAMPLE_RATE
    assert frame_bytes == SAMPLE_WIDTH        # one channel, two bytes
    assert len(data) == 1000
    assert summary.sample_rate == DEFAULT_SAMPLE_RATE


def test_the_samples_come_back_as_they_went_in(tmp_path) -> None:
    rng = np.random.default_rng(SEED)
    original = rng.uniform(-0.6, 0.6, 4096)
    summary = _record(tmp_path, [original[:2048], original[2048:]])
    data, _, _ = _read(summary.path)

    assert len(data) == len(original)
    # 16-bit quantisation is the only difference allowed.
    assert np.max(np.abs(data - original)) < 2.0 / 32767.0


def test_nothing_is_normalised_on_the_way_out(tmp_path) -> None:
    # A quiet capture must stay quiet: the absolute level is how a clipping radio
    # is told apart from a quiet one, and normalising would erase the evidence.
    quiet = np.full(2000, 0.01)
    data, _, _ = _read(_record(tmp_path, [quiet]).path)
    assert np.max(np.abs(data)) == pytest.approx(0.01, abs=1e-4)


def test_a_capture_of_many_blocks_is_continuous(tmp_path) -> None:
    # A gap in a capture looks exactly like a channel that faded, so it must not
    # be possible to lose a block silently.
    blocks = [np.full(480, index / 100.0) for index in range(50)]
    summary = _record(tmp_path, blocks)
    data, _, _ = _read(summary.path)

    assert summary.dropped_blocks == 0
    assert len(data) == 50 * 480
    for index in range(50):
        assert data[index * 480] == pytest.approx(index / 100.0, abs=1e-4)


def test_samples_beyond_full_scale_are_clipped_not_wrapped(tmp_path) -> None:
    # Signed 16-bit wraps to the opposite rail on overflow, which would turn a
    # loud capture into one full of impulses that were never on the air.
    data, _, _ = _read(_record(tmp_path, [np.array([2.0, -2.0, 0.5])]).path)
    assert data[0] > 0.99 and data[1] < -0.99
    assert data[2] == pytest.approx(0.5, abs=1e-4)


# -- the summary the operator reads at the radio ----------------------------- #

def test_a_normal_capture_reports_its_level_and_reads_as_usable(tmp_path) -> None:
    rng = np.random.default_rng(SEED)
    summary = _record(tmp_path, [rng.normal(0.0, 0.15, DEFAULT_SAMPLE_RATE)])

    assert summary.duration_seconds == pytest.approx(1.0, abs=0.01)
    assert summary.rms == pytest.approx(0.15, rel=0.05)
    assert summary.rms_dbfs == pytest.approx(-16.5, abs=1.0)
    assert summary.peak_dbfs < 0.0
    assert 6.0 < summary.crest_factor_db < 20.0
    assert summary.clipped_samples == 0
    assert summary.usable
    assert not summary.silent
    assert not summary.clipping
    assert "usable" in summary.verdict()


def test_a_silent_capture_says_to_check_the_device(tmp_path) -> None:
    # The commonest way a session is wasted: recording the wrong input.
    summary = _record(tmp_path, [np.full(48_000, 1e-5)])
    assert summary.silent
    assert not summary.usable
    assert "silent" in summary.verdict()
    assert "receive device" in summary.verdict()


def test_a_clipping_capture_says_to_turn_it_down_and_repeat(tmp_path) -> None:
    loud = np.concatenate([np.full(500, 0.999), np.full(500, 0.2)])
    summary = _record(tmp_path, [loud])

    assert summary.clipping
    assert summary.clipped_samples == 500
    assert not summary.usable
    assert "clipping" in summary.verdict()
    assert "record again" in summary.verdict()


def test_a_very_quiet_capture_is_usable_but_says_so(tmp_path) -> None:
    rng = np.random.default_rng(SEED)
    summary = _record(tmp_path, [rng.normal(0.0, 0.002, 48_000)])
    assert not summary.silent
    assert "very quiet" in summary.verdict()


def test_clipping_is_counted_just_below_the_rail(tmp_path) -> None:
    # A path driven into its limit rounds off before it reaches full scale; by
    # the time samples are at 1.0 the damage has been done for a while.
    assert 0.9 < CLIP_THRESHOLD < 1.0
    summary = _record(tmp_path, [np.full(10, CLIP_THRESHOLD + 0.005)])
    assert summary.clipped_samples == 10


def test_an_empty_capture_says_nothing_was_recorded(tmp_path) -> None:
    summary = _record(tmp_path, [])
    assert summary.samples == 0
    assert not summary.usable
    assert summary.verdict() == "nothing was recorded"
    assert np.isnan(summary.crest_factor_db)


def test_the_log_line_names_the_file_and_the_levels(tmp_path) -> None:
    lines = []
    recorder = WavRecorder(tmp_path / "capture.wav",
                           sample_rate=DEFAULT_SAMPLE_RATE, on_log=lines.append)
    recorder.start()
    recorder.write(np.random.default_rng(SEED).normal(0.0, 0.1, 48_000))
    summary = recorder.stop()

    assert any("capture.wav" in line for line in lines)
    assert "dBFS" in summary.summary()
    assert "crest" in summary.summary()
    assert summary.summary().isascii()


# -- lifecycle --------------------------------------------------------------- #

def test_writing_before_start_and_after_stop_is_ignored(tmp_path) -> None:
    # `write` runs on the PortAudio callback thread, where an exception would take
    # the whole receive path down with it.
    recorder = WavRecorder(tmp_path / "capture.wav", sample_rate=48_000)
    recorder.write(np.ones(100))          # before start
    recorder.start()
    recorder.write(np.ones(100))
    recorder.stop()
    recorder.write(np.ones(100))          # after stop

    data, _, _ = _read(tmp_path / "capture.wav")
    assert len(data) == 100


def test_starting_twice_is_refused_rather_than_truncating_the_file(tmp_path) -> None:
    recorder = WavRecorder(tmp_path / "capture.wav", sample_rate=48_000)
    recorder.start()
    try:
        with pytest.raises(RuntimeError):
            recorder.start()
    finally:
        recorder.stop()


def test_stopping_twice_returns_the_same_summary(tmp_path) -> None:
    recorder = WavRecorder(tmp_path / "capture.wav", sample_rate=48_000)
    recorder.start()
    recorder.write(np.ones(500) * 0.5)
    first = recorder.stop()
    again = recorder.stop()
    assert first.samples == again.samples == 500


def test_live_state_tracks_what_has_been_captured(tmp_path) -> None:
    recorder = WavRecorder(tmp_path / "capture.wav", sample_rate=48_000)
    assert not recorder.active
    recorder.start()
    assert recorder.active
    recorder.write(np.full(24_000, 0.4))
    # The writer thread drains asynchronously; stopping flushes it.
    summary = recorder.stop()
    assert not recorder.active
    assert summary.samples == 24_000
    assert recorder.seconds == pytest.approx(0.5, abs=0.01)
    assert recorder.level() == pytest.approx(0.4, abs=1e-6)


def test_the_directory_is_created_if_it_is_not_there(tmp_path) -> None:
    target = tmp_path / "captures" / "nested" / "capture.wav"
    recorder = WavRecorder(target, sample_rate=48_000)
    assert recorder.start() == target
    recorder.write(np.zeros(10))
    recorder.stop()
    assert target.is_file()


def test_a_write_failure_is_logged_rather_than_killing_the_thread(tmp_path) -> None:
    lines = []
    recorder = WavRecorder(tmp_path / "capture.wav", sample_rate=48_000,
                           on_log=lines.append)
    recorder.start()

    class Exploding:
        def writeframes(self, data):
            raise OSError("disk full")

        def close(self):
            pass

    recorder._handle = Exploding()
    recorder.write(np.ones(100))
    recorder.stop()
    assert any("write failed" in line for line in lines)


# -- names ------------------------------------------------------------------- #

def test_capture_names_sort_in_the_order_they_happened(tmp_path) -> None:
    early = capture_path(tmp_path, datetime(2026, 8, 8, 9, 5, 1))
    later = capture_path(tmp_path, datetime(2026, 8, 8, 17, 30, 2))
    assert early.name == "capture-20260808-090501.wav"
    assert early.suffix == ".wav"
    assert sorted([later.name, early.name]) == [early.name, later.name]


# -- the whole point: a capture the offline tools can decode ----------------- #

def test_a_recorded_burst_decodes_back_to_the_bytes_that_were_sent(tmp_path) -> None:
    # The reason this feature exists. Record what the receiver heard, hand the
    # file to the modem afterwards, and get the payload back -- which is what
    # makes an on-air session analysable long after the radios are away.
    rng = np.random.default_rng(SEED)
    payload = rng.integers(0, 256, 256, dtype=np.uint8).tobytes()
    header = PhyHeader(OfdmFrameType.DATA, 99, mcs=1, payload_len=len(payload))
    aired = Channel(BENCH, ChannelSpec(snr_db=18.0, delay=4000, trailing=4000),
                    seed=1)(build_burst(BENCH, header, payload))

    recorder = WavRecorder(tmp_path / "capture.wav", sample_rate=BENCH.sample_rate)
    recorder.start()
    block = int(BENCH.sample_rate * 0.1)
    for start in range(0, len(aired), block):
        recorder.write(aired[start: start + block])
    summary = recorder.stop()

    assert summary.usable, summary.verdict()
    data, rate, _ = _read(summary.path)
    assert rate == BENCH.sample_rate
    decoded = decode_burst(BENCH, data)

    assert decoded.payload == payload
    assert decoded.metrics.snr_db == pytest.approx(18.0, abs=2.0)


def test_a_capture_of_nothing_but_noise_yields_no_payload(tmp_path) -> None:
    rng = np.random.default_rng(SEED)
    recorder = WavRecorder(tmp_path / "noise.wav", sample_rate=BENCH.sample_rate)
    recorder.start()
    recorder.write(rng.normal(0.0, 0.05, 2 * BENCH.sample_rate))
    recorder.stop()

    data, _, _ = _read(tmp_path / "noise.wav")
    decoded = decode_burst(BENCH, data)
    assert decoded.payload is None
    assert decoded.metrics.error


# -- the standalone capture stream ------------------------------------------- #

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

    def feed(self, samples) -> None:
        block = np.asarray(samples, dtype=np.float32).reshape(-1, 1)
        self.callback(block, len(block), None, None)


class FakeSounddevice:
    def __init__(self) -> None:
        self.streams: list[FakeStream] = []
        self.checked: list[dict] = []

    def check_input_settings(self, **kwargs) -> None:
        self.checked.append(kwargs)

    def InputStream(self, **kwargs):  # noqa: N802 - mirrors sounddevice
        stream = FakeStream(kwargs.get("callback"))
        self.streams.append(stream)
        return stream


def test_a_standalone_capture_feeds_the_recorder(tmp_path, monkeypatch) -> None:
    # The case that matters for the first on-air step: a station brought up only
    # to record what the other end transmits, with no control channel running.
    sd = FakeSounddevice()
    monkeypatch.setattr("guardian.modem.audio._import_sounddevice", lambda: sd)
    recorder = WavRecorder(tmp_path / "capture.wav", sample_rate=48_000)
    recorder.start()
    capture = AudioCapture(recorder, device=3, sample_rate=48_000)
    capture.start()

    assert sd.checked[0]["device"] == 3
    assert sd.checked[0]["samplerate"] == 48_000
    sd.streams[0].feed(np.full(4800, 0.25))
    capture.stop()
    summary = recorder.stop()

    assert summary.samples == 4800
    assert summary.peak == pytest.approx(0.25, abs=1e-3)
    assert sd.streams[0].closed


def test_a_capture_refuses_a_device_that_did_not_resolve(tmp_path, monkeypatch) -> None:
    # resolve_device hands back the name it was given when it cannot find it, so
    # anything that is not an int means the device is not there.
    sd = FakeSounddevice()
    monkeypatch.setattr("guardian.modem.audio._import_sounddevice", lambda: sd)
    recorder = WavRecorder(tmp_path / "capture.wav", sample_rate=48_000)
    capture = AudioCapture(recorder, device="No such microphone", sample_rate=48_000)

    with pytest.raises(RuntimeError, match="RX audio input"):
        capture.start()
    assert sd.streams == []


def test_stopping_a_capture_that_never_started_does_not_raise(tmp_path) -> None:
    recorder = WavRecorder(tmp_path / "capture.wav", sample_rate=48_000)
    capture = AudioCapture(recorder, device=1, sample_rate=48_000)
    capture.stop()
    capture.stop()


# -- Operations: which audio source, and when it must refuse ----------------- #

def _operations(tmp_path, **overrides):
    from guardian.config import StationConfig
    from guardian.message import MessageStore
    from guardian.operations import Operations
    from guardian.routing import HeardStations, RouteTable
    from guardian.services import EventBus, SnapshotStore, WorkerPool

    config = StationConfig(callsign="OK7PS", radio_backend="none", **overrides)
    workers = WorkerPool(max_workers=1)
    operations = Operations(
        config, EventBus(), SnapshotStore(), workers,
        MessageStore(tmp_path / "mail"), RouteTable(), HeardStations(),
    )
    return operations, workers


def test_operations_taps_the_control_stream_when_one_is_open(tmp_path, monkeypatch):
    # One handle on the device is less to go wrong, and it guarantees the capture
    # is exactly the audio the modem is working from.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    operations, workers = _operations(tmp_path)
    try:
        transport = AudioControlTransportStub()
        operations.audio_transport = transport

        path = operations.start_recording()
        assert path is not None
        assert operations.recording_active()
        assert transport.on_audio is not None, "the control stream was not tapped"

        transport.on_audio(np.full(4800, 0.3))
        summary = operations.stop_recording()

        assert transport.on_audio is None, "the tap was left attached"
        assert summary.samples == 4800
        assert summary.peak == pytest.approx(0.3, abs=1e-3)
        assert not operations.recording_active()
        assert path.is_file()
    finally:
        workers.close()


def test_operations_opens_its_own_stream_when_the_control_channel_is_shut(
    tmp_path, monkeypatch
):
    # The first step of an on-air test: a station brought up only to record what
    # the other end transmits, with no control channel running at all.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    sd = FakeSounddevice()
    monkeypatch.setattr("guardian.modem.audio._import_sounddevice", lambda: sd)
    monkeypatch.setattr("guardian.operations.resolve_device", lambda name, kind: 4)
    operations, workers = _operations(tmp_path, audio_input="Radio codec")
    try:
        assert operations.audio_transport is None
        assert operations.start_recording() is not None
        assert sd.streams, "no input stream was opened"

        sd.streams[0].feed(np.full(2400, 0.2))
        summary = operations.stop_recording()

        assert summary.samples == 2400
        assert sd.streams[0].closed, "the stream was left open"
    finally:
        workers.close()


def test_operations_refuses_when_no_receive_device_is_selected(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    operations, workers = _operations(tmp_path, audio_input="")
    try:
        assert operations.start_recording() is None
        assert not operations.recording_active()
        # Nothing half-created: no stray empty file left behind.
        captures = tmp_path / "Guardian-G2" / "captures"
        assert not captures.exists() or not list(captures.glob("*.wav"))
    finally:
        workers.close()


def test_operations_refuses_while_a_payload_transfer_owns_the_codec(
    tmp_path, monkeypatch
):
    # The payload transport has exclusive use of the sound card; recording would
    # either fail to open the device or steal it mid-transfer.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    operations, workers = _operations(tmp_path, audio_input="Radio codec")
    try:
        operations._payload_active.set()
        assert operations.payload_active()
        assert operations.start_recording() is None
        assert not operations.recording_active()
    finally:
        workers.close()


def test_starting_twice_returns_the_capture_already_in_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    operations, workers = _operations(tmp_path)
    try:
        operations.audio_transport = AudioControlTransportStub()
        first = operations.start_recording()
        assert operations.start_recording() == first
        operations.stop_recording()
    finally:
        workers.close()


def test_stopping_when_nothing_is_recording_is_harmless(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    operations, workers = _operations(tmp_path)
    try:
        assert operations.stop_recording() is None
        assert operations.recording_seconds() == 0.0
        assert operations.recording_level() == 0.0
        assert operations.recording_path() is None
    finally:
        workers.close()


class AudioControlTransportStub:
    """The parts of AudioControlTransport the codec-handoff paths touch.

    Not just the recording ones: `stop_control_channel` and `_suspend_control`
    both reach into the transport on their way past, and a stub that only knew
    about `on_audio` would fail there for reasons that have nothing to do with
    what is being tested.
    """

    def __init__(self, sample_rate: int = 48_000) -> None:
        self.fs = sample_rate
        self.on_audio = None
        self.actual_input_device_name = "Radio codec"
        self.stopped = False

    def wait_tx_idle(self, timeout: float = 5.0) -> bool:
        return True

    def start(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def test_a_tapped_recording_is_closed_when_the_control_channel_stops(
    tmp_path, monkeypatch
):
    # It would otherwise keep looking like it was recording while receiving
    # nothing: the file stays open, the UI still says "recording", and only the
    # elapsed time quietly stops advancing.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    operations, workers = _operations(tmp_path)
    try:
        transport = AudioControlTransportStub()
        operations.audio_transport = transport
        path = operations.start_recording()
        transport.on_audio(np.full(4800, 0.3))

        operations.stop_control_channel()

        assert not operations.recording_active()
        assert path.is_file()
        with wave.open(str(path), "rb") as handle:
            assert handle.getnframes() == 4800, "the file was left incomplete"
    finally:
        workers.close()


def test_a_tapped_recording_is_closed_when_a_transfer_takes_the_codec(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    operations, workers = _operations(tmp_path)
    try:
        transport = AudioControlTransportStub()
        operations.audio_transport = transport
        operations.start_recording()
        transport.on_audio(np.full(2400, 0.25))

        operations._suspend_control()

        assert not operations.recording_active()
        assert operations.payload_active()
    finally:
        workers.close()


def test_a_recording_on_its_own_stream_survives_the_control_channel_stopping(
    tmp_path, monkeypatch
):
    # Its audio source is not the control stream, so there is nothing to lose.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    sd = FakeSounddevice()
    monkeypatch.setattr("guardian.modem.audio._import_sounddevice", lambda: sd)
    monkeypatch.setattr("guardian.operations.resolve_device", lambda name, kind: 4)
    operations, workers = _operations(tmp_path, audio_input="Radio codec")
    try:
        operations.start_recording()
        operations.stop_control_channel()
        assert operations.recording_active()
        sd.streams[0].feed(np.full(1200, 0.2))
        assert operations.stop_recording().samples == 1200
    finally:
        workers.close()
