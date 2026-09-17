"""Guardian control packets carried by ARDOP 4PSK.200.100 FEC frames."""
import numpy as np
from ..ardop import Engine

class ArdopControlModem:
    name = 'ardop500'

    def __init__(self, sample_rate=48000):
        if sample_rate != 48000:
            raise ValueError('ARDOP control uses 48000 Hz audio')
        self.fs = sample_rate
        self.tx_scale = 1.0

    def airtime(self, payload_bytes):
        if not 1 <= payload_bytes <= 64:
            raise ValueError('ARDOP control supports 1..64 bytes')
        return 4.52  # fixed 64-byte PHY frame including 300 ms leader

    def modulate(self, payload):
        with Engine(receive_only=True) as engine:
            engine.fec(payload)
            return engine.waveform().astype(np.float32) / 32768.0 * min(1.0, max(0.01, self.tx_scale))

    def demodulate(self, samples, validator=None):
        pcm = np.rint(np.clip(samples, -1, 1) * 32767).astype(np.int16)
        found = []
        with Engine(receive_only=True) as engine:
            for i in range(0, len(pcm) - len(pcm) % 4, 960):
                block = pcm[i:min(i + 960, len(pcm) - len(pcm) % 4)]
                engine.receive(block)
                data = engine.read()
                if data and (validator is None or validator(data)):
                    found.append(data)
        return found
