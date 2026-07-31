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


def test_signal_to_noise_is_estimated_from_the_burst_against_the_idle_floor() -> None:
    # The heard-stations table had an SNR column that nothing ever filled: no
    # modem reports one, so it has to come from the receive audio itself.
    transport = AudioControlTransport(sample_rate=48_000)
    quiet = 0.01
    window = np.full(48_000, quiet, dtype=np.float32)
    window[:12_000] = 0.1                      # a burst in the first quarter

    assert transport.window_snr(window) is None, "no floor yet, no number"

    transport._floor = quiet
    # The floor is only worth dividing by once the tracker has heard enough
    # audio to have settled on it.
    assert transport.window_snr(window) is None, "floor has not settled yet"
    transport._floor_seconds = audio_module.SNR_FLOOR_SETTLE_SECONDS

    snr = transport.window_snr(window)
    assert snr is not None
    assert 19.0 < snr < 21.0, snr               # 0.1 over 0.01 is 20 dB

    # Nothing but noise reads as no margin, never as a negative signal report.
    assert transport.window_snr(np.full(48_000, quiet, dtype=np.float32)) == 0.0
    # Too little audio to judge.
    assert transport.window_snr(np.zeros(100, dtype=np.float32)) is None


def test_a_squelched_receiver_cannot_produce_an_80_db_signal_report() -> None:
    # OK7PS's log: "S/N ~78.7 dB" on a frame the same session otherwise scored
    # around 40 dB. A squelched FM receiver delivers digital silence, so the
    # floor tracker (which chases the minimum) collapses toward its own 1e-5
    # term and every later burst divides by nothing.
    transport = AudioControlTransport(sample_rate=48_000)
    transport._floor = 3.8e-5                  # measured from that station
    transport._floor_seconds = audio_module.SNR_FLOOR_SETTLE_SECONDS
    window = np.full(48_000, 1e-5, dtype=np.float32)
    window[:12_000] = 0.33                     # his peak level

    snr = transport.window_snr(window)

    assert snr == audio_module.SNR_MAX_DB, snr
    assert snr < 50.0, "silence is not a noise measurement"

    # The cap alone is not enough: a *weak* burst against a collapsed floor
    # would otherwise still report the ceiling. The noise reference is clamped
    # to a level below any real receiver but far above digital silence.
    weak = np.full(48_000, 1e-5, dtype=np.float32)
    weak[:12_000] = 1e-3
    assert transport.window_snr(weak) == 20.0, "1e-3 over the 1e-4 clamp"


def test_the_frame_snr_travels_with_the_frame_to_the_orchestrator() -> None:
    # pump() runs on the owner's thread, so the estimate has to be attached to
    # the frame being delivered -- not to whatever the meter reads later.
    transport = AudioControlTransport(sample_rate=48_000)
    seen: list[tuple[str, float | None]] = []
    transport.on_frame = lambda frame: seen.append(
        (frame.source, transport.last_frame_snr)
    )
    transport._rx_frames.append((ControlFrame(FrameType.BEACON, source="OK7PS"), 8.5))
    transport._rx_frames.append((ControlFrame(FrameType.BEACON, source="OK2IPW"), None))

    assert transport.pump() == 2
    assert seen == [("OK7PS", 8.5), ("OK2IPW", None)]
    assert transport.last_frame_snr is None, "a stale reading must not stick"


def _fake_sounddevice(devices, *, default_hostapi=0, unreadable=(), hostapis=None):
    """A PortAudio stand-in that can misbehave the way Windows does."""

    def query_devices(index=None, kind=None):
        if index is None:
            return [dict(d, index=i) for i, d in enumerate(devices)]
        if index in unreadable:
            raise UnicodeDecodeError(
                "utf-8", b"\xe1", 0, 1, "invalid start byte"
            )
        return dict(devices[index], index=index)

    return SimpleNamespace(
        _check=lambda value: value,
        _lib=SimpleNamespace(Pa_GetDeviceCount=lambda: len(devices)),
        query_devices=query_devices,
        query_hostapis=lambda: hostapis or [{"name": "MME", "devices": [0]}],
        get_portaudio_version=lambda: (1246976, "PortAudio V19.7.0"),
        default=SimpleNamespace(hostapi=default_hostapi),
        _terminate=lambda: None,
        _initialize=lambda: None,
    )


def test_one_undecodable_device_no_longer_blanks_the_whole_picker(
    monkeypatch,
) -> None:
    # Reported from a Czech Windows PC: Guardian showed no audio devices at
    # all while VARA showed them fine. sounddevice decodes each device name
    # and re-raises UnicodeDecodeError for host APIs other than
    # MME/DirectSound/ASIO -- and WDM-KS hands back the local ANSI code page.
    # Asking for the whole list at once meant one diacritic in one endpoint
    # emptied the picker.
    monkeypatch.setitem(
        sys.modules, "sounddevice", _fake_sounddevice(DEVICES, unreadable={0})
    )

    scan = audio_module.scan_audio_devices()

    assert scan.inputs == ["Mikrofon (USB Audio CODEC)"]
    assert scan.outputs == ["Speakers (USB Audio CODEC)"]
    assert scan.ok
    report = audio_module.audio_backend_report()
    assert len(report["unreadable_devices"]) == 1
    assert "UnicodeDecodeError" in report["unreadable_devices"][0]


