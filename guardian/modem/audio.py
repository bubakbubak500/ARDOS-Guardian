"""AudioControlTransport — bind a modem to a sound device + the radio PTT.

This is the real on-air control channel: it modulates outgoing control frames
to audio, keys PTT, and plays them; and it continuously captures RX audio,
demodulates it, and hands decoded ControlFrames to the orchestrator. It is a
drop-in `ControlTransport`, so the Phase-2 orchestrator uses it unchanged.

sounddevice (PortAudio) is imported lazily so the rest of Guardian runs on a PC
with no audio backend; start() raises a clear error if it can't be used.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform as _platform
import re
import sys
import threading
import time
from typing import Callable
import wave

import numpy as np

from ..protocol import MAX_CONTROL_FRAME_BYTES, ControlFrame, FrameError, FrameType
from ..session.transport import ControlTransport
from .afsk import AFSKModem, PREAMBLE
from .morse import modulate_morse, normalise_morse_text


_PSEUDO_DEVICE_PREFIXES = (
    "microsoft sound mapper",
    "primary sound capture driver",
    "primary sound driver",
)

#: What every audio path here runs at. It was a bare default on
#: AudioControlTransport until a second caller needed the same figure; a named
#: constant is better than two copies that can drift apart.
DEFAULT_SAMPLE_RATE = 48000

PTT_LEAD_SECONDS = 0.15
PTT_TAIL_SECONDS = 0.25
# Silence appended after every transmitted frame. Stopping the output stream
# discards whatever is still buffered in the host API / USB device, and on air
# that cost a constant ~130 ms off the end of every burst: measured 2026-07-29
# as the last ~16 symbols of three consecutive captures demodulating as pure
# noise while symbols 0-130 were error-free. (The same clip at the previous
# 32 ms/symbol rate damaged only the final byte -- both days fit one cause.)
# With the guard, what gets discarded is silence instead of the CRC.
TX_GUARD_SECONDS = 0.4


class _ControlTxCancelled(Exception):
    """An admitted control was stopped before it acquired the channel."""


class _AfskAcquisition:
    """Recognize a fresh control leader independently of audio gain/noise RMS.

    FM squelch can make a received signal quieter than idle noise. Looking for
    a level increase therefore misses precisely those bursts. The existing
    normalized Bell-202 correlator and 32 alternating symbols identify the
    protocol's acquisition sequence instead; unrelated carrier/noise levels
    alone do not occupy the control channel.
    """

    def __init__(self, modem: AFSKModem):
        self.modem = modem
        self.samples = deque(maxlen=int(modem.fs * 0.1))
        self.received_samples = 0
        self.last_acquisition_sample = -1

    def clear(self) -> None:
        self.samples.clear()

    def observe(self, block: np.ndarray, now: float) -> float | None:
        self.samples.extend(block)
        self.received_samples += len(block)
        samples = np.fromiter(self.samples, dtype=np.float32)
        step = self.modem.sps
        if len(samples) < step * 34:
            return None
        soft = self.modem._bit_stream(samples)
        axis = np.arange(len(soft))
        pattern = np.tile(np.array([1, -1], dtype=np.int8), 16)
        latest = -1.0
        # Across 32 symbols even a 1% clock mismatch is less than one bit.
        # Searching phase is enough for early acquisition; full decoding keeps
        # its existing finer clock search and CRC validation.
        for phase in np.linspace(0.0, 1.0, 10, endpoint=False):
            centers = (np.arange(int(len(soft) / step) - 1) + phase + 0.5) * step
            bits = np.where(np.interp(centers, axis, soft) > 0.0, 1, -1).astype(np.int8)
            matches = np.flatnonzero(np.correlate(bits, pattern, "valid") >= 28)
            if len(matches):
                latest = max(latest, float(centers[matches[-1] + 31] + step / 2))
        if latest < 0:
            return None
        position = self.received_samples - len(samples) + int(latest)
        if position <= self.last_acquisition_sample:
            return None
        self.last_acquisition_sample = position
        return now - max(0.0, len(samples) - latest) / self.modem.fs


def transmit_waveform(sd, samples, *, device, sample_rate: int,
                      ptt: Callable[[bool], None],
                      lead_seconds: float = PTT_LEAD_SECONDS,
                      tail_seconds: float = PTT_TAIL_SECONDS,
                      guard_seconds: float = TX_GUARD_SECONDS,
                      write_chunk_frames: int = 0,
                      before_play=None, after_release=None) -> float:
    """Key the radio, play a waveform, unkey. Returns the seconds it aired.

    The single place this discipline is written down. Three callers need it -- the
    control transport below, the OFDM payload pipe, and the test burst the Modem
    test workspace transmits -- and "the transmitter is always released" is not a
    property to maintain in three copies, because the copy that gets it wrong
    leaves a station keyed on a channel other people are using.

    The order matters and each part of it was paid for:

    * **Lead-in after keying.** A waveform that starts before the carrier does is
      a burst the far end cannot synchronise to.
    * **Drain before unkeying.** Blocking writes may leave samples queued in
      the host. Stream.stop() waits for their playback while PTT remains ON;
      the configured radio tail follows that completion.
    * **Open the output stream before PTT.** A transient Windows/WASAPI open
      failure must not spend even a short carrier-only key-up discovering that
      no waveform can be sent. Opening is retried briefly while the radio is
      still in receive.
    * **The tail sleep and the unkey in `finally`.** PortAudio returning means it
      has finished filling the endpoint, not that the radio has finished sending;
      and if playback raised, dropping PTT still has to happen.

    `before_play` and `after_release` are for a caller with buffers to clear on
    either side of its own transmission.
    """
    rate = int(sample_rate)
    guard = np.zeros(int(max(0.0, guard_seconds) * rate))
    waveform = np.concatenate([np.asarray(samples, dtype=np.float64), guard])
    chunk_frames = max(0, int(write_chunk_frames))
    stream = _open_output_stream(
        sd, device=device, sample_rate=rate,
        blocksize=chunk_frames,
    )
    stream_started = False
    try:
        if before_play is not None:
            before_play()
        ptt(True)
        time.sleep(max(0.0, lead_seconds))
        playback = waveform.astype(np.float32)
        if stream is None:
            # Compatibility path for small test doubles and older backends.
            sd.play(playback, samplerate=rate, device=device)
            sd.wait()
        else:
            framed = playback.reshape(-1, 1)
            step = chunk_frames or len(framed)
            # An active blocking stream with no writes starves during the PTT
            # lead. Its first write then reports that startup gap as an output
            # underflow, even when the actual waveform plays successfully.
            # Open before keying, but start only when samples are ready.
            stream.start()
            stream_started = True
            for offset in range(0, len(framed), step):
                underflowed = stream.write(framed[offset:offset + step])
                if underflowed:
                    raise RuntimeError("audio output underflow during transmit")
    finally:
        try:
            # Blocking writes only queue the final USB buffers. Drain while
            # keyed, then apply the radio's tail; silence is not a drain timer.
            if stream is not None:
                # Always stop an opened endpoint, including a partial start
                # failure. A cancelled wait / rejected PTT leaves it inactive:
                # ignore PortAudio's "already stopped" status in that case,
                # while a real playback drain failure must still propagate.
                stream.stop(ignore_errors=not stream_started)
        finally:
            try:
                time.sleep(max(0.0, tail_seconds))
                ptt(False)
            finally:
                try:
                    if after_release is not None:
                        after_release()
                finally:
                    if stream is not None:
                        stream.close()
    return len(waveform) / rate


def _open_output_stream(sd, *, device, sample_rate: int, blocksize: int = 0):
    """Open blocking playback before PTT, retrying transient host failures."""
    factory = getattr(sd, "OutputStream", None)
    if factory is None:
        return None
    for attempt in range(3):
        stream = None
        try:
            stream = factory(
                samplerate=sample_rate,
                channels=1,
                device=device,
                dtype="float32",
                blocksize=max(0, int(blocksize)),
            )
            return stream
        except Exception:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            if attempt == 2:
                raise
            time.sleep(0.10)
    return None


def is_real_audio_device_name(name: str) -> bool:
    """Exclude PortAudio aliases that are not physical Windows endpoints."""
    normalized = " ".join(name.casefold().split())
    return bool(normalized) and not normalized.startswith(_PSEUDO_DEVICE_PREFIXES)


def process_architecture() -> str:
    """The instruction set *this process* runs as, upper-case ("AMD64").

    Not the machine's: on Windows on ARM an x64 program runs under emulation,
    and Windows tells it so through PROCESSOR_ARCHITECTURE while advertising
    the native ARM64 in PROCESSOR_ARCHITEW6432.
    """
    return (os.environ.get("PROCESSOR_ARCHITECTURE", "") or "").strip().upper()


def _import_sounddevice():
    """Import sounddevice with the PortAudio build the *process* can load.

    sounddevice picks its bundled DLL from `platform.machine()`, and Python
    resolves that to PROCESSOR_ARCHITEW6432 first -- the *machine's* native
    architecture. On a Windows-on-ARM PC that is ARM64 even though Guardian
    itself is the x64 build running under emulation, so sounddevice asked for
    `libportaudioarm64.dll`: a file no x64 wheel ships, and one an emulated
    x64 process could not load even if it did. The import failed and every
    audio device on the PC vanished from the pickers (OK2IPW, 0.6.41
    diagnostics, error 0x7e = module not found).

    The process architecture is the right question, so answer that one while
    sounddevice decides, and put `platform.machine` back immediately.
    """
    machine = _platform.machine().strip().upper()
    process = process_architecture()
    if sys.platform != "win32" or not process or process == machine:
        import sounddevice

        return sounddevice
    original = _platform.machine
    _platform.machine = lambda: process
    try:
        import sounddevice
    finally:
        _platform.machine = original
    return sounddevice


@dataclass(frozen=True)
class AudioDeviceScan:
    """What an enumeration attempt found, and why it found nothing.

    An empty list used to be indistinguishable from a crashed backend: a
    missing PortAudio DLL, a stopped Windows Audio service and a genuinely
    silent PC all produced the same blank picker, so the operator had nothing
    to act on. The reason now travels with the result.
    """

    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    error: str = ""            # empty when the backend answered normally
    reinitialised: bool = False  # PortAudio re-scanned the hardware

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.inputs or self.outputs)


def reinitialise_audio_backend() -> str:
    """Make PortAudio look at the hardware again. Returns "" or an error.

    PortAudio enumerates devices once, when it initialises, and hands out the
    same snapshot forever after. A codec plugged in after Guardian started is
    therefore invisible however often the operator presses Refresh -- which is
    exactly the case where another program (started later) *does* see it. The
    documented cure is to terminate and re-initialise, which is what this does.

    The caller must be sure no stream is open: re-initialising underneath a
    running control channel would pull the device out from under it.
    """
    try:
        sd = _import_sounddevice()

        sd._terminate()
        sd._initialize()
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        return f"{type(exc).__name__}: {exc}"
    return ""


def _query_devices_one_by_one(sd) -> tuple[list[dict], list[str]]:
    """Enumerate device by device, so one bad entry cannot hide the rest.

    sounddevice decodes each device name itself, and for host APIs other than
    MME/DirectSound/ASIO a name that is not valid UTF-8 makes it re-raise
    `UnicodeDecodeError`. Windows in a non-English locale does produce such
    names -- WDM-KS in particular hands back the local ANSI code page, so a
    single device with a diacritic in it is enough. Asking for the whole list
    in one call meant that one undecodable entry blanked the picker
    completely, on a PC where every other program saw the codec perfectly
    well. Returns (devices, per-device failure descriptions).
    """
    count = None
    try:
        count = int(sd._check(sd._lib.Pa_GetDeviceCount()))
    except Exception:  # noqa: BLE001 - private API; fall back to the bulk call
        count = None
    if count is None:
        return [dict(device) for device in sd.query_devices()], []
    devices: list[dict] = []
    failures: list[str] = []
    for index in range(count):
        try:
            devices.append(dict(sd.query_devices(index)))
        except Exception as exc:  # noqa: BLE001 - skip it, keep the others
            failures.append(f"device #{index}: {type(exc).__name__}: {exc}")
    return devices, failures


def scan_audio_devices(*, reinitialise: bool = False) -> AudioDeviceScan:
    """Enumerate the usable audio endpoints, keeping any failure reason.

    Windows enumerates the *same* physical device once per host API (MME,
    WASAPI, DirectSound, WDM-KS) — and MME even truncates names to 31 chars —
    so one radio codec otherwise shows up 4+ times under slightly different
    names. We list devices from a single host API (the default one) so each
    real device appears exactly once, falling back per direction to a
    name-deduped list across every API when the default one has nothing.
    """
    reinit_error = reinitialise_audio_backend() if reinitialise else ""
    try:
        sd = _import_sounddevice()
    except Exception as exc:  # noqa: BLE001 - the backend is optional at import
        return AudioDeviceScan(
            error=(
                f"the audio backend could not be loaded ({type(exc).__name__}: "
                f"{exc})"
            ),
            reinitialised=reinitialise and not reinit_error,
        )
    try:
        devices, skipped = _query_devices_one_by_one(sd)
    except Exception as exc:  # noqa: BLE001 - PortAudio/host API failure
        return AudioDeviceScan(
            error=f"the audio backend reported an error ({type(exc).__name__}: {exc})",
            reinitialised=reinitialise and not reinit_error,
        )

    def collect(api_filter) -> tuple[list[str], list[str]]:
        ins: list[str] = []
        outs: list[str] = []
        seen_in: set[str] = set()
        seen_out: set[str] = set()
        for d in devices:
            if api_filter is not None and d.get("hostapi") != api_filter:
                continue
            name = (d.get("name", "") or "").strip()
            if not is_real_audio_device_name(name):
                continue
            if d.get("max_input_channels", 0) > 0 and name not in seen_in:
                seen_in.add(name)
                ins.append(name)
            if d.get("max_output_channels", 0) > 0 and name not in seen_out:
                seen_out.add(name)
                outs.append(name)
        return ins, outs

    try:
        default_api = sd.default.hostapi
    except Exception:
        default_api = None
    inputs, outputs = collect(default_api)
    if not inputs or not outputs:
        # Per direction, not both together: a host API that exposes playback
        # but no capture would otherwise hide every microphone on the PC.
        all_inputs, all_outputs = collect(None)
        inputs = inputs or all_inputs
        outputs = outputs or all_outputs

    error = reinit_error
    if not inputs and not outputs and not error:
        error = (
            f"the audio backend started but reported no usable device "
            f"({len(devices)} endpoint(s) seen)"
        )
        if skipped:
            error += f"; {len(skipped)} endpoint(s) unreadable: {skipped[0]}"
    return AudioDeviceScan(
        inputs=inputs,
        outputs=outputs,
        error=error,
        reinitialised=reinitialise and not reinit_error,
    )


def list_audio_devices() -> tuple[list[str], list[str]]:
    """Return (input_device_names, output_device_names). Empty if no backend."""
    scan = scan_audio_devices()
    return scan.inputs, scan.outputs


def audio_backend_report() -> dict:
    """Everything PortAudio will admit to, for the diagnostics export.

    Deliberately *unfiltered*: the question this has to answer is whether the
    backend sees nothing at all, or whether Guardian's own host-API choice and
    pseudo-device filter are hiding what it does see. A filtered list cannot
    tell those apart, and that is the case that had us guessing.
    """
    report: dict = {
        "backend": "sounddevice/PortAudio",
        # The pair that mattered on Windows on ARM: an x64 process on an ARM64
        # machine, where the wrong one picks a DLL that cannot be loaded.
        "process_architecture": process_architecture(),
        "machine_architecture": _platform.machine(),
    }
    try:
        sd = _import_sounddevice()
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
        return report
    try:
        report["portaudio_version"] = sd.get_portaudio_version()[1]
    except Exception as exc:  # noqa: BLE001
        report["portaudio_version_error"] = f"{type(exc).__name__}: {exc}"
    try:
        report["default_hostapi"] = sd.default.hostapi
    except Exception as exc:  # noqa: BLE001
        report["default_hostapi_error"] = f"{type(exc).__name__}: {exc}"
    try:
        report["host_apis"] = [
            {
                "index": index,
                "name": api.get("name"),
                "devices": len(api.get("devices", ())),
                "default_input": api.get("default_input_device"),
                "default_output": api.get("default_output_device"),
            }
            for index, api in enumerate(sd.query_hostapis())
        ]
    except Exception as exc:  # noqa: BLE001
        report["host_apis_error"] = f"{type(exc).__name__}: {exc}"
    try:
        devices, skipped = _query_devices_one_by_one(sd)
        report["devices"] = [
            {
                "index": device.get("index", index),
                "name": device.get("name"),
                "hostapi": device.get("hostapi"),
                "in": device.get("max_input_channels"),
                "out": device.get("max_output_channels"),
                "default_samplerate": device.get("default_samplerate"),
                "excluded_as_alias": not is_real_audio_device_name(
                    (device.get("name", "") or "").strip()
                ),
            }
            for index, device in enumerate(devices)
        ]
        # The interesting line when a picker is empty: which endpoints the
        # backend could not even describe, and why.
        report["unreadable_devices"] = skipped
    except Exception as exc:  # noqa: BLE001
        report["devices_error"] = f"{type(exc).__name__}: {exc}"
    scan = scan_audio_devices()
    report["guardian_sees"] = {
        "inputs": scan.inputs,
        "outputs": scan.outputs,
        "error": scan.error,
    }
    return report


def default_device_names() -> tuple[str | None, str | None]:
    """(default_input_name, default_output_name) for the default host API, or
    (None, None) if unavailable. Lets the UI warn when the radio codec is also
    the Windows default device — Windows would then mix system sounds into it
    and compete for the device (and put PC beeps on the air)."""
    try:
        sd = _import_sounddevice()
        devs = list(sd.query_devices())
        ha = sd.query_hostapis(sd.default.hostapi)
    except Exception:
        return None, None
    di = ha.get("default_input_device", -1)
    do = ha.get("default_output_device", -1)
    in_name = (devs[di].get("name", "") or "").strip() if 0 <= di < len(devs) else None
    out_name = (devs[do].get("name", "") or "").strip() if 0 <= do < len(devs) else None
    return in_name, out_name


def resolve_device(name: str, kind: str = "input"):
    """Resolve a device *name* to a unique device *index* on the default host
    API. Opening a stream by name is ambiguous on Windows because the same name
    exists under MME/DirectSound/WASAPI/WDM-KS; an index is unambiguous. Returns
    the int index, or the original name if it can't be resolved (let sd try)."""
    if not name:
        return None
    try:
        sd = _import_sounddevice()
        devices = list(sd.query_devices())
        default_api = sd.default.hostapi
    except Exception:
        return name
    match = match_device_index(devices, name, kind, default_api)
    if match is not None:
        return match
    return name


