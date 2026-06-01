"""MFSK-16 modem for the HF/SSB control channel.

16 orthogonal tones (4 bits/symbol), Gray-coded so a tone slip costs only one
bit, protected by the rate-1/2 K=7 convolutional FEC. Narrow (~500 Hz) and slow
enough to ride a fading SSB channel where AFSK has no chance.

On-air structure:
    preamble : alternating low/high tone, N_PRE symbols (AGC + coarse timing)
    sync sym : tone 0 then tone M-1 (a known 2-symbol marker)
    data     : FEC(framing) where framing = SYNC2 + length + payload bytes

The byte-level SYNC word + the frame's own CRC (checked by ControlFrame.decode)
mean a mis-decode is rejected rather than mistaken for a valid frame.
"""

from __future__ import annotations

import numpy as np

from .afsk import SYNC, _bits_lsb_first, _bits_to_bytes_lsb_first
from .fec import conv_encode, viterbi_decode

M = 16
BITS_PER_SYM = 4
N_PRE = 8          # preamble symbols
MARKER = (0, M - 1)  # 2-symbol sync marker after the preamble


def _gray(n: int) -> int:
    return n ^ (n >> 1)


def _ungray(g: int) -> int:
    n = 0
    while g:
        n ^= g
        g >>= 1
    return n


_GRAY = [_gray(i) for i in range(M)]            # symbol value -> tone index
_UNGRAY = {_GRAY[i]: i for i in range(M)}        # tone index -> symbol value


class MFSKModem:
    name = "mfsk16"

    def __init__(self, sample_rate: int = 8000, n_per_symbol: int = 256,
                 base_freq: float = 600.0):
        self.fs = int(sample_rate)
        self.N = int(n_per_symbol)
        self.base = base_freq
        self.spacing = self.fs / self.N            # orthogonal tone spacing
        self.tones = self.base + np.arange(M) * self.spacing
        # Precompute per-tone reference vectors for one symbol window.
        n = np.arange(self.N)
        self._ref = np.exp(-2j * np.pi * np.outer(self.tones, n) / self.fs)  # (M, N)

    @property
    def baud(self) -> float:
        return self.fs / self.N

    # ------------------------------------------------------------------ #
    #  Transmit                                                           #
    # ------------------------------------------------------------------ #
    def modulate(self, payload: bytes, amplitude: float = 0.7) -> np.ndarray:
        if len(payload) > 255:
            raise ValueError("control payload must be <= 255 bytes")
        framing = SYNC + bytes((len(payload),)) + payload
        bits = _bits_lsb_first(framing)
        coded = conv_encode(bits)
        # Pad coded bits to a whole number of 4-bit symbols.
        pad = (-len(coded)) % BITS_PER_SYM
        if pad:
            coded = np.concatenate([coded, np.zeros(pad, dtype=np.int8)])
        nibbles = coded.reshape(-1, BITS_PER_SYM)
        sym_vals = nibbles[:, 0] * 8 + nibbles[:, 1] * 4 + nibbles[:, 2] * 2 + nibbles[:, 3]
        data_tones = [_GRAY[int(v)] for v in sym_vals]

        pre = [0 if i % 2 == 0 else M - 1 for i in range(N_PRE)]
        tone_seq = pre + list(MARKER) + data_tones
        return self._synthesize(tone_seq, amplitude)

    def _synthesize(self, tone_indices, amplitude: float) -> np.ndarray:
        n = np.arange(self.N)
        phase = 0.0
        out = np.empty(len(tone_indices) * self.N, dtype=np.float64)
        for k, ti in enumerate(tone_indices):
            f = self.tones[ti]
            seg = np.sin(2 * np.pi * f * n / self.fs + phase)
            phase = (phase + 2 * np.pi * f * self.N / self.fs) % (2 * np.pi)  # continuity
            out[k * self.N:(k + 1) * self.N] = seg
        sig = amplitude * out
        fade = min(64, self.N // 4)
        if fade > 1:
            w = np.hanning(2 * fade)
            sig[:fade] *= w[:fade]
            sig[-fade:] *= w[fade:]
        return sig.astype(np.float32)

    # ------------------------------------------------------------------ #
    #  Receive                                                            #
    # ------------------------------------------------------------------ #
    def _symbol_energies(self, samples: np.ndarray, start: int) -> np.ndarray | None:
        seg = samples[start:start + self.N]
        if len(seg) < self.N:
            return None
        return np.abs(self._ref @ seg)  # (M,) magnitude at each tone

    def demodulate(self, samples: np.ndarray) -> list[bytes]:
        x = np.asarray(samples, dtype=np.float64)
        if len(x) < self.N * (N_PRE + 4):
            return []

        # Coarse symbol-timing search: find the start offset whose first symbols
        # are most "tone-like" (one bin dominating). Search across one symbol.
        best_off, best_score = 0, -1.0
        step = max(1, self.N // 16)
        probe = min(len(x) - self.N * 4, self.N * 2)
        for off in range(0, max(1, probe), step):
            score = 0.0
            cnt = 0
            for s in range(4):
                e = self._symbol_energies(x, off + s * self.N)
                if e is None:
                    break
                tot = e.sum()
                if tot > 0:
                    score += e.max() / tot
                    cnt += 1
            if cnt:
                score /= cnt
            if score > best_score:
                best_score, best_off = score, off

        # Find the 2-symbol marker (tone0, toneM-1) right after the preamble to
        # lock byte/symbol framing, scanning a few symbol positions.
        data_start = None
        for s in range(N_PRE - 2, N_PRE + 4):
            a = self._symbol_energies(x, best_off + s * self.N)
            b = self._symbol_energies(x, best_off + (s + 1) * self.N)
            if a is None or b is None:
                break
            if int(np.argmax(a)) == MARKER[0] and int(np.argmax(b)) == MARKER[1]:
                data_start = best_off + (s + 2) * self.N
                break
        if data_start is None:
            data_start = best_off + (N_PRE + 2) * self.N

        # Demodulate data symbols to coded bits.
        coded_bits: list[int] = []
        pos = data_start
        while pos + self.N <= len(x):
            e = self._symbol_energies(x, pos)
            if e is None:
                break
            tone = int(np.argmax(e))
            val = _UNGRAY.get(tone, 0)
            coded_bits.extend(((val >> 3) & 1, (val >> 2) & 1, (val >> 1) & 1, val & 1))
            pos += self.N

        if len(coded_bits) < 16:
            return []
        info = viterbi_decode(np.array(coded_bits, dtype=np.int8))
        return self._extract_frames(info)

    def _extract_frames(self, bits: np.ndarray) -> list[bytes]:
        sync_bits = _bits_lsb_first(SYNC)
        L = len(sync_bits)
        results: list[bytes] = []
        i = 0
        limit = len(bits) - L
        while i <= limit:
            if int(np.sum(bits[i:i + L] != sync_bits)) == 0:
                after = bits[i + L:]
                if len(after) >= 8:
                    length = _bits_to_bytes_lsb_first(after[:8])[0]
                    need = 8 + length * 8
                    if len(after) >= need:
                        results.append(_bits_to_bytes_lsb_first(after[8:need]))
                        i += L + need
                        continue
            i += 1
        return results
