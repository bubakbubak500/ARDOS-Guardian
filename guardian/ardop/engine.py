"""Small versioned ctypes boundary. One worker owns each Engine."""
from __future__ import annotations

import ctypes as C
from pathlib import Path
import sys
import threading
import numpy as np

_library = None
_load_lock = threading.Lock()
_PCM = np.ctypeslib.ndpointer(dtype=np.int16, ndim=1, flags='C_CONTIGUOUS')

def library():
    global _library
    with _load_lock:
        if _library is not None:
            return _library
        name = 'guardian_ardop.dll' if sys.platform == 'win32' else 'libguardian_ardop.so'
        root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
        path = root / 'ardop' / name if getattr(sys, 'frozen', False) else root / 'native/ardop/bin' / name
        if not path.is_file():
            raise RuntimeError('ARDOP library missing: run tools/build_ardop.py')
        lib = C.CDLL(str(path))
        signatures = {
            'ga_abi': (C.c_int, []),
            'ga_create': (C.c_void_p, [C.c_char_p, C.c_int]),
            'ga_destroy': (None, [C.c_void_p]),
            'ga_rx': (C.c_int, [C.c_void_p, _PCM, C.c_int]),
            'ga_tx': (C.c_int, [C.c_void_p, _PCM, C.c_int]),
            'ga_finish_tx': (None, [C.c_void_p, C.c_int]),
            'ga_command': (C.c_int, [C.c_void_p, C.c_int, C.c_char_p, C.c_char_p, C.c_int]),
            'ga_read': (C.c_int, [C.c_void_p, C.c_void_p, C.c_int]),
            'ga_status': (C.c_int, [C.c_void_p, C.c_int]),
            'ga_remote': (C.c_int, [C.c_void_p, C.c_void_p, C.c_int]),
            'ga_fec': (C.c_int, [C.c_void_p, C.c_char_p, C.c_int, C.c_int]),
        }
        for name, (result, args) in signatures.items():
            fn = getattr(lib, name)
            fn.restype, fn.argtypes = result, args
        if lib.ga_abi() != 1:
            raise RuntimeError('Incompatible ARDOP library ABI')
        _library = lib
        return lib

class Engine:
    def __init__(self, callsign='N0CALL', *, receive_only=False):
        self.lib = library()
        self.handle = self.lib.ga_create(callsign.strip().upper().encode('ascii'), int(receive_only))
        if not self.handle:
            raise ValueError(f'Invalid ARDOP callsign or modem initialization failed: {callsign}')

    def close(self):
        if self.handle:
            self.lib.ga_destroy(self.handle)
            self.handle = None

    def __enter__(self): return self
    def __exit__(self, *exc): self.close()

    def _handle(self):
        if not self.handle:
            raise RuntimeError('ARDOP engine is closed')
        return self.handle

    def command(self, kind, *, target=None, data=b''):
        target = target.upper().encode('ascii') if target is not None else None
        if self.lib.ga_command(self._handle(), kind, target, data, len(data)) < 0:
            raise ValueError('ARDOP command rejected (callsign or full TX queue)')

    def receive(self, pcm):
        pcm = np.ascontiguousarray(pcm, dtype=np.int16)
        if pcm.ndim != 1 or len(pcm) > 4096 or len(pcm) % 4:
            raise ValueError('ARDOP expects mono 48 kHz PCM blocks, <=4096, divisible by 4')
        if self.lib.ga_rx(self._handle(), pcm, len(pcm)) < 0:
            raise RuntimeError('ARDOP receive overflow')

    def transmit(self, count=960):
        if count < 0 or count > 4096 or count % 4:
            raise ValueError('Invalid ARDOP transmit block')
        out = np.zeros(count, dtype=np.int16)
        if self.lib.ga_tx(self._handle(), out, count) < 0:
            raise RuntimeError('ARDOP transmit failed')
        return out

    def read(self):
        buffer = C.create_string_buffer(65536)
        n = self.lib.ga_read(self._handle(), buffer, len(buffer))
        if n < 0:
            raise RuntimeError('ARDOP receive overflow')
        return buffer.raw[:n]

    def status(self, field): return self.lib.ga_status(self._handle(), field)
    @property
    def state(self): return self.status(0)
    @property
    def transmitting(self): return bool(self.status(1))
    @property
    def queued(self): return self.status(2)
    @property
    def remote(self):
        buffer = C.create_string_buffer(32)
        if self.lib.ga_remote(self._handle(), buffer, 32) < 0:
            raise RuntimeError('Invalid ARDOP remote callsign')
        return buffer.value.decode('ascii')

    def fec(self, data, *, frame_type=0x40):
        if self.lib.ga_fec(self._handle(), data, len(data), frame_type) < 0:
            raise ValueError('Invalid ARDOP FEC frame or payload length')

    def waveform(self):
        chunks = []
        while self.status(6):
            chunks.append(self.transmit())
        chunks.append(self.transmit())  # flush the interpolation filter
        return np.concatenate(chunks)

    def finish_transmit(self, elapsed_samples):
        self.lib.ga_finish_tx(self._handle(), elapsed_samples)