def audio_device_host_api(value, kind: str = "input") -> str:
    """Return the PortAudio host API used by one resolved endpoint.

    A saved Windows friendly name is not a complete audio-path identity: the
    same text is commonly exposed by MME, DirectSound, WASAPI and WDM-KS.  The
    APIs can have different gain and timing behaviour, so AutoTune records must
    not be reused after the same name resolves through a different API.  Keep
    the result stable and human-readable; ``unresolved`` deliberately prevents
    us from pretending that an API was measured when PortAudio cannot identify
    it.
    """
    if value in (None, ""):
        return "none"
    try:
        sd = _import_sounddevice()
        index = value if isinstance(value, int) else resolve_device(value, kind)
        if not isinstance(index, int):
            return "unresolved"
        device = sd.query_devices(index)
        api_index = int(device.get("hostapi", -1))
        host_apis = list(sd.query_hostapis())
        if 0 <= api_index < len(host_apis):
            name = str(host_apis[api_index].get("name") or "").strip()
            if name:
                return name
        return f"hostapi-{api_index}" if api_index >= 0 else "unresolved"
    except Exception:  # noqa: BLE001 - identity metadata must not break audio
        return "unresolved"


_GENERIC_DEVICE_WORDS = {
    "analog",
    "audio",
    "device",
    "input",
    "line",
    "mic",
    "microphone",
    "mikrofon",
    "output",
    "reproduktor",
    "reproduktory",
    "speakers",
    "stereo",
}