def test_a_missing_audio_backend_is_reported_not_swallowed(monkeypatch) -> None:
    # An empty list used to be indistinguishable from a crashed backend, so
    # the operator was sent hunting through Windows privacy for a missing DLL.
    import builtins

    real_import = builtins.__import__

    def explode(name, *args, **kwargs):
        if name == "sounddevice":
            raise ImportError("DLL load failed: libportaudio64bit.dll")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    monkeypatch.setattr(builtins, "__import__", explode)

    scan = audio_module.scan_audio_devices()

    assert scan.inputs == [] and scan.outputs == []
    assert not scan.ok
    assert "could not be loaded" in scan.error
    assert "libportaudio64bit.dll" in scan.error


def test_a_backend_that_reports_nothing_says_how_many_it_saw(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "sounddevice", _fake_sounddevice([]))

    scan = audio_module.scan_audio_devices()

    assert not scan.ok
    assert "no usable device" in scan.error
    assert "0 endpoint" in scan.error


def test_each_direction_falls_back_across_host_apis_on_its_own(
    monkeypatch,
) -> None:
    # A default host API that exposes playback but no capture used to hide
    # every microphone on the PC: the fallback only ran when *both* were empty.
    devices = [
        {
            "name": "Speakers (USB Audio CODEC)",
            "hostapi": 0,
            "max_input_channels": 0,
            "max_output_channels": 2,
        },
        {
            "name": "Mikrofon (USB Audio CODEC)",
            "hostapi": 1,
            "max_input_channels": 1,
            "max_output_channels": 0,
        },
    ]
    monkeypatch.setitem(
        sys.modules, "sounddevice", _fake_sounddevice(devices, default_hostapi=0)
    )

    scan = audio_module.scan_audio_devices()

    assert scan.outputs == ["Speakers (USB Audio CODEC)"]
    assert scan.inputs == ["Mikrofon (USB Audio CODEC)"], "found on another API"


def test_a_rescan_asks_portaudio_to_look_at_the_hardware_again(
    monkeypatch,
) -> None:
    # PortAudio enumerates once and hands out that snapshot forever, so a
    # codec plugged in after Guardian started could never appear however often
    # Refresh was pressed.
    calls: list[str] = []
    fake = _fake_sounddevice(DEVICES)
    fake._terminate = lambda: calls.append("terminate")
    fake._initialize = lambda: calls.append("initialize")
    monkeypatch.setitem(sys.modules, "sounddevice", fake)

    audio_module.scan_audio_devices()
    assert calls == [], "an ordinary listing must not disturb a running stream"

    scan = audio_module.scan_audio_devices(reinitialise=True)
    assert calls == ["terminate", "initialize"]
    assert scan.reinitialised and scan.ok


def test_the_portaudio_build_is_chosen_for_the_process_not_the_machine(
    monkeypatch,
) -> None:
    # OK2IPW's PC, 0.6.41 diagnostics: Windows on ARM running the x64 build
    # under emulation. platform.machine() resolves PROCESSOR_ARCHITEW6432
    # first, so it says ARM64, and sounddevice went looking for
    # libportaudioarm64.dll -- a file no x64 wheel ships and an emulated x64
    # process could not load anyway. Result: error 0x7e and not one audio
    # device anywhere in Guardian.
    import builtins
    import platform

    seen: list[str] = []
    fake = _fake_sounddevice(DEVICES)
    real_import = builtins.__import__

    def capture(name, *args, **kwargs):
        if name == "sounddevice":
            seen.append(platform.machine())
            return fake
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "AMD64")
    monkeypatch.setattr(platform, "machine", lambda: "ARM64")
    monkeypatch.setattr(audio_module, "_platform", platform)
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    monkeypatch.setattr(builtins, "__import__", capture)

    assert audio_module._import_sounddevice() is fake
    assert seen == ["AMD64"], "sounddevice must see the process architecture"
    assert platform.machine() == "ARM64", "and the patch must not linger"


def test_a_native_machine_is_left_completely_alone(monkeypatch) -> None:
    # The override exists only for the emulated case; an ordinary x64 PC (or
    # any non-Windows host) must import exactly as before.
    import builtins
    import platform

    seen: list[str] = []
    fake = _fake_sounddevice(DEVICES)
    real_import = builtins.__import__

    def capture(name, *args, **kwargs):
        if name == "sounddevice":
            seen.append(platform.machine())
            return fake
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "AMD64")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(audio_module, "_platform", platform)
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    monkeypatch.setattr(builtins, "__import__", capture)

    audio_module._import_sounddevice()

    assert seen == ["AMD64"]


def test_diagnostics_name_both_architectures(monkeypatch) -> None:
    # The pair is what identifies an emulated process at a glance.
    monkeypatch.setitem(sys.modules, "sounddevice", _fake_sounddevice(DEVICES))

    report = audio_module.audio_backend_report()

    assert "process_architecture" in report
    assert "machine_architecture" in report
