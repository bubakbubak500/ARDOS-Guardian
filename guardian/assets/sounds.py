"""Notification sounds, synthesized with the standard library.

Generated rather than shipped, for the same reason as the icon: no binary
blob in the repository, and the cached file can always be rebuilt. Two
distinct voices on purpose -- in a busy room the operator must know from the
sound alone whether a message arrived or the net raised an emergency.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from ..config import config_dir

SAMPLE_RATE = 22_050


def _tone(frequency: float, seconds: float, volume: float) -> list[float]:
    """One sine tone with a short fade at both ends so it never clicks."""
    total = int(SAMPLE_RATE * seconds)
    fade = max(1, int(SAMPLE_RATE * 0.012))
    samples: list[float] = []
    for n in range(total):
        envelope = min(1.0, n / fade, (total - n) / fade)
        samples.append(
            volume * envelope * math.sin(2 * math.pi * frequency * n / SAMPLE_RATE)
        )
    return samples


def _silence(seconds: float) -> list[float]:
    return [0.0] * int(SAMPLE_RATE * seconds)


def _write_wav(path: Path, samples: list[float]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = b"".join(
        struct.pack("<h", int(max(-1.0, min(1.0, value)) * 32_000))
        for value in samples
    )
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(frames)
    return path


def ensure_notify_wav(path: Path | None = None) -> Path:
    """A gentle two-tone chime: mail arrived, nothing is on fire."""
    path = path or config_dir() / "notify.wav"
    if not path.exists():
        _write_wav(
            path,
            _tone(880.0, 0.14, 0.35) + _silence(0.03) + _tone(1318.5, 0.20, 0.30),
        )
    return path


def ensure_emergency_wav(path: Path | None = None) -> Path:
    """Three rising tones, louder and longer: the net raised an alert."""
    path = path or config_dir() / "emergency.wav"
    if not path.exists():
        samples: list[float] = []
        for frequency in (950.0, 1150.0, 1400.0):
            samples += _tone(frequency, 0.22, 0.75) + _silence(0.05)
        _write_wav(path, samples)
    return path