def _normalized_device_name(value: str) -> str:
    """Normalize harmless Windows/PortAudio name formatting differences."""
    return " ".join(re.findall(r"\w+", value.casefold(), flags=re.UNICODE))


def _device_identity_words(value: str) -> set[str]:
    return {
        word
        for word in _normalized_device_name(value).split()
        if word not in _GENERIC_DEVICE_WORDS and not word.isdigit()
    }


def audio_device_pair_identity(value: str) -> str | None:
    """Return a direction-neutral identity for one Windows audio endpoint.

    PortAudio enumerates capture and playback endpoints independently.  Their
    numeric indexes can therefore swap independently after a USB reconnect,
    even though Windows still exposes the matching endpoint instance in names
    such as ``Microphone (3 - USB Audio CODEC)`` and ``Speakers (3 - ...)``.
    Prefer that instance marker when present; otherwise retain the distinctive
    non-direction words as a conservative diagnostic identity.
    """
    text = str(value or "")
    instance = re.search(r"\(\s*(\d+)\s*-", text)
    if instance is not None:
        return f"windows-endpoint:{instance.group(1)}"
    words = sorted(_device_identity_words(text))
    return " ".join(words) if words else None


def match_device_index(
    devices: list[dict],
    name: str,
    kind: str = "input",
    default_api=None,
) -> int | None:
    """Match a saved device despite MME truncation/local formatting.

    Exact normalized matches win. A conservative identity-token fallback then
    handles changes such as a trailing space before ``)`` or Windows adding a
    numeric endpoint prefix while retaining a distinctive hardware name.
    """
    ch_key = "max_input_channels" if kind == "input" else "max_output_channels"
    target = _normalized_device_name(name)
    if not target:
        return None
    candidates = [
        (index, device)
        for index, device in enumerate(devices)
        if device.get(ch_key, 0) > 0
    ]
    candidates.sort(key=lambda item: item[1].get("hostapi") != default_api)

    for index, device in candidates:
        candidate = _normalized_device_name(device.get("name", "") or "")
        if candidate == target:
            return index

    target_words = _device_identity_words(name)
    ranked: list[tuple[float, int]] = []
    for index, device in candidates:
        candidate_name = device.get("name", "") or ""
        candidate = _normalized_device_name(candidate_name)
        if min(len(candidate), len(target)) >= 8 and (
            candidate in target or target in candidate
        ):
            ranked.append((1.0, index))
            continue
        candidate_words = _device_identity_words(candidate_name)
        shared = target_words & candidate_words
        union = target_words | candidate_words
        if len(shared) >= 2 and union:
            score = len(shared) / len(union)
            if score >= 0.6:
                ranked.append((score, index))
    if not ranked:
        return None
    ranked.sort(reverse=True)
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        return None
    return ranked[0][1]


