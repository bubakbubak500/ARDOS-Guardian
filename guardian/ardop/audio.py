"""Half-duplex soundcard adapter. DSP runs on the owning payload worker.

The callback only copies bounded audio blocks. Existing Guardian playback owns
PTT and drains the endpoint before releasing it; TX_DONE reaches ARDOP afterwards.
"""
import queue
import time
import numpy as np
from ..modem.audio import _import_sounddevice, resolve_device, transmit_waveform

TX_GUARD_SECONDS = 0.25
TX_TAIL_SECONDS = 0.05
REPLY_QUIET_SECONDS = TX_GUARD_SECONDS + TX_TAIL_SECONDS + 0.05

class AudioSession:
    def __init__(self, engine, *, audio_input=None, audio_output=None,
                 ptt=None, turnaround_ms=0, tx_scale=1.0, cancelled=None):
        self.engine = engine
        self.input = audio_input
        self.output = audio_output
        self.ptt = ptt or (lambda state: None)
        self.lead = max(0.15, turnaround_ms / 1000)
        self.tx_scale = min(1.0, max(0.01, tx_scale))
        self.cancelled = cancelled
        self.blocks = queue.Queue(maxsize=100)
        self.sending = False
        self.error = None
        self.stream = None

    def _capture(self, data, frames, timing, status):
        if self.sending:
            return
        if status:
            self.error = RuntimeError(f'ARDOP audio input: {status}')
            return
        try:
            self.blocks.put_nowait(data[:, 0].copy())
        except queue.Full:
            self.error = RuntimeError('ARDOP audio input overflow')

    def __enter__(self):
        self.sd = _import_sounddevice()
        self.out_device = resolve_device(self.output, 'output')
        self.stream = self.sd.InputStream(
            samplerate=48000, blocksize=960, channels=1, dtype='int16',
            device=resolve_device(self.input, 'input'), callback=self._capture)
        try:
            self.stream.start()
        except BaseException:
            self.stream.close()
            raise
        return self

    def __exit__(self, *exc):
        try:
            if self.stream is not None:
                try:
                    self.stream.stop()
                finally:
                    self.stream.close()
        finally:
            self.ptt(False)

    def step(self):
        if self.cancelled is not None and self.cancelled.is_set():
            raise RuntimeError('ARDOP cancelled')
        if self.error:
            raise self.error
        if self.engine.transmitting:
            self.sending = True
            started = time.monotonic()
            try:
                # A decoded frame can end before the peer drains its USB
                # output and releases PTT. Wait in RX before keying our reply;
                # a PTT lead alone would already make us deaf to that tail.
                if self.cancelled is not None:
                    if self.cancelled.wait(REPLY_QUIET_SECONDS):
                        raise RuntimeError('ARDOP cancelled')
                else:
                    time.sleep(REPLY_QUIET_SECONDS)
                while not self.blocks.empty():
                    self.blocks.get_nowait()
                wave = self.engine.waveform().astype(np.float32) / 32768.0
                transmit_waveform(
                    self.sd, wave * self.tx_scale, sample_rate=48000,
                    device=self.out_device, ptt=self.ptt,
                    lead_seconds=self.lead, tail_seconds=TX_TAIL_SECONDS,
                    guard_seconds=TX_GUARD_SECONDS)
                self.engine.finish_transmit(int((time.monotonic() - started) * 48000))
            finally:
                self.sending = False
            return
        try:
            pcm = self.blocks.get(timeout=1.0)
        except queue.Empty as exc:
            raise RuntimeError('ARDOP audio input stopped') from exc
        self.engine.receive(pcm)
