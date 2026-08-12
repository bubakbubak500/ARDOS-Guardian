"""Paired station calibration, scoring, persistence, and compact wire tokens.

The DSP and the UI deliberately meet at plain dataclasses.  A real radio run,
a deterministic test, and a saved report therefore use the same safety rules
and recommendation algorithm.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import math
import os
import struct
import sys
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from statistics import median
from typing import Protocol

from . import __version__
from .config import config_dir

CALIBRATION_PROTOCOL_VERSION = 2
MAX_CALIBRATION_SECONDS = 15 * 60
MAX_CALIBRATION_BURSTS = 96
MAX_DUTY_CYCLE = 0.50
MIN_DIGITAL_HEADROOM_DB = 1.0

_FAMILY_ID = {
    "ofdm": 0, "sc_hs": 1, "sc_ftn": 2, "sefdm": 3,
    "sc_fde_ftn": 4,
}
_ID_FAMILY = {value: key for key, value in _FAMILY_ID.items()}
_BANDWIDTH_ID = {"1K2": 0, "2K7": 1, "5K": 2, "10K": 3, "20K": 4}
_ID_BANDWIDTH = {value: key for key, value in _BANDWIDTH_ID.items()}
_PROBE_V1 = struct.Struct(">BBBBH")
_PROBE = struct.Struct(">BBBBBH")
_REPORT = struct.Struct(">BBbbbBBB")


class CalibrationMode(str, Enum):
    QUICK = "quick"
    FULL = "full"


class CalibrationState(str, Enum):
    IDLE = "idle"
    OFFERING = "offering"
    WAITING_APPROVAL = "waiting_approval"
    PREPARING = "preparing"
    MEASURING = "measuring"
    WAITING_REPORT = "waiting_report"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class ProbeCommand:
    sequence: int
    waveform: str
    mcs: int
    fec: int
    tx_scale: float
    bandwidth: str = "2K7"
    capture_path: str = ""

    def encode(self) -> str:
        family = _FAMILY_ID.get(self.waveform)
        if family is None:
            raise ValueError(f"unknown waveform family {self.waveform!r}")
        bandwidth = _BANDWIDTH_ID.get(str(self.bandwidth).upper())
        if bandwidth is None:
            raise ValueError(f"unknown calibration bandwidth {self.bandwidth!r}")
        raw = _PROBE.pack(
            int(self.sequence) & 0xFF,
            family,
            bandwidth,
            max(0, min(255, int(self.mcs))),
            max(0, min(255, int(self.fec))),
            max(0, min(1000, round(float(self.tx_scale) * 1000))),
        )
        return base64.b32encode(raw).decode("ascii").rstrip("=")

    @classmethod
    def decode(cls, token: str) -> "ProbeCommand":
        raw = _decode_base32(token)
        if len(raw) == _PROBE_V1.size:
            sequence, family, mcs, fec, scale = _PROBE_V1.unpack(raw)
            bandwidth = "2K7"
        elif len(raw) == _PROBE.size:
            sequence, family, width, mcs, fec, scale = _PROBE.unpack(raw)
            if width not in _ID_BANDWIDTH:
                raise ValueError("unknown calibration bandwidth id")
            bandwidth = _ID_BANDWIDTH[width]
        else:
            raise ValueError("wrong calibration token length")
        if family not in _ID_FAMILY:
            raise ValueError("unknown calibration waveform id")
        return cls(sequence, _ID_FAMILY[family], mcs, fec, scale / 1000.0,
                   bandwidth)


@dataclass
class ProbeResult:
    sequence: int
    tx_scale: float
    waveform: str
    mcs: int
    fec: int
    frame_ok: bool
    header_ok: bool = False
    snr_db: float | None = None
    evm_rms: float | None = None
    audio_peak: float | None = None
    audio_rms: float | None = None
    clipped_samples: int = 0
    flat_top: bool = False
    sync_confidence: float | None = None
    cfo_hz: float | None = None
    payload_bytes: int = 0
    wall_seconds: float = 0.0
    keyed_seconds: float = 0.0
    error: str = ""
    endpoint_volume: float | None = None
    session_volume: float | None = None
    bandwidth: str = "2K7"

    @property
    def safe(self) -> bool:
        peak_safe = self.audio_peak is None or self.audio_peak < 0.995
        return bool(
            self.header_ok and not self.flat_top
            and self.clipped_samples == 0 and peak_safe
        )

    @property
    def expected_goodput_bps(self) -> float:
        if not self.safe or not self.frame_ok or self.wall_seconds <= 0.0:
            return 0.0
        return self.payload_bytes * 8.0 / self.wall_seconds

    def encode_report(self) -> str:
        status = ((1 if self.frame_ok else 0) | (2 if self.header_ok else 0)
                  | (4 if self.flat_top else 0))
        raw = _REPORT.pack(
            int(self.sequence) & 0xFF,
            status,
            _signed_half_db(self.snr_db),
            _signed_half_db(None if self.evm_rms is None else
                            -20.0 * math.log10(max(self.evm_rms, 1e-6))),
            max(-128, min(127, round(float(self.cfo_hz or 0.0)))),
            _unit_byte(self.audio_peak),
            max(0, min(255, int(self.clipped_samples))),
            _unit_byte(self.sync_confidence),
        )
        return base64.b32encode(raw).decode("ascii").rstrip("=")

    @classmethod
    def decode_report(cls, token: str, command: ProbeCommand) -> "ProbeResult":
        raw = _decode_base32(token, _REPORT.size)
        seq, status, snr, evm_snr, cfo, peak, clipped, sync = _REPORT.unpack(raw)
        if seq != command.sequence:
            raise ValueError("calibration report sequence does not match probe")
        evm = None if evm_snr == -128 else 10.0 ** (-(evm_snr / 2.0) / 20.0)
        return cls(
            sequence=seq, tx_scale=command.tx_scale,
            waveform=command.waveform, mcs=command.mcs, fec=command.fec,
            frame_ok=bool(status & 1), header_ok=bool(status & 2),
            flat_top=bool(status & 4),
            snr_db=None if snr == -128 else snr / 2.0,
            evm_rms=evm, cfo_hz=float(cfo),
            audio_peak=_byte_unit(peak),
            clipped_samples=clipped, sync_confidence=_byte_unit(sync),
            bandwidth=command.bandwidth,
        )


@dataclass(frozen=True)
class RecommendedPoint:
    waveform: str
    mcs: int
    fec: int
    tx_scale: float
    endpoint_volume: float | None
    score_bps: float
    margin_db: float
    reason: str
    bandwidth: str = "2K7"


@dataclass
class CalibrationReport:
    session_id: int
    peer: str
    mode: str
    direction: str
    started_utc: str
    completed_utc: str = ""
    radio: str = ""
    audio_input: str = ""
    audio_output: str = ""
    host_api: str = ""
    frequency_hz: int | None = None
    guardian_version: str = __version__
    protocol_version: int = CALIBRATION_PROTOCOL_VERSION
    results: list[ProbeResult] = field(default_factory=list)
    recommendation: RecommendedPoint | None = None
    stop_reason: str = ""
    applied: bool = False

    def finish(self, reason: str = "") -> None:
        self.completed_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.stop_reason = reason
        self.recommendation = recommend(self.results)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    def to_csv(self) -> str:
        fields = list(ProbeResult.__dataclass_fields__)
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for result in self.results:
            writer.writerow(asdict(result))
        return output.getvalue()

    def save(self, directory: Path | None = None) -> tuple[Path, Path]:
        root = directory or config_dir() / "station-lab"
        root.mkdir(parents=True, exist_ok=True)
        stem = f"cal-{self.started_utc.replace(':', '').replace('-', '')}-{self.peer}-{self.session_id:08x}"
        json_path = root / f"{stem}.json"
        csv_path = root / f"{stem}.csv"
        json_path.write_text(self.to_json(), encoding="utf-8")
        csv_path.write_text(self.to_csv(), encoding="utf-8-sig")
        return json_path, csv_path


def quick_plan(waveform: str, mcs: int, fec: int,
               bandwidth: str = "2K7") -> list[ProbeCommand]:
    """Conservative low-to-high ladder with a useful point at every step."""
    scales = (0.20, 0.28, 0.40, 0.56, 0.72, 0.88)
    return [ProbeCommand(i, waveform, mcs, fec, scale, bandwidth)
            for i, scale in enumerate(scales)]


def full_plan(waveforms: list[str] | None = None,
              bandwidths: list[str] | None = None) -> list[ProbeCommand]:
    """Bounded characterizer frontier; failed branches are pruned by the runner."""
    families = waveforms or ["ofdm", "sc_hs", "sc_ftn", "sc_fde_ftn", "sefdm"]
    commands: list[ProbeCommand] = []
    sequence = 0
    widths = bandwidths or ["1K2", "2K7", "5K", "10K", "20K"]
    for family in families:
        # First cover the whole family/width surface with robust and useful
        # intermediate orders. Denser modes are appended below and naturally
        # trimmed by MAX_CALIBRATION_BURSTS.
        ladder = (0, 1, 2, 3) if family == "ofdm" else (1, 2, 7, 5)
        family_widths = ["2K7"] if family == "ofdm" else widths
        for width in family_widths:
            for mcs in ladder:
                commands.append(ProbeCommand(
                    sequence, family, mcs, 1, 0.40, width
                ))
                sequence += 1
    for family in (item for item in families if item != "ofdm"):
        for mcs in (3, 6, 8, 9, 10, 11, 12, 16, 17, 18, 19, 20):
            commands.append(ProbeCommand(
                sequence, family, mcs, 1, 0.40, "2K7"
            ))
            sequence += 1
    return commands[:MAX_CALIBRATION_BURSTS]


def recommend(results: list[ProbeResult]) -> RecommendedPoint | None:
    """Choose the lowest drive within 0.5 dB of the best safe goodput."""
    grouped: dict[tuple[str, str, int, int, float, float | None], list[ProbeResult]] = {}
    for result in results:
        key = (result.waveform, result.bandwidth, result.mcs, result.fec,
               round(result.tx_scale, 4), result.endpoint_volume)
        grouped.setdefault(key, []).append(result)
    candidates: list[tuple[float, tuple, list[ProbeResult]]] = []
    for key, group in grouped.items():
        # A point is only eligible when every decoded delivery is byte-valid and
        # at least two thirds of repetitions pass. Unsafe measurements never get
        # averaged into respectability.
        if any(not result.safe for result in group):
            continue
        passed = [result for result in group if result.frame_ok]
        if not passed or len(passed) / len(group) < 2.0 / 3.0:
            continue
        score = median(result.expected_goodput_bps for result in passed)
        candidates.append((score, key, group))
    if not candidates:
        return None
    best_score = max(score for score, _key, _group in candidates)
    floor = best_score * 10.0 ** (-0.5 / 10.0)
    near = [item for item in candidates if item[0] >= floor]
    score, key, _group = min(near, key=lambda item: (item[1][4], item[1][5] or 0.0,
                                                     -item[0]))
    waveform, bandwidth, mcs, fec, tx_scale, endpoint = key
    margin = 10.0 * math.log10(max(score, 1e-12) / max(best_score, 1e-12))
    return RecommendedPoint(
        waveform, mcs, fec, tx_scale, endpoint, score, margin,
        "lowest safe drive within 0.5 dB of the best measured goodput",
        bandwidth,
    )


@dataclass(frozen=True)
class GainSnapshot:
    endpoint_volume: float | None
    endpoint_muted: bool | None
    session_volume: float | None
    supported: bool
    device: str = ""
    host_api: str = ""


class GainController(Protocol):
    def snapshot(self) -> GainSnapshot: ...
    def set_endpoint_volume(self, scalar: float) -> None: ...
    def set_session_volume(self, scalar: float) -> None: ...
    def restore(self, snapshot: GainSnapshot) -> None: ...


class WindowsGainController:
    """Optional Core Audio controller; importing it never requires pycaw.

    Device mutation is intentionally unavailable until ``snapshot`` has found
    the configured endpoint.  The caller journals that snapshot before making
    the first change and always restores it on cancel/error.
    """

    def __init__(self, device_hint: str = "") -> None:
        self.device_hint = " ".join(device_hint.casefold().split())
        self._endpoint = None
        self._session = None

    def _bind(self) -> None:
        if sys.platform != "win32":
            return
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL, CoInitialize
            CoInitialize()
        except (ImportError, OSError):
            return
        devices = list(getattr(AudioUtilities, "GetAllDevices", lambda: [])())
        if not devices:
            try:
                devices = [AudioUtilities.GetSpeakers()]
            except Exception:  # noqa: BLE001 - optional OS integration
                return
        for device in devices:
            name = " ".join(str(getattr(device, "FriendlyName", "")).casefold().split())
            if self.device_hint and self.device_hint not in name and name not in self.device_hint:
                continue
            try:
                self._endpoint = getattr(device, "EndpointVolume", None)
                if self._endpoint is None:
                    interface = device.Activate(
                        IAudioEndpointVolume._iid_, CLSCTX_ALL, None
                    )
                    from ctypes import POINTER, cast
                    self._endpoint = cast(interface, POINTER(IAudioEndpointVolume))
                break
            except Exception:  # noqa: BLE001
                continue
        try:
            pid = os.getpid()
            for session in AudioUtilities.GetAllSessions():
                if session.Process and session.Process.pid == pid:
                    self._session = session.SimpleAudioVolume
                    break
        except Exception:  # noqa: BLE001
            self._session = None

    def snapshot(self) -> GainSnapshot:
        self._bind()
        try:
            endpoint = float(self._endpoint.GetMasterVolumeLevelScalar())
            muted = bool(self._endpoint.GetMute())
        except Exception:  # noqa: BLE001
            endpoint, muted = None, None
        try:
            session = float(self._session.GetMasterVolume())
        except Exception:  # noqa: BLE001
            session = None
        return GainSnapshot(endpoint, muted, session, endpoint is not None,
                            self.device_hint, "WASAPI/Core Audio")

    def set_endpoint_volume(self, scalar: float) -> None:
        if self._endpoint is None:
            raise RuntimeError("Windows endpoint volume is not available")
        self._endpoint.SetMasterVolumeLevelScalar(_clamp_unit(scalar), None)

    def set_session_volume(self, scalar: float) -> None:
        if self._session is None:
            raise RuntimeError("Guardian audio-session volume is not available")
        self._session.SetMasterVolume(_clamp_unit(scalar), None)

    def restore(self, snapshot: GainSnapshot) -> None:
        if snapshot.endpoint_volume is not None and self._endpoint is not None:
            self._endpoint.SetMasterVolumeLevelScalar(snapshot.endpoint_volume, None)
        if snapshot.endpoint_muted is not None and self._endpoint is not None:
            self._endpoint.SetMute(snapshot.endpoint_muted, None)
        if snapshot.session_volume is not None and self._session is not None:
            self._session.SetMasterVolume(snapshot.session_volume, None)


class GainJournal:
    """Crash-safe marker for a temporarily changed Windows mixer."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_dir() / "station-lab" / "gain-restore.json"

    def arm(self, snapshot: GainSnapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(snapshot), indent=2), encoding="utf-8")

    def load(self) -> GainSnapshot | None:
        try:
            return GainSnapshot(**json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


def _decode_base32(token: str, expected: int | None = None) -> bytes:
    clean = "".join(str(token).upper().split())
    raw = base64.b32decode(clean + "=" * ((-len(clean)) % 8), casefold=True)
    if expected is not None and len(raw) != expected:
        raise ValueError("wrong calibration token length")
    return raw


def _signed_half_db(value: float | None) -> int:
    if value is None or not math.isfinite(value):
        return -128
    return max(-127, min(127, round(value * 2.0)))


def _unit_byte(value: float | None) -> int:
    if value is None or not math.isfinite(value):
        return 255
    return max(0, min(254, round(_clamp_unit(value) * 254.0)))


def _byte_unit(value: int) -> float | None:
    return None if value == 255 else value / 254.0


def _clamp_unit(value: float) -> float:
    return min(1.0, max(0.0, float(value)))
