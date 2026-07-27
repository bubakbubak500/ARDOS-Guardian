import sys
import threading
from types import SimpleNamespace
import wave

import numpy as np

import guardian.modem.audio as audio_module
from guardian.modem.audio import (
    AudioControlTransport,
    is_real_audio_device_name,
    match_device_index,
    match_device_name,
)
from guardian.protocol import ControlFrame, FrameType


DEVICES = [
    {
        "name": "Microphone Array (Internal Audio)",
        "hostapi": 0,
        "max_input_channels": 2,
        "max_output_channels": 0,
    },
    {
        "name": "Mikrofon (USB Audio CODEC)",
        "hostapi": 0,
        "max_input_channels": 1,
        "max_output_channels": 0,
    },
    {
        "name": "Speakers (USB Audio CODEC)",
        "hostapi": 0,
        "max_input_channels": 0,
        "max_output_channels": 2,
    },
]


def test_device_match_tolerates_saved_trailing_parenthesis_space() -> None:
    assert (
        match_device_index(
            DEVICES,
            "Mikrofon (USB Audio CODEC )",
            "input",
            default_api=0,
        )
        == 1
    )
    assert (
        match_device_name(
            ["Mikrofon (USB Audio CODEC)"],
            "Mikrofon (USB Audio CODEC )",
        )
        == "Mikrofon (USB Audio CODEC)"
    )


def test_device_match_respects_input_output_direction() -> None:
    assert (
        match_device_index(
            DEVICES,
            "Speakers (USB Audio CODEC)",
            "output",
            default_api=0,
        )
        == 2
    )


def test_device_match_refuses_ambiguous_hardware_identity() -> None:
    devices = DEVICES + [
        {
            "name": "Line (USB Audio CODEC)",
            "hostapi": 0,
            "max_input_channels": 1,
            "max_output_channels": 0,
        }
    ]
    assert (
        match_device_index(
            devices,
            "USB Audio CODEC",
            "input",
            default_api=0,
        )
        is None
    )


def test_windows_mapper_alias_is_not_presented_as_a_real_device() -> None:
    assert not is_real_audio_device_name("Microsoft Sound Mapper - Input")
    assert not is_real_audio_device_name("Primary Sound Capture Driver")
    assert is_real_audio_device_name("Mikrofon (USB Audio CODEC)")


def test_control_transport_reports_the_endpoint_portaudio_actually_opened(
    monkeypatch,
) -> None:
    class Stream:
        def __init__(self, *, device, **_kwargs):
            self.device = device

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    names = {
        4: "Mikrofon (USB Audio CODEC)",
        7: "Reproduktory (USB Audio CODEC)",
    }
    fake_sd = SimpleNamespace(
        InputStream=Stream,
        query_devices=lambda index, _kind: {"name": names[index]},
        check_input_settings=lambda **_kwargs: None,
        check_output_settings=lambda **_kwargs: None,
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    modem = SimpleNamespace(
        name="test modem",
        demodulate=lambda _samples: [],
    )
    transport = AudioControlTransport(
        modem=modem,
        input_device=4,
        output_device=7,
    )
    try:
        transport.start()
        assert transport.actual_input_device_index == 4
        assert transport.actual_input_device_name == names[4]
        assert transport.actual_output_device_index == 7
        assert transport.actual_output_device_name == names[7]
    finally:
        transport.stop()


def test_control_transport_reports_pending_tx_until_playback_finishes() -> None:
    playback_started = threading.Event()
    release_playback = threading.Event()

    class SoundDevice:
        @staticmethod
        def play(_samples, **_kwargs):
            playback_started.set()

        @staticmethod
        def wait():
            release_playback.wait(2.0)

    modem = SimpleNamespace(
        name="afsk1200",
        modulate=lambda _payload: np.zeros(32, dtype=np.float32),
    )
    transport = AudioControlTransport(
        modem=modem,
        input_device=4,
        output_device=7,
    )
    transport._sd = SoundDevice()

    transport.send(ControlFrame(FrameType.BEACON, source="OK7PS"))

    assert playback_started.wait(1.0)
    assert not transport.wait_tx_idle(timeout=0.01)
    release_playback.set()
    assert transport.wait_tx_idle(timeout=1.0)


def test_control_transport_keeps_ptt_keyed_for_usb_audio_tail(monkeypatch) -> None:
    sleeps: list[float] = []
    ptt: list[bool] = []

    class SoundDevice:
        @staticmethod
        def play(_samples, **_kwargs):
            return None

        @staticmethod
        def wait():
            return None

    monkeypatch.setattr(audio_module.time, "sleep", sleeps.append)
    modem = SimpleNamespace(
        name="afsk1200",
        modulate=lambda _payload: np.zeros(32, dtype=np.float32),
    )
    transport = AudioControlTransport(
        modem=modem,
        ptt=ptt.append,
        input_device=4,
        output_device=7,
    )
    transport._sd = SoundDevice()

    transport.send(ControlFrame(FrameType.BEACON, source="OK7PS"))

    assert transport.wait_tx_idle(timeout=1.0)
    assert ptt == [True, False]
    assert sleeps == [
        audio_module.PTT_LEAD_SECONDS,
        audio_module.PTT_TAIL_SECONDS,
    ]


def test_control_transport_saves_failed_receive_audio(tmp_path) -> None:
    path = tmp_path / "last-bad-control.wav"
    transport = AudioControlTransport(
        diagnostic_audio_path=path,
        sample_rate=48_000,
    )
    samples = np.linspace(-0.25, 0.25, 48_000, dtype=np.float32)

    transport._save_bad_audio(samples)

    assert path.exists()
    with wave.open(str(path), "rb") as recording:
        assert recording.getnchannels() == 1
        assert recording.getsampwidth() == 2
        assert recording.getframerate() == 48_000
        assert recording.getnframes() == 48_000