def match_device_name(names: list[str], saved_name: str) -> str | None:
    """Return the current canonical spelling for an unambiguous saved name."""
    devices = [
        {
            "name": candidate,
            "hostapi": 0,
            "max_input_channels": 1,
            "max_output_channels": 0,
        }
        for candidate in names
    ]
    index = match_device_index(devices, saved_name, "input", default_api=0)
    return names[index] if index is not None else None


MIN_RX_WINDOW = 4.0      # seconds; what AFSK has always used
MIN_POLL_INTERVAL = 0.25  # seconds; likewise

# Neither modem reports a channel measurement, so the figure shown beside a
# heard station is estimated from the receive audio itself: the loudest quarter
# of the demodulated window is the burst, the slow-tracking idle level is the
# noise. It is therefore an (S+N)/N estimate of the audio the rig delivers --
# not a VARA or S-meter reading -- which is why the UI labels it an estimate.
SNR_BLOCK_SECONDS = 0.1
SNR_MIN_BLOCKS = 4
# Until the tracker has heard this much audio the floor is still whatever the
# first block happened to be -- a burst there would peg the floor at signal
# level and understate every later reading.
SNR_FLOOR_SETTLE_SECONDS = 2.0
# A squelched FM receiver delivers *digital silence*, so the floor tracker,
# which chases the minimum, collapses toward the 1e-5 term in its own update.
# Dividing a burst by that produced OK7PS's "S/N ~78.7 dB" on a frame the same
# session otherwise scored around 40 dB (measured floor: 3.8e-5). Silence is
# not a noise measurement: clamp the reference to a level below any real
# receiver noise but far above nothing at all, and cap the result, because
# beyond this much margin the number stops carrying information anyway.
SNR_MIN_FLOOR = 1e-4        # -80 dBFS
SNR_MAX_DB = 40.0


