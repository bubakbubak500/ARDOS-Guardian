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

from ..protocol import MAX_CONTROL_FRAME_BYTES, ControlFrame, FrameError
from ..session.transport import ControlTransport
from .afsk import AFSKModem


_PSEUDO_DEVICE_PREFIXES = (
    "microsoft sound mapper",
    "primary sound capture driver",
    "primary sound driver",
)

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


class AudioControlTransport(ControlTransport):
    def _modem_airtime(self, payload_bytes: int) -> float:
        """Longest frame this modem puts on air, or 0 for a modem without one."""
        airtime = getattr(self.modem, "airtime", None)
        return float(airtime(payload_bytes)) if airtime else 0.0

    def __init__(
        self,
        modem: AFSKModem | None = None,
        ptt: Callable[[bool], None] | None = None,
        sample_rate: int = 48000,
        input_device=None,
        output_device=None,
        diagnostic_audio_path: Path | str | None = None,
        on_log: Callable[[str], None] | None = None,
    ):
        self.modem = modem or AFSKModem(sample_rate=sample_rate)
        self.fs = sample_rate
        self.ptt = ptt or (lambda on: None)
        self.input_device = input_device
        self.output_device = output_device
        self.diagnostic_audio_path = (
            Path(diagnostic_audio_path) if diagnostic_audio_path else None
        )
        self.on_log = on_log or (lambda m: None)
        self.on_frame = None

        self._sd = None
        self._stream = None
        self.actual_input_device_name = ""
        self.actual_output_device_name = ""
        self.actual_input_device_index: int | None = None
        self.actual_output_device_index: int | None = None
        self._tx_lock = threading.Lock()
        self._tx_condition = threading.Condition()
        self._pending_tx = 0
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
        # Live RX metering (linear RMS in 0..1).
        self._level = 0.0          # smoothed current level
        self._floor = 0.0          # slow-tracking idle noise floor
        self._peak = 0.0
        self._max_peak = 0.0
        self._last_diagnostic_audio = 0.0

    # ------------------------------------------------------------------ #
    def start(self) -> None:
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
        self._rx_thread = threading.Thread(target=self._rx_loop, name="afsk-rx", daemon=True)
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

    def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    # ------------------------------------------------------------------ #
    #  Transmit                                                           #
    # ------------------------------------------------------------------ #
    def send(self, frame: ControlFrame) -> None:
        # TX off the caller's thread so the UI/orchestrator never blocks on PTT.
        with self._tx_condition:
            self._pending_tx += 1
        threading.Thread(
            target=self._tx_pending,
            args=(frame,),
            name="afsk-tx",
            daemon=True,
        ).start()

    def _tx_pending(self, frame: ControlFrame) -> None:
        try:
            self._tx(frame)
        finally:
            with self._tx_condition:
                self._pending_tx -= 1
                self._tx_condition.notify_all()

    def wait_tx_idle(self, timeout: float = 5.0) -> bool:
        """Wait until every already-queued control burst has left the radio."""
        deadline = time.monotonic() + timeout
        with self._tx_condition:
            while self._pending_tx:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._tx_condition.wait(remaining)
        return True

    def _tx(self, frame: ControlFrame) -> None:
        if self._sd is None:
            self.on_log("Audio TX skipped — control channel not started")
            return
        samples = self.modem.modulate(frame.encode())
        guard = np.zeros(int(TX_GUARD_SECONDS * self.fs), dtype=samples.dtype)
        samples = np.concatenate([samples, guard])
        with self._tx_lock:
            try:
                # Never splice samples from before and after our own half-duplex
                # transmission into one artificial receive window.
                self._rx_buf.clear()
                self.ptt(True)
                # brief lead-in so the rig is keyed before tones start
                time.sleep(PTT_LEAD_SECONDS)
                self._sd.play(samples, samplerate=self.fs, device=self.output_device)
                self._sd.wait()
            finally:
                # PortAudio has finished filling the USB endpoint here, but a
                # USB radio can still have audio buffered internally. Keep PTT
                # asserted long enough for the final CRC and postamble to air.
                time.sleep(PTT_TAIL_SECONDS)
                self.ptt(False)
                self._rx_buf.clear()
        self.on_log(f"TX {frame.summary()}")

    # ------------------------------------------------------------------ #
    #  Receive                                                            #
    # ------------------------------------------------------------------ #
    def _rx_callback(self, indata, frames, time_info, status):  # PortAudio thread
        if self._tx_lock.locked():
            return  # half-duplex: ignore our own transmission
        block = indata[:, 0]
        self._rx_buf.extend(block.copy())
        # Update level meters: smoothed RMS, peak, and a slow noise floor.
        rms = float(np.sqrt(np.mean(block.astype(np.float64) ** 2))) if len(block) else 0.0
        self._level = 0.7 * self._level + 0.3 * rms
        self._peak = max(self._peak * 0.95, float(np.max(np.abs(block))) if len(block) else 0.0)
        self._max_peak = max(self._max_peak, self._peak)
        # Floor tracks downward fast, recovers slowly -> settles on the quiet level.
        if self._floor == 0.0:
            self._floor = rms
        self._floor = min(rms, self._floor * 1.001 + 1e-5) if rms < self._floor else self._floor * 0.9995 + 0.0005 * rms

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
        rms = np.sqrt((samples[: blocks * block].reshape(blocks, block) ** 2).mean(axis=1))
        loudest = np.sort(rms)[-max(1, blocks // 4):]
        signal = float(loudest.mean())
        if signal <= floor:
            return 0.0
        return round(self.to_db(signal) - self.to_db(floor), 1)

    def _rx_loop(self) -> None:
        while self._running:
            time.sleep(self.poll_interval)
            if len(self._rx_buf) < self.fs * 0.4:
                continue
            window = np.fromiter(self._rx_buf, dtype=np.float32)
            snr = self.window_snr(window)
            for payload in self.modem.demodulate(
                window,
                validator=self._is_valid_control_payload,
            ):
                if not self._handle_payload(payload, snr):
                    self._save_bad_audio(window)

    @staticmethod
    def _is_valid_control_payload(payload: bytes) -> bool:
        try:
            ControlFrame.decode(payload)
        except FrameError:
            return False
        return True

    def _handle_payload(self, payload: bytes, snr: float | None = None) -> bool:
        now = time.monotonic()
        # Drop duplicates seen recently (overlapping demod windows / repeats).
        self._recent = {k: t for k, t in self._recent.items() if now - t < 8.0}
        if payload in self._recent:
            self._recent[payload] = now
            return self._is_valid_control_payload(payload)
        self._recent[payload] = now
        try:
            frame = ControlFrame.decode(payload)
        except FrameError as exc:
            self.on_log(f"RX bad frame: {exc}")
            return False
        self.on_log(
            f"RX {frame.summary()}"
            + (f"  S/N ~{snr:.1f} dB" if snr is not None else "")
        )
        # Queue for delivery on the owner's thread via pump() (avoids races with
        # the orchestrator's tick loop).
        self._rx_frames.append((frame, snr))
        return True

    def _save_bad_audio(self, samples: np.ndarray) -> None:
        path = self.diagnostic_audio_path
        now = time.monotonic()
        if path is None or now - self._last_diagnostic_audio < 8.0:
            return
        self._last_diagnostic_audio = now
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
