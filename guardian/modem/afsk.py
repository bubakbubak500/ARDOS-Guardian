"""AFSK 1200 (Bell 202) modem in numpy.

Mark = 1200 Hz, Space = 2200 Hz, 1200 baud. Phase-continuous FSK on TX;
non-coherent correlator demod on RX.

On-air framing (LSB-first within each byte):

    preamble : 0x55 * N      alternating tones, lets RX settle + find bit edges
    sync     : 0x2D 0xD4     two-byte unique sync word
    length   : 1 byte        number of payload bytes that follow (0..255)
    payload  : the frame bytes (e.g. ControlFrame.encode(), already CRC'd)

This is a clean custom framing (not AX.25/APRS-compatible) — Guardian defines
its own control protocol, so we don't need HDLC/NRZI/bit-stuffing.
"""

from __future__ import annotations

import numpy as np

MARK = 1200.0
SPACE = 2200.0
BAUD = 1200.0
SYNC = bytes((0x2D, 0xD4))
PREAMBLE = b"\x55" * 24
POSTAMBLE_BITS = 8  # trailing mark bits so the last symbol is clean


def _bits_lsb_first(data: bytes) -> np.ndarray:
    """Expand bytes to a bit array, least-significant-bit first."""
    arr = np.frombuffer(data, dtype=np.uint8)
    bits = np.unpackbits(arr[:, None], axis=1, bitorder="little").reshape(-1)
    return bits.astype(np.int8)


def _bits_to_bytes_lsb_first(bits: np.ndarray) -> bytes:
    n = (len(bits) // 8) * 8
    bits = bits[:n].astype(np.uint8).reshape(-1, 8)
    out = np.packbits(bits, axis=1, bitorder="little").reshape(-1)
    return out.tobytes()


class AFSKModem:
    name = "afsk1200"

    def __init__(self, sample_rate: int = 48000, mark: float = MARK,
                 space: float = SPACE, baud: float = BAUD):
        self.fs = int(sample_rate)
        self.mark = mark
        self.space = space
        self.baud = baud
        self.sps = self.fs / self.baud  # samples per symbol (may be fractional)

    # ------------------------------------------------------------------ #
    #  Transmit                                                           #
    # ------------------------------------------------------------------ #
    def modulate(self, payload: bytes, amplitude: float = 0.7) -> np.ndarray:
        if len(payload) > 255:
            raise ValueError("control payload must be <= 255 bytes")
        frame = PREAMBLE + SYNC + bytes((len(payload),)) + payload
        bits = _bits_lsb_first(frame)
        bits = np.concatenate([bits, np.ones(POSTAMBLE_BITS, dtype=np.int8)])

        # Per-sample instantaneous frequency from the bit stream.
        total = int(np.ceil(len(bits) * self.sps))
        idx = (np.arange(total) / self.sps).astype(int)
        idx = np.clip(idx, 0, len(bits) - 1)
        freqs = np.where(bits[idx] == 1, self.mark, self.space)

        phase = np.cumsum(2 * np.pi * freqs / self.fs)
        sig = amplitude * np.sin(phase)
        # Short raised-cosine fade in/out to avoid clicks keying the rig.
        fade = min(64, total // 10)
        if fade > 1:
            w = np.hanning(2 * fade)
            sig[:fade] *= w[:fade]
            sig[-fade:] *= w[fade:]
        return sig.astype(np.float32)

    # ------------------------------------------------------------------ #
    #  Receive                                                            #
    # ------------------------------------------------------------------ #
    def _bit_stream(self, samples: np.ndarray) -> np.ndarray:
        """Non-coherent FSK detection -> one soft value per sample (>0 = mark)."""
        x = np.asarray(samples, dtype=np.float64)
        n = np.arange(len(x))
        # Correlate against mark/space (I/Q) over a one-symbol sliding window.
        win = max(1, int(round(self.sps)))
        kernel = np.ones(win)

        def power(freq: float) -> np.ndarray:
            i = np.cos(2 * np.pi * freq * n / self.fs) * x
            q = np.sin(2 * np.pi * freq * n / self.fs) * x
            ci = np.convolve(i, kernel, mode="same")
            cq = np.convolve(q, kernel, mode="same")
            return ci * ci + cq * cq

        return power(self.mark) - power(self.space)

    def demodulate(self, samples: np.ndarray) -> list[bytes]:
        """Return every well-formed payload found in the audio buffer."""
        soft = self._bit_stream(samples)
        sps = self.sps
        if len(soft) < sps * 16:
            return []
        n_syms = int(len(soft) / sps) - 1
        if n_syms < 16:
            return []

        # Bit timing: search the fractional symbol phase that yields the most
        # confident slicing (largest mean magnitude at the sampling instants),
        # averaging a window around each symbol centre for noise immunity.
        half = max(1, int(sps * 0.3))
        best_vals = None
        best_score = -1.0
        for p in np.linspace(0.0, 1.0, 16, endpoint=False):
            centers = ((np.arange(n_syms) + p + 0.5) * sps).astype(int)
            centers = np.clip(centers, half, len(soft) - half - 1)
            vals = np.array([soft[c - half:c + half + 1].mean() for c in centers])
            score = float(np.mean(np.abs(vals)))
            if score > best_score:
                best_score, best_vals = score, vals

        bits = (best_vals > 0).astype(np.int8)
        return self._extract_frames(bits)

    def _extract_frames(self, bits: np.ndarray, max_sync_errors: int = 1) -> list[bytes]:
        sync_bits = _bits_lsb_first(SYNC)
        L = len(sync_bits)
        results: list[bytes] = []
        i = 0
        limit = len(bits) - L
        while i <= limit:
            # Tolerate a small number of bit errors in the sync word.
            if int(np.sum(bits[i:i + L] != sync_bits)) <= max_sync_errors:
                after = bits[i + L:]
                if len(after) >= 8:
                    length = _bits_to_bytes_lsb_first(after[:8])[0]
                    need_bits = 8 + length * 8
                    if len(after) >= need_bits:
                        payload = _bits_to_bytes_lsb_first(after[8:need_bits])
                        results.append(payload)
                        i += L + need_bits
                        continue
            i += 1
        return results
