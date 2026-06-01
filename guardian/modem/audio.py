"""AudioControlTransport — bind a modem to a sound device + the radio PTT.

This is the real on-air control channel: it modulates outgoing control frames
to audio, keys PTT, and plays them; and it continuously captures RX audio,
demodulates it, and hands decoded ControlFrames to the orchestrator. It is a
drop-in `ControlTransport`, so the Phase-2 orchestrator uses it unchanged.

sounddevice (PortAudio) is imported lazily so the rest of Guardian runs on a PC
with no audio backend; start() raises a clear error if it can't be used.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

import numpy as np

from ..protocol import ControlFrame, FrameError
from ..session.transport import ControlTransport
from .afsk import AFSKModem


def list_audio_devices() -> tuple[list[str], list[str]]:
    """Return (input_device_names, output_device_names). Empty if no backend."""
    try:
        import sounddevice as sd
    except Exception:
        return [], []
    inputs, outputs = [], []
    try:
        for d in sd.query_devices():
            name = d.get("name", "")
            if d.get("max_input_channels", 0) > 0:
                inputs.append(name)
            if d.get("max_output_channels", 0) > 0:
                outputs.append(name)
    except Exception:
        return [], []
    return inputs, outputs


class AudioControlTransport(ControlTransport):
    def __init__(
        self,
        modem: AFSKModem | None = None,
        ptt: Callable[[bool], None] | None = None,
        sample_rate: int = 48000,
        input_device=None,
        output_device=None,
        on_log: Callable[[str], None] | None = None,
    ):
        self.modem = modem or AFSKModem(sample_rate=sample_rate)
        self.fs = sample_rate
        self.ptt = ptt or (lambda on: None)
        self.input_device = input_device
        self.output_device = output_device
        self.on_log = on_log or (lambda m: None)
        self.on_frame = None

        self._sd = None
        self._stream = None
        self._tx_lock = threading.Lock()
        self._rx_buf = deque(maxlen=int(sample_rate * 4))  # ~4 s rolling window
        self._recent: dict[bytes, float] = {}              # payload -> last seen
        self._rx_frames: deque = deque()                   # decoded, awaiting pump()
        self._running = False
        self._rx_thread: threading.Thread | None = None
        # Live RX metering (linear RMS in 0..1).
        self._level = 0.0          # smoothed current level
        self._floor = 0.0          # slow-tracking idle noise floor
        self._peak = 0.0

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        import sounddevice as sd  # lazy; raises if PortAudio missing
        self._sd = sd
        self._running = True
        self._stream = sd.InputStream(
            samplerate=self.fs, channels=1, dtype="float32",
            device=self.input_device, callback=self._rx_callback,
            blocksize=int(self.fs * 0.1),
        )
        self._stream.start()
        self._rx_thread = threading.Thread(target=self._rx_loop, name="afsk-rx", daemon=True)
        self._rx_thread.start()
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
        threading.Thread(target=self._tx, args=(frame,), daemon=True).start()

    def _tx(self, frame: ControlFrame) -> None:
        if self._sd is None:
            self.on_log("Audio TX skipped — control channel not started")
            return
        samples = self.modem.modulate(frame.encode())
        with self._tx_lock:
            try:
                self.ptt(True)
                # brief lead-in so the rig is keyed before tones start
                time.sleep(0.15)
                self._sd.play(samples, samplerate=self.fs, device=self.output_device)
                self._sd.wait()
            finally:
                time.sleep(0.05)
                self.ptt(False)
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
            "rms": self._level, "peak": self._peak, "floor": self._floor,
            "rms_db": self.to_db(self._level), "floor_db": self.to_db(self._floor),
            "running": self._stream is not None,
        }

    def _rx_loop(self) -> None:
        while self._running:
            time.sleep(0.25)
            if len(self._rx_buf) < self.fs * 0.4:
                continue
            window = np.fromiter(self._rx_buf, dtype=np.float32)
            for payload in self.modem.demodulate(window):
                self._handle_payload(payload)

    def _handle_payload(self, payload: bytes) -> None:
        now = time.monotonic()
        # Drop duplicates seen recently (overlapping demod windows / repeats).
        self._recent = {k: t for k, t in self._recent.items() if now - t < 8.0}
        if payload in self._recent:
            self._recent[payload] = now
            return
        self._recent[payload] = now
        try:
            frame = ControlFrame.decode(payload)
        except FrameError as exc:
            self.on_log(f"RX bad frame: {exc}")
            return
        self.on_log(f"RX {frame.summary()}")
        # Queue for delivery on the owner's thread via pump() (avoids races with
        # the orchestrator's tick loop).
        self._rx_frames.append(frame)

    def pump(self) -> int:
        """Deliver queued RX frames to on_frame. Call from the main/net thread."""
        delivered = 0
        while self._rx_frames:
            frame = self._rx_frames.popleft()
            if self.on_frame is not None:
                self.on_frame(frame)
            delivered += 1
        return delivered