class AudioControlTransport(ControlTransport):
    def _modem_airtime(self, payload_bytes: int) -> float:
        """Longest frame this modem puts on air, or 0 for a modem without one."""
        airtime = getattr(self.modem, "airtime", None)
        return float(airtime(payload_bytes)) if airtime else 0.0

    def __init__(
        self,
        modem: AFSKModem | None = None,
        ptt: Callable[[bool], None] | None = None,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        input_device=None,
        output_device=None,
        diagnostic_audio_path: Path | str | None = None,
        on_log: Callable[[str], None] | None = None,
        tx_lead_seconds: float | None = None,
        tx_tail_seconds: float | None = None,
    ):
        self.modem = modem or AFSKModem(sample_rate=sample_rate)
        # Keep the 1.1.2 key-up/drain budget for session and routing controls:
        # losing a one-shot START_VARA or forwarded RREQ cannot be repaired by
        # an extended leader on an identical retransmission. Beacons alone can
        # use shorter edges because the next periodic beacon refreshes them.
        # A real AFSK modem advertises the extended-acquisition retry hook.
        # Keep duck-typed legacy modem fakes on the historical slow edges;
        # they may only expose ``name`` for logging and do not implement the
        # short-control timing contract.
        short_control = (
            getattr(self.modem, "name", "") == "afsk1200"
            and callable(getattr(self.modem, "modulate_retry", None))
        )
        self._short_beacon = short_control
        self.tx_lead_seconds = PTT_LEAD_SECONDS
        self.tx_tail_seconds = PTT_TAIL_SECONDS
        self.tx_guard_seconds = TX_GUARD_SECONDS
        if short_control and tx_lead_seconds is not None:
            self.tx_lead_seconds = max(0.0, float(tx_lead_seconds))
        if short_control and tx_tail_seconds is not None:
            self.tx_tail_seconds = max(0.0, float(tx_tail_seconds))
        self.fs = sample_rate
        self.ptt = ptt or (lambda on: None)
        self.input_device = input_device
        self.output_device = output_device
        self.diagnostic_audio_path = (
            Path(diagnostic_audio_path) if diagnostic_audio_path else None
        )
        self.on_log = on_log or (lambda m: None)
        self.on_frame = None
        #: Optional sink handed every received block, for recording the audio to
        #: a file. Set by `Operations.start_recording`; None the rest of the time.
        self.on_audio = None

        self._sd = None
        self._stream = None
        self.actual_input_device_name = ""
        self.actual_output_device_name = ""
        self.actual_input_device_index: int | None = None
        self.actual_output_device_index: int | None = None
        self._tx_lock = threading.Lock()
        # Serialization is not transmission: continue receiving while a queued
        # reply waits, modulates or opens its output device.
        self._transmitting = threading.Event()
        self._tx_condition = threading.Condition()
        self._pending_tx = 0
        self._next_tx_token = 0
        self._pending_frames: dict[int, ControlFrame | None] = {}
        self._cancelled_tx: set[int] = set()
        self._tx_context = threading.local()
        self._tx_failed = False
        self._tx_suspended = False
        self._stopped = False
        self._deferred_tx: list[tuple[str, object]] = []
        self._post_tx_pending = 0
        self._peer_ready_at = 0.0
        self._acquisition_ready_at = 0.0
        self._acquisition = _AfskAcquisition(self.modem) if isinstance(self.modem, AFSKModem) else None
        self._acquisition_hold = (
            self.modem.airtime(MAX_CONTROL_FRAME_BYTES)
            - len(PREAMBLE) * 8 / self.modem.baud
            + TX_GUARD_SECONDS + PTT_TAIL_SECONDS
            if self._acquisition is not None else 0.0
        )
        self._sent_control_frames: dict[bytes, float] = {}
        # The rolling window must hold one whole frame however slow the modem
        # is. A fixed 4 s was ample for AFSK's 1.2 s frames but silently swallowed
        # MFSK-16 once its geometry was corrected: a 6.9 s frame never fitted, so
        # nothing was ever attempted and not even a bad-frame line appeared.
        # Polling faster than a frame arrives only burns CPU, so that scales too.
        frame = self._modem_airtime(MAX_CONTROL_FRAME_BYTES)
        self.poll_interval = max(MIN_POLL_INTERVAL, frame / 8.0)
        self.rx_window = max(MIN_RX_WINDOW, frame + self.poll_interval + 1.0)
        self._rx_buf = deque(maxlen=int(sample_rate * self.rx_window))
        self._recent: dict[bytes, float] = {}              # payload -> last seen
        self._rx_frames: deque = deque()                   # (frame, snr), awaiting pump()
        # S/N estimate for the frame currently being handed to on_frame, so the
        # orchestrator can file it against the station it just heard.
        self.last_frame_snr: float | None = None
        self._running = False
        self._rx_thread: threading.Thread | None = None
        self._rx_stop = threading.Event()
        # Live RX metering (linear RMS in 0..1).
        self._level = 0.0          # smoothed current level
        self._floor = 0.0          # slow-tracking idle noise floor
        self._floor_seconds = 0.0  # audio heard since the floor started tracking
        self._peak = 0.0
        self._max_peak = 0.0
        self.input_status_events = 0
        self.last_input_status = ""
        self._last_diagnostic_audio = 0.0
        self.rejected_control_candidates = 0
        self.last_rejected_control: dict | None = None

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._running:
            return
        # A resumed control channel must not replay a pre-payload request.
        self._rx_buf.clear()
        self._rx_frames.clear()
        if self._acquisition is not None:
            self._acquisition.clear()
        self._rx_stop = threading.Event()
        sd = _import_sounddevice()  # lazy; raises if PortAudio missing
        self._sd = sd
        if not isinstance(self.input_device, int):
            raise RuntimeError("The configured RX audio input is not available")
        if not isinstance(self.output_device, int):
            raise RuntimeError("The configured TX audio output is not available")
        input_info = sd.query_devices(self.input_device, "input")
        output_info = sd.query_devices(self.output_device, "output")
        sd.check_input_settings(
            device=self.input_device,
            channels=1,
            dtype="float32",
            samplerate=self.fs,
        )
        sd.check_output_settings(
            device=self.output_device,
            channels=1,
            dtype="float32",
            samplerate=self.fs,
        )
        self._running = True
        try:
            self._stream = sd.InputStream(
                samplerate=self.fs, channels=1, dtype="float32",
                device=self.input_device, callback=self._rx_callback,
                blocksize=int(self.fs * 0.1),
            )
            self._stream.start()
        except Exception:
            self._running = False
            self._stream = None
            raise
        opened_index = getattr(self._stream, "device", self.input_device)
        if isinstance(opened_index, (tuple, list)):
            opened_index = opened_index[0]
        if int(opened_index) != self.input_device:
            self.stop()
            raise RuntimeError(
                f"PortAudio opened input #{opened_index}, expected #{self.input_device}"
            )
        self.actual_input_device_index = self.input_device
        self.actual_output_device_index = self.output_device
        self.actual_input_device_name = str(input_info["name"])
        self.actual_output_device_name = str(output_info["name"])
        self._rx_thread = threading.Thread(target=self._rx_loop,
                                           args=(self._rx_stop,),
                                           name="afsk-rx", daemon=True)
        self._rx_thread.start()
        self.on_log(
            f"Audio RX opened: {self.actual_input_device_name} "
            f"[PortAudio #{self.actual_input_device_index}]"
        )
        self.on_log(
            f"Audio TX verified: {self.actual_output_device_name} "
            f"[PortAudio #{self.actual_output_device_index}]"
        )
        self.on_log(f"Audio control channel started ({self.modem.name} @ {self.fs} Hz)")
        with self._tx_condition:
            self._stopped = False
        self._resume_queued_tx()

    def stop(self) -> None:
        # A user stop cancels held work; a payload suspension below preserves
        # it for resumption on the same codec after VARA releases the radio.
        with self._tx_condition:
            self._stopped = True
            self._tx_suspended = False
            self._deferred_tx.clear()
            self._cancelled_tx.update(self._pending_frames)
            self._tx_condition.notify_all()
        self._stop_rx()

    def _stop_rx(self) -> None:
        self._running = False
        self._rx_stop.set()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._rx_thread is not None and self._rx_thread is not threading.current_thread():
            self._rx_thread.join(timeout=2.0)
        self._rx_buf.clear()
        self._rx_frames.clear()

    def suspend(self, timeout: float = 8.0) -> bool:
        """Drain admitted controls and hold later sends during payload audio."""
        with self._tx_condition:
            self._tx_suspended = True
        try:
            if not self.wait_tx_idle(timeout):
                self._resume_queued_tx()
                return False
            self._stop_rx()
        except Exception:
            self._resume_queued_tx()
            raise
        return True

    def _resume_queued_tx(self) -> None:
        with self._tx_condition:
            self._tx_suspended = False
            held, self._deferred_tx = self._deferred_tx, []
        for kind, value in held:
            if kind == "frame":
                self.send(value)
            else:
                text, wpm = value
                self.send_morse_after_pending(text, wpm=wpm)

    def discard_deferred_message(
        self, message_id: int, *, discovery_query_id: int | None = None,
    ) -> None:
        """Remove held requests for a cancelled/failed message, preserving receipts."""
        request_types = {
            FrameType.HAVE_MSG, FrameType.ACK_HAVE, FrameType.START_VARA,
            FrameType.ROUTE_QUERY, FrameType.ROUTE_OFFER,
            FrameType.WORKING_OFFER, FrameType.WORKING_ACK,
            FrameType.G2_PROFILE_OFFER, FrameType.G2_PROFILE_ACK,
        }
        def cancelled(frame: ControlFrame) -> bool:
            return (
                (frame.message_id == message_id and frame.type in request_types)
                or (discovery_query_id is not None
                    and frame.message_id == discovery_query_id
                    and frame.type is FrameType.MULTIHOP_RREQ)
            )

        with self._tx_condition:
            self._deferred_tx = [
                (kind, value) for kind, value in self._deferred_tx
                if kind != "frame" or not cancelled(value)
            ]
            # A request can already have a worker yet still be waiting for a
            # busy channel. Cancel that admission too, without cancelling its
            # final receipts or a later, independently admitted request.
            self._cancelled_tx.update(token for token, frame in self._pending_frames.items()
                                      if frame is not None and cancelled(frame))
            self._tx_condition.notify_all()

    # ------------------------------------------------------------------ #
    #  Transmit                                                           #
    # ------------------------------------------------------------------ #
    def send(self, frame: ControlFrame) -> None:
        # TX off the caller's thread so the UI/orchestrator never blocks on PTT.
        with self._tx_condition:
            if self._stopped:
                return
            if self._tx_suspended:
                item = ("frame", frame)
                if item not in self._deferred_tx:
                    self._deferred_tx.append(item)
                return
            if not self._pending_tx:
                self._tx_failed = False
            self._pending_tx += 1
            self._next_tx_token += 1
            token = self._next_tx_token
            self._pending_frames[token] = frame
        threading.Thread(
            target=self._tx_pending,
            args=(frame, token),
            name="afsk-tx",
            daemon=True,
        ).start()

    def _tx_pending(self, frame: ControlFrame, token: int) -> None:
        try:
            self._tx_context.token = token
            self._tx(frame)
        except _ControlTxCancelled:
            pass
        except Exception as exc:
            with self._tx_condition:
                self._tx_failed = True
            self.on_log(f"Audio TX failed: {exc}")
        finally:
            with self._tx_condition:
                self._pending_frames.pop(token, None)
                self._cancelled_tx.discard(token)
                self._pending_tx -= 1
                self._tx_condition.notify_all()
            del self._tx_context.token

    def send_morse_after_pending(self, text: str, *, wpm: float = 40.0) -> bool:
        """Queue a CW identifier after all control frames already in flight.

        The final RECEIVED/DELIVERED frames are asynchronous. Counting this as
        a post-TX item lets it wait for those frames without making
        ``wait_tx_idle`` return early, and the shared TX lock keeps one radio
        carrier active at a time.
        """
        clean = normalise_morse_text(text)
        if not clean:
            return False
        with self._tx_condition:
            if self._stopped:
                return False
            if self._tx_suspended:
                self._deferred_tx.append(("morse", (clean, float(wpm))))
                return True
            self._pending_tx += 1
            self._post_tx_pending += 1
            self._next_tx_token += 1
            token = self._next_tx_token
            self._pending_frames[token] = None
        threading.Thread(
            target=self._morse_pending,
            args=(clean, float(wpm), token),
            name="morse-id-tx",
            daemon=True,
        ).start()
        return True

    def _morse_pending(self, text: str, wpm: float, token: int) -> None:
        try:
            self._tx_context.token = token
            with self._tx_condition:
                while self._pending_tx > self._post_tx_pending:
                    if self._control_tx_cancelled():
                        raise _ControlTxCancelled()
                    self._tx_condition.wait()
            self._tx_morse(text, wpm)
        except _ControlTxCancelled:
            pass
        finally:
            with self._tx_condition:
                self._pending_frames.pop(token, None)
                self._cancelled_tx.discard(token)
                self._pending_tx -= 1
                self._post_tx_pending -= 1
                self._tx_condition.notify_all()
            del self._tx_context.token

    def _tx_morse(self, text: str, wpm: float) -> None:
        if self._sd is None:
            self.on_log("Morse ID skipped — control channel not started")
            return
        samples = modulate_morse(text, sample_rate=self.fs, wpm=wpm)
        with self._tx_lock:
            if self._stopped:
                return
            transmit_waveform(
                self._sd,
                samples,
                device=self.output_device,
                sample_rate=self.fs,
                ptt=self._control_ptt,
                before_play=self._acquire_control_channel,
                after_release=self._release_control_channel,
            )
        self.on_log(f"TX Morse ID {text} ({wpm:g} WPM)")

    def wait_tx_idle(self, timeout: float = 5.0) -> bool:
        """Wait until every already-queued control burst has left the radio."""
        deadline = time.monotonic() + timeout
        with self._tx_condition:
            while self._pending_tx:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._tx_condition.wait(remaining)
            return not self._tx_failed

    def _tx(self, frame: ControlFrame) -> None:
        if self._sd is None:
            self.on_log("Audio TX skipped — control channel not started")
            return
        with self._tx_lock:
            if self._stopped:
                return
            payload = frame.encode()
            now = time.monotonic()
            self._sent_control_frames = {
                key: sent for key, sent in self._sent_control_frames.items()
                if now - sent < 120.0
            }
            # Beacons repeat by design and have no acknowledgement/retry
            # exchange. An unchanged position must not turn every periodic
            # beacon into an extended-acquisition retransmission.
            retry = frame.type is not FrameType.BEACON and payload in self._sent_control_frames
            if frame.type is not FrameType.BEACON:
                self._sent_control_frames[payload] = now
            retry_modulator = getattr(self.modem, "modulate_retry", None)
            if retry and retry_modulator is not None:
                samples = retry_modulator(payload)
                self.on_log("AFSK retry: extended acquisition for this repeated frame")
            else:
                samples = self.modem.modulate(payload)
            short_beacon = self._short_beacon and frame.type is FrameType.BEACON
            transmit_waveform(
                self._sd, samples,
                device=self.output_device,
                sample_rate=self.fs,
                ptt=self._control_ptt,
                lead_seconds=min(self.tx_lead_seconds, 0.06) if short_beacon else self.tx_lead_seconds,
                tail_seconds=min(self.tx_tail_seconds, 0.06) if short_beacon else self.tx_tail_seconds,
                guard_seconds=0.06 if short_beacon else self.tx_guard_seconds,
                # Never splice samples from before and after our own half-duplex
                # transmission into one artificial receive window.
                before_play=self._acquire_control_channel,
                after_release=self._release_control_channel,
            )
        self.on_log(f"TX {frame.summary()}")

    def channel_busy(self) -> bool:
        """Recognised control activity, including acquisition before decode.

        This is not a broadband carrier/speech detector. The same deadlines
        are checked again at PTT acquisition to cover traffic arriving later.
        """
        with self._tx_condition:
            return (
                self._tx_suspended or self._transmitting.is_set()
                or time.monotonic() < max(self._peer_ready_at, self._acquisition_ready_at)
            )

    def _acquire_control_channel(self) -> None:
        # Called after output-device preparation, immediately before PTT. A
        # second neighbor advertisement can begin during that preparation or
        # while the first frame's relay jitter expires. Keep RX running and
        # re-evaluate every new acquisition, rather than sleeping once against
        # the already-decoded first frame's release time.
        with self._tx_condition:
            while True:
                if self._control_tx_cancelled():
                    raise _ControlTxCancelled()
                remaining = max(self._peer_ready_at, self._acquisition_ready_at) - time.monotonic()
                if remaining <= 0:
                    self._transmitting.set()
                    self._rx_buf.clear()
                    if self._acquisition is not None:
                        self._acquisition.clear()
                    return
                self._tx_condition.wait(remaining)

    def _control_ptt(self, on: bool) -> None:
        if on:
            with self._tx_condition:
                if self._control_tx_cancelled():
                    raise _ControlTxCancelled()
        self.ptt(on)

    def _control_tx_cancelled(self) -> bool:
        return self._stopped or getattr(self._tx_context, "token", None) in self._cancelled_tx

    def _release_control_channel(self) -> None:
        self._rx_buf.clear()
        self._transmitting.clear()

    # ------------------------------------------------------------------ #
    #  Receive                                                            #
    # ------------------------------------------------------------------ #
    def _rx_callback(self, indata, frames, time_info, status):  # PortAudio thread
        if status:
            self.input_status_events += 1
            self.last_input_status = str(status)
        block = indata[:, 0]
        with self._tx_condition:
            if self._transmitting.is_set():
                return  # half-duplex: ignore our own transmission
            if self._acquisition is not None:
                acquired = self._acquisition.observe(block, time.monotonic())
                if acquired is not None:
                    self._acquisition_ready_at = max(
                        self._acquisition_ready_at, acquired + self._acquisition_hold,
                    )
                    self._tx_condition.notify_all()
            self._rx_buf.extend(block.copy())
        if self.on_audio is not None:
            # A recorder taps the stream here rather than opening a second handle
            # on the same device, so a capture is exactly the audio the modem is
            # working from. Wrapped because this runs on the PortAudio thread: an
            # exception escaping it would take the whole receive path down.
            try:
                self.on_audio(block)
            except Exception:  # noqa: BLE001 - a sink must never stop reception
                pass
        # Update level meters: smoothed RMS, peak, and a slow noise floor.
        rms = float(np.sqrt(np.mean(block.astype(np.float64) ** 2))) if len(block) else 0.0
        self._level = 0.7 * self._level + 0.3 * rms
        self._peak = max(self._peak * 0.95, float(np.max(np.abs(block))) if len(block) else 0.0)
        self._max_peak = max(self._max_peak, self._peak)
        # Floor tracks downward fast, recovers slowly -> settles on the quiet level.
        if self._floor == 0.0:
            self._floor = rms
        self._floor = min(rms, self._floor * 1.001 + 1e-5) if rms < self._floor else self._floor * 0.9995 + 0.0005 * rms
        if len(block):
            self._floor_seconds += len(block) / float(self.fs)

    @staticmethod
    def to_db(rms: float) -> float:
        import math
        return 20.0 * math.log10(max(rms, 1e-6))

    def levels(self) -> dict:
        """Current RX metering: linear rms/peak/floor plus dBFS conversions."""
        return {
            "rms": self._level, "peak": self._peak, "max_peak": self._max_peak,
            "floor": self._floor,
            "rms_db": self.to_db(self._level), "floor_db": self.to_db(self._floor),
            "running": self._stream is not None,
        }

    def window_snr(self, window: np.ndarray) -> float | None:
        """Estimated S/N in dB for a demodulated window, or None if unknowable.

        The burst does not fill the window, so the loudest quarter of it is the
        signal estimate; the noise is the idle floor the RX callback tracks.
        Before the floor has settled (or with too short a window) there is no
        honest number to give, and None keeps the UI from inventing one.
        """
        floor = self._floor
        samples = np.asarray(window, dtype=np.float64)
        block = max(1, int(self.fs * SNR_BLOCK_SECONDS))
        blocks = samples.size // block
        if floor <= 0.0 or blocks < SNR_MIN_BLOCKS:
            return None
        if self._floor_seconds < SNR_FLOOR_SETTLE_SECONDS:
            return None
        floor = max(floor, SNR_MIN_FLOOR)
        rms = np.sqrt((samples[: blocks * block].reshape(blocks, block) ** 2).mean(axis=1))
        loudest = np.sort(rms)[-max(1, blocks // 4):]
        signal = float(loudest.mean())
        if signal <= floor:
            return 0.0
        return round(min(self.to_db(signal) - self.to_db(floor), SNR_MAX_DB), 1)

    def _rx_loop(self, stopped: threading.Event) -> None:
        while not stopped.wait(self.poll_interval):
            if len(self._rx_buf) < self.fs * 0.4:
                continue
            window = np.fromiter(self._rx_buf, dtype=np.float32)
            snr = self.window_snr(window)
            for payload in self.modem.demodulate(
                window,
                validator=self._is_valid_control_payload,
            ):
                if stopped.is_set():
                    return
                self._process_candidate(payload, snr, window)

    def _process_candidate(
        self,
        payload: bytes,
        snr: float | None,
        window: np.ndarray,
    ) -> bool:
        """Classify a modem candidate before it reaches the orchestrator."""
        try:
            ControlFrame.decode(payload)
        except FrameError as exc:
            self.rejected_control_candidates += 1
            metadata = {
                "generated_utc": datetime.now(timezone.utc).isoformat(),
                "modem": getattr(self.modem, "name", type(self.modem).__name__),
                "sample_rate": self.fs,
                "snr_db": snr,
                "payload_length": len(payload),
                "payload_hex": payload.hex(),
                "reason": str(exc),
                "rejected_candidates": self.rejected_control_candidates,
            }
            self.last_rejected_control = metadata
            self.on_log(
                "RX rejected control candidate: "
                f"{exc}; {len(payload)} B; head={payload[:12].hex() or '-'}"
            )
            self._save_bad_audio(window, metadata)
            return False
        return self._handle_payload(payload, snr)

    @staticmethod
    def _is_valid_control_payload(payload: bytes) -> bool:
        try:
            ControlFrame.decode(payload)
        except FrameError:
            return False
        return True

    def _handle_payload(self, payload: bytes, snr: float | None = None) -> bool:
        now = time.monotonic()
        # Suppress the same capture across overlapping windows. Expire from
        # its first decode: refreshing on each poll can also suppress a real
        # protocol retry after a lost response, indefinitely on short retries.
        lifetime = self.rx_window + self.poll_interval
        self._recent = {k: t for k, t in self._recent.items()
                        if now - t < lifetime}
        if payload in self._recent:
            return self._is_valid_control_payload(payload)
        self._recent[payload] = now
        try:
            frame = ControlFrame.decode(payload)
        except FrameError as exc:
            self.on_log(f"RX bad frame: {exc}")
            return False
        # Preamble length does not identify a peer's release time: 1.1.2 used
        # the same 24-byte leader with a 400 ms guard and 250 ms PTT tail.
        peer_quiet = max(self.tx_guard_seconds + self.tx_tail_seconds,
                         TX_GUARD_SECONDS + PTT_TAIL_SECONDS)
        with self._tx_condition:
            self._peer_ready_at = max(self._peer_ready_at, now + peer_quiet)
            self._tx_condition.notify_all()
        self.on_log(
            f"RX {frame.summary()}"
            + (f"  S/N ~{snr:.1f} dB" if snr is not None else "")
        )
        # Queue for delivery on the owner's thread via pump() (avoids races with
        # the orchestrator's tick loop).
        self._rx_frames.append((frame, snr))
        return True

    def _save_bad_audio(self, samples: np.ndarray, metadata: dict | None = None) -> None:
        path = self.diagnostic_audio_path
        now = time.monotonic()
        if path is None or now - self._last_diagnostic_audio < 8.0:
            return
        self._last_diagnostic_audio = now
        metadata = metadata or {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "modem": getattr(self.modem, "name", type(self.modem).__name__),
            "sample_rate": self.fs,
            "reason": "demodulation failed",
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            pcm = (
                np.clip(np.asarray(samples), -1.0, 1.0) * 32767.0
            ).astype("<i2")
            with wave.open(str(path), "wb") as recording:
                recording.setnchannels(1)
                recording.setsampwidth(2)
                recording.setframerate(self.fs)
                recording.writeframes(pcm.tobytes())
            metadata_path = path.with_suffix(".json")
            metadata_path.write_text(
                json.dumps(metadata, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            self.on_log(f"Failed control audio saved: {path}")
        except OSError as exc:
            self.on_log(f"Failed control audio could not be saved: {exc}")

    def pump(self) -> int:
        """Deliver queued RX frames to on_frame. Call from the main/net thread."""
        delivered = 0
        while self._rx_frames:
            frame, self.last_frame_snr = self._rx_frames.popleft()
            if self.on_frame is not None:
                self.on_frame(frame)
            delivered += 1
        self.last_frame_snr = None
        return delivered
