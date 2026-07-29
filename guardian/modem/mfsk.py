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

from collections.abc import Callable

import numpy as np

from .afsk import SYNC, _bits_lsb_first, _bits_to_bytes_lsb_first
from .fec import K, conv_encode, viterbi_decode, viterbi_decode_soft

M = 16
BITS_PER_SYM = 4
N_PRE = 8          # preamble symbols
MARKER = (0, M - 1)  # 2-symbol sync marker after the preamble
# Orthogonal MFSK ties tone spacing, symbol rate and window length together:
# spacing = baud = sample_rate / n_per_symbol. The window must therefore be
# derived from the device's sample rate, not fixed -- a 256-sample window is
# 31.25 Hz at 8 kHz but 187.5 Hz at the 48 kHz the sound card actually runs at,
# which spreads the 16 tones over 600-3412 Hz and pushes the top three outside
# any SSB filter. Measured on air 2026-07-29: the tones arrived at 600/975/
# 1162/2287 Hz and everything above 2900 Hz was gone, so the preamble (which
# alternates tone 0 and tone 15) never survived and no frame ever decoded.
# Geometry. Orthogonal MFSK forces spacing == baud == sample_rate / window, so
# these three move together. 125 Hz over 16 tones occupies 400-2275 Hz: clear of
# the ~300 Hz low-end rolloff some radios have, well inside a 2.4 kHz SSB
# filter, and four times faster than the original 31.25 Hz grid. Crucially it
# also makes the receiver 4x less sensitive to frequency error -- the -8.5 Hz
# offset measured on air was 27% of a 31.25 Hz spacing but is 7% of this one.
DEFAULT_SPACING = 125.0   # Hz, and therefore also the baud rate
DEFAULT_BASE_FREQ = 400.0
# Widest receiver frequency error corrected for, and how much signal the fit
# uses. +-0.5 ppm at 21 MHz is ~10 Hz per radio, so 25 covers two of them.
MAX_OFFSET_HZ = 25.0
OFFSET_FIT_SYMBOLS = 64
FEC_FLUSH_BITS = K - 1    # conv_encode appends K-1 flush bits before rate 1/2


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

    def __init__(self, sample_rate: int = 8000, n_per_symbol: int | None = None,
                 base_freq: float = DEFAULT_BASE_FREQ,
                 spacing_hz: float = DEFAULT_SPACING):
        self.fs = int(sample_rate)
        # Keep the on-air geometry fixed and let the window follow the device.
        self.N = int(n_per_symbol or round(self.fs / spacing_hz))
        self.base = base_freq
        self.spacing = self.fs / self.N            # orthogonal tone spacing
        self.tones = self.base + np.arange(M) * self.spacing
        # Precompute per-tone reference vectors for one symbol window.
        n = np.arange(self.N)
        self._ref = np.exp(-2j * np.pi * np.outer(self.tones, n) / self.fs)  # (M, N)
        # Preamble references for the burst search: only two tones are needed.
        self._pre_ref = self._ref[[MARKER[0], MARKER[1]], :]
        self.last_offset_hz = 0.0   # frequency error measured on the last frame
        # Bit b of a symbol is 1 for these tone indices; used for soft output.
        self._bit_ones = [
            np.array([t for t in range(M)
                      if (_UNGRAY[t] >> (BITS_PER_SYM - 1 - b)) & 1])
            for b in range(BITS_PER_SYM)
        ]
        self._bit_zeros = [
            np.array([t for t in range(M) if t not in set(ones.tolist())])
            for ones in self._bit_ones
        ]

    @property
    def baud(self) -> float:
        return self.fs / self.N

    def airtime(self, payload_bytes: int) -> float:
        """Seconds on air for a frame of this payload size."""
        framing_bits = (len(SYNC) + 1 + payload_bytes) * 8
        coded_bits = 2 * (framing_bits + FEC_FLUSH_BITS)
        symbols = N_PRE + len(MARKER) + -(-coded_bits // BITS_PER_SYM)
        return symbols / self.baud

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
    def _symbol_energies(
        self, samples: np.ndarray, start: int, ref: np.ndarray | None = None
    ) -> np.ndarray | None:
        seg = samples[start:start + self.N]
        if len(seg) < self.N:
            return None
        return np.abs((self._ref if ref is None else ref) @ seg)

    def _find_preamble(self, x: np.ndarray) -> int:
        """Start of the alternating tone0/toneM-1 preamble, anywhere in x."""
        span = len(x) - (N_PRE + 2) * self.N
        if span <= 0:
            return 0
        best, best_score = 0, -1.0
        for start in range(0, span, max(1, self.N // 8)):
            score = 0.0
            for k in range(N_PRE):
                seg = x[start + k * self.N:start + (k + 1) * self.N]
                if len(seg) < self.N:
                    break
                low, high = np.abs(self._pre_ref @ seg)
                total = low + high + 1e-12
                score += (low if k % 2 == 0 else high) / total
            score /= N_PRE
            if score > best_score:
                best_score, best = score, start
        return best

    def _reference_at(self, offset_hz: float) -> np.ndarray:
        n = np.arange(self.N)
        return np.exp(-2j * np.pi * np.outer(self.tones + offset_hz, n) / self.fs)

    def estimate_offset(self, samples: np.ndarray, start: int) -> float:
        """Fit the 16-tone grid to the located frame to measure dial error.

        Two radios each inside a +-0.5 ppm TCXO spec differ by ~21 Hz at
        21 MHz. That cannot be tuned away, so the receiver has to measure it.
        """
        span = min(len(samples) - start, OFFSET_FIT_SYMBOLS * self.N)
        if span < 4 * self.N:
            return 0.0
        seg = samples[start:start + span]
        size = 1 << int(np.ceil(np.log2(len(seg) * 2)))
        spectrum = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), n=size))
        bin_hz = self.fs / size
        best_offset, best_score = 0.0, -1.0
        for offset in np.arange(-MAX_OFFSET_HZ, MAX_OFFSET_HZ + 0.5, 0.5):
            bins = np.rint((self.tones + offset) / bin_hz).astype(int)
            bins = bins[(bins >= 0) & (bins < len(spectrum))]
            score = float(spectrum[bins].sum())
            if score > best_score:
                best_score, best_offset = score, float(offset)
        return best_offset

    def demodulate(
        self,
        samples: np.ndarray,
        validator: Callable[[bytes], bool] | None = None,
    ) -> list[bytes]:
        x = np.asarray(samples, dtype=np.float64)
        if len(x) < self.N * (N_PRE + 4):
            return []

        # Locate the preamble anywhere in the window. The RX buffer is a
        # rolling window many times longer than a frame; the old search covered
        # only its first two symbols and was finding frames by luck. On air the
        # frame sat 3.5 s in, and correlating just the two preamble tones finds
        # it with a 0.97 match for a fraction of the cost of a full search.
        best_off = self._find_preamble(x)

        # Refine symbol timing within a symbol of that.
        step = max(1, self.N // 16)
        best_score = -1.0
        lo = max(0, best_off - self.N // 2)
        for off in range(lo, lo + self.N, step):
            score, count = 0.0, 0
            for k in range(4):
                e = self._symbol_energies(x, off + k * self.N)
                if e is None:
                    break
                total = e.sum()
                if total > 0:
                    score += e.max() / total
                    count += 1
            if count:
                score /= count
            if score > best_score:
                best_score, best_off = score, off

        # Correct the two stations' frequency error before reading any symbol.
        offset = self.estimate_offset(x, best_off)
        self.last_offset_hz = offset
        ref = self._ref if offset == 0.0 else self._reference_at(offset)

        # Find the 2-symbol marker (tone0, toneM-1) right after the preamble to
        # lock byte/symbol framing, scanning a few symbol positions.
        data_start = None
        for s in range(N_PRE - 2, N_PRE + 4):
            a = self._symbol_energies(x, best_off + s * self.N, ref)
            b = self._symbol_energies(x, best_off + (s + 1) * self.N, ref)
            if a is None or b is None:
                break
            if int(np.argmax(a)) == MARKER[0] and int(np.argmax(b)) == MARKER[1]:
                data_start = best_off + (s + 2) * self.N
                break
        if data_start is None:
            data_start = best_off + (N_PRE + 2) * self.N

        # Demodulate data symbols, keeping how sure each bit decision was.
        soft: list[float] = []
        pos = data_start
        while pos + self.N <= len(x):
            e = self._symbol_energies(x, pos, ref)
            if e is None:
                break
            scale = e.max() + 1e-12
            for b in range(BITS_PER_SYM):
                ones = e[self._bit_ones[b]].max()
                zeros = e[self._bit_zeros[b]].max()
                soft.append((ones - zeros) / scale)
            pos += self.N

        if len(soft) < 16:
            return []
        soft = np.array(soft)
        frames = self._extract_frames(viterbi_decode_soft(soft))
        # That stream runs from the frame to the end of the RX window, so the
        # trellis is never terminated where the frame ends and the traceback
        # starts from a state chosen by noise. On air the body came through
        # perfectly every time and only the trailing CRC bytes were corrupt.
        # The length byte tells us the frame's true extent, so decode it again
        # over exactly those bits, where the flush bits can do their job.
        bounded = [
            frame
            for frame in (self._decode_bounded(soft, at, length)
                          for at, length in self._frame_positions(soft, frames))
            if frame is not None
        ]
        frames = bounded + [f for f in frames if f not in bounded]
        if validator is not None:
            valid = [payload for payload in frames if validator(payload)]
            if valid:
                return valid
        return frames

    def _frame_positions(self, soft: np.ndarray, frames: list[bytes]):
        """(info-bit index of SYNC, payload length) for each frame candidate."""
        bits = viterbi_decode_soft(soft)
        sync_bits = _bits_lsb_first(SYNC)
        L = len(sync_bits)
        i = 0
        while i <= len(bits) - L - 8:
            if int(np.sum(bits[i:i + L] != sync_bits)) == 0:
                length = int(_bits_to_bytes_lsb_first(bits[i + L:i + L + 8])[0])
                if length:
                    yield i, length
                i += L
            else:
                i += 1

    def _decode_bounded(
        self, soft: np.ndarray, info_start: int, length: int
    ) -> bytes | None:
        """Re-decode one frame with the trellis terminated at its true end."""
        need = 2 * ((len(SYNC) + 1 + length) * 8 + FEC_FLUSH_BITS)
        chunk = soft[2 * info_start:2 * info_start + need]
        if len(chunk) < need:
            return None
        found = self._extract_frames(viterbi_decode_soft(chunk))
        return found[0] if found else None

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
