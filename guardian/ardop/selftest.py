"""Headless test of the actually packaged native modem; never opens hardware."""
import numpy as np
from .engine import Engine

def run():
    payload = bytes(range(256))
    received = bytearray()
    with Engine('N0AAA') as a, Engine('N0BBB') as b:
        a.command(3, data=payload)
        a.command(0, target='N0BBB')
        for _ in range(100):
            for tx, rx in ((a, b), (b, a)):
                if tx.transmitting:
                    wave = tx.waveform()
                    tx.finish_transmit(len(wave))
                    wave = np.concatenate((wave, np.zeros(24000, dtype=np.int16)))
                    for i in range(0, len(wave), 960):
                        rx.receive(wave[i:i+960])
            received.extend(b.read())
            if received == payload and a.queued == 0:
                return 'ARDOP ABI 1: native 500 Hz ARQ roundtrip OK (256 binary bytes, two independent instances)\n'
            if not a.transmitting and not b.transmitting:
                for _ in range(50):
                    a.receive(np.zeros(960, dtype=np.int16))
                    b.receive(np.zeros(960, dtype=np.int16))
    raise RuntimeError('ARDOP native self-test failed')
