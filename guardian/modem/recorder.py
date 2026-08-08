"""Record received audio to a WAV file, for analysing a link off the air.

This exists because the useful measurements of a new modem are the ones taken on
a captured file rather than live: a capture can be decoded again and again, with
changes, long after the radios have been put away. `tools/ofdm_bench.py
--read-wav` reads exactly what this writes.

The format is fixed at mono 16-bit PCM at the audio path's own sample rate, which
is what every offline tool here expects. Nothing is normalised, trimmed or
filtered on the way out: the silence around a burst is data -- it is the noise
floor a squelch is measured against -- and the absolute level is how you tell a
clipping radio from a quiet one.
"""

from __future__ import annotations

import queue
import threading
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

#: WAV files are written as signed 16-bit PCM.
SAMPLE_WIDTH = 2
_FULL_SCALE = 32767

#: Above this fraction of full scale a sample is counted as clipped. Not 1.0:
#: a path that is driving into its limit rounds off just below the rail, and by
#: the time samples actually reach full scale the damage is long done.
CLIP_THRESHOLD = 0.98


def capture_path(directory: Path, when: datetime | None = None) -> Path:
    """A dated file name, so a session's captures sort in the order they happened."""
    stamp = (when or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return Path(directory) / f"capture-{stamp}.wav"


@dataclass(frozen=True)
class RecordingSummary:
    """What was captured, and whether it is worth analysing.

    The operator needs to know this at the radio, not after sending the file
    somewhere: a capture that was silent or clipped is a capture to take again
    while the other station is still on the air.
    """

    path: Path
    sample_rate: int
    samples: int
    rms: float
    peak: float
    clipped_samples: int
    dropped_blocks: int = 0

    @property
    def duration_seconds(self) -> float:
        return self.samples / self.sample_rate if self.sample_rate else 0.0

    @property
    def peak_dbfs(self) -> float:
        return 20.0 * np.log10(max(self.peak, 1e-9))

    @property
    def rms_dbfs(self) -> float:
        return 20.0 * np.log10(max(self.rms, 1e-9))

    @property
    def crest_factor_db(self) -> float:
        """Peak above RMS. An OFDM burst should show roughly 10-13 dB.

        Much less means the peaks have been flattened somewhere in the path, which
        is the signature of clipping even when no sample reached full scale.
        """
        if self.rms <= 0.0 or self.peak <= 0.0:
            return float("nan")
        return 20.0 * np.log10(self.peak / self.rms)

    @property
    def usable(self) -> bool:
        return not self.silent and not self.clipping and self.samples > 0

    @property
    def silent(self) -> bool:
        # -60 dBFS is below any radio's noise floor through a working interface;
        # a capture this quiet means nothing was connected, not that it was quiet.
        return self.peak < 1e-3

    @property
    def clipping(self) -> bool:
        return self.clipped_samples > 0

    def verdict(self) -> str:
        """One line naming the single most important thing about this capture."""
        if not self.samples:
            return "nothing was recorded"
        if self.silent:
            return (f"silent (peak {self.peak_dbfs:.0f} dBFS) -- check that the "
                    "receive device is the one the radio feeds")
        if self.clipping:
            return (f"clipping ({self.clipped_samples} samples at full scale) -- "
                    "reduce the receive level and record again")
        if self.peak_dbfs < -40.0:
            return (f"very quiet (peak {self.peak_dbfs:.0f} dBFS) -- usable, but "
                    "more receive level would measure better")
        return f"looks usable (peak {self.peak_dbfs:.0f} dBFS)"

    def summary(self) -> str:
        """A line for the log."""
        return (
            f"{self.path.name}: {self.duration_seconds:.1f} s at "
            f"{self.sample_rate} Hz, peak {self.peak_dbfs:.1f} dBFS, "
            f"RMS {self.rms_dbfs:.1f} dBFS, crest {self.crest_factor_db:.1f} dB"
            + (f", {self.dropped_blocks} blocks dropped" if self.dropped_blocks else "")
        )


class WavRecorder:
    """Writes audio blocks to a WAV file from a thread of its own.

    `write` is called from the PortAudio callback, so it does the least possible
    work: it hands the block to a queue and returns. The file write happens on a
    worker thread, because writing to disk inside an audio callback is how you get
    dropouts -- and a capture with gaps in it is worse than no capture, since the
    gaps look exactly like a channel that faded.
    """

    def __init__(self, path: Path | str, sample_rate: int,
                 on_log=None) -> None:
        self.path = Path(path)
        self.sample_rate = int(sample_rate)
        self.on_log = on_log or (lambda message: None)
        self._queue: queue.Queue = queue.Queue(maxsize=512)
        self._writer: threading.Thread | None = None
        self._handle: wave.Wave_write | None = None
        self._running = False
        self._lock = threading.Lock()
        self._samples = 0
        self._sum_squares = 0.0
        self._peak = 0.0
        self._clipped = 0
        self._dropped = 0

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> Path:
        """Open the file and start the writer thread."""
        if self._running:
            raise RuntimeError("this recorder is already running")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = wave.open(str(self.path), "wb")
        handle.setnchannels(1)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(self.sample_rate)
        self._handle = handle
        self._running = True
        self._writer = threading.Thread(target=self._drain, name="wav-recorder",
                                        daemon=True)
        self._writer.start()
        return self.path

    def write(self, block) -> None:
        """Queue one block of audio. Called from the audio callback; never raises."""
        if not self._running:
            return
        try:
            self._queue.put_nowait(np.asarray(block, dtype=np.float64).copy())
        except queue.Full:
            # Losing a block is bad but stalling the audio callback is worse, and
            # the count is reported so a gappy capture is never mistaken for a
            # clean one.
            with self._lock:
                self._dropped += 1

    def stop(self) -> RecordingSummary:
        """Flush everything queued, close the file, and report what was captured."""
        if not self._running:
            return self._summary()
        self._running = False
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.join(timeout=5.0)
        # Anything the writer did not reach before it noticed the stop.
        while True:
            try:
                self._consume(self._queue.get_nowait())
            except queue.Empty:
                break
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.close()
            except Exception:  # noqa: BLE001 - closing is best-effort
                pass
        summary = self._summary()
        self.on_log(f"Recorded {summary.summary()}")
        return summary

    # -- internals ----------------------------------------------------------

    def _drain(self) -> None:
        while self._running:
            try:
                block = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            self._consume(block)

    def _consume(self, block: np.ndarray) -> None:
        if self._handle is None or not len(block):
            return
        clipped = np.clip(block, -1.0, 1.0)
        with self._lock:
            self._samples += len(block)
            self._sum_squares += float(np.sum(block ** 2))
            self._peak = max(self._peak, float(np.max(np.abs(block))))
            self._clipped += int(np.count_nonzero(np.abs(block) >= CLIP_THRESHOLD))
        try:
            self._handle.writeframes((clipped * _FULL_SCALE).astype("<i2").tobytes())
        except Exception as exc:  # noqa: BLE001 - a full disk must not kill the thread
            self.on_log(f"Recording write failed: {exc}")

    def _summary(self) -> RecordingSummary:
        with self._lock:
            samples = self._samples
            rms = float(np.sqrt(self._sum_squares / samples)) if samples else 0.0
            return RecordingSummary(
                path=self.path,
                sample_rate=self.sample_rate,
                samples=samples,
                rms=rms,
                peak=self._peak,
                clipped_samples=self._clipped,
                dropped_blocks=self._dropped,
            )

    # -- live state ---------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._running

    @property
    def samples(self) -> int:
        with self._lock:
            return self._samples

    @property
    def seconds(self) -> float:
        return self.samples / self.sample_rate if self.sample_rate else 0.0

    def level(self) -> float:
        """Peak seen so far, in full-scale units. For a recording indicator."""
        with self._lock:
            return self._peak


class AudioCapture:
    """An input stream of its own, for recording when no control channel is open.

    When the control channel *is* open, `Operations` taps its existing stream
    instead: one device handle is less to go wrong, and it guarantees the capture
    is exactly the audio the modem is working from. This class is the other case
    -- a station brought up only to record what the other end transmits, which is
    the first and most useful step of an on-air test.
    """

    def __init__(self, recorder: WavRecorder, *, device, sample_rate: int) -> None:
        self.recorder = recorder
        self.device = device
        self.sample_rate = int(sample_rate)
        self._sd = None
        self._stream = None

    def start(self) -> None:
        from .audio import _import_sounddevice

        sd = _import_sounddevice()
        self._sd = sd
        if not isinstance(self.device, int):
            raise RuntimeError("The configured RX audio input is not available")
        sd.check_input_settings(device=self.device, channels=1, dtype="float32",
                                samplerate=self.sample_rate)
        self._stream = sd.InputStream(
            samplerate=self.sample_rate, channels=1, dtype="float32",
            device=self.device, callback=self._on_audio,
            blocksize=int(self.sample_rate * 0.1),
        )
        self._stream.start()

    def _on_audio(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        self.recorder.write(indata[:, 0])

    def stop(self) -> None:
        """Close the stream. Never raises — it runs in `finally` blocks."""
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001 - closing is best-effort
                pass
