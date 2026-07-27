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
ACQUISITION_PREAMBLE_BITS = 32
MAX_ACQUISITION_ERRORS = 4
MAX_SYNC_ERRORS = 1
CLOCK_SEARCH = np.linspace(0.99, 1.01, 9)
PHASE_SEARCH = np.linspace(0.0, 1.0, 10, endpoint=False)


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
        """Non-coherent, level-normalized FSK confidence per audio sample.

        Comparing raw mark/space power makes a receiver fragile when a radio's
        de-emphasis, speaker path, or sound interface favors one tone. The
        normalized ratio keeps the decision centered between both tones.
        """
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

        mark_power = power(self.mark)
        space_power = power(self.space)
        return (mark_power - space_power) / (
            mark_power + space_power + np.finfo(np.float64).eps
        )

    def demodulate(self, samples: np.ndarray) -> list[bytes]:
        """Return frame candidates found with preamble-aided clock recovery.

        Real sound interfaces do not share an exact clock. A single symbol
        phase chosen across a four-second rolling buffer accumulates timing
        error through a frame and is also easily biased by unrelated VARA
        audio. Search a narrow clock range and require the known alternating
        preamble immediately before the sync word. Candidates representing the
        same on-air burst are ranked by confidence and collapsed to one.
        """
        soft = self._bit_stream(samples)
        if len(soft) < self.sps * (
            ACQUISITION_PREAMBLE_BITS + len(SYNC) * 8 + 8
        ):
            return []
        preamble = _bits_lsb_first(PREAMBLE)[-ACQUISITION_PREAMBLE_BITS:]
        sync = _bits_lsb_first(SYNC)
        acquisition = np.concatenate((preamble, sync))
        acquisition_sign = acquisition * 2 - 1
        sample_axis = np.arange(len(soft))
        candidates: list[tuple[float, float, bytes]] = []

        for clock_scale in CLOCK_SEARCH:
            step = self.sps * float(clock_scale)
            symbol_count = max(0, int((len(soft) - step) / step))
            for phase in PHASE_SEARCH:
                centers = (
                    np.arange(symbol_count, dtype=np.float64)
                    + float(phase)
                    + 0.5
                ) * step
                confidence = np.interp(centers, sample_axis, soft)
                bits = (confidence > 0.0).astype(np.int8)
                if bits.size < acquisition.size + 8:
                    continue
                correlation = np.correlate(
                    bits * 2 - 1,
                    acquisition_sign,
                    mode="valid",
                )
                # Every bit error reduces a +/-1 correlation by two.
                threshold = acquisition.size - 2 * MAX_ACQUISITION_ERRORS
                for start in np.flatnonzero(correlation >= threshold):
                    observed = bits[start : start + acquisition.size]
                    sync_errors = int(
                        np.sum(
                            observed[ACQUISITION_PREAMBLE_BITS:]
                            != sync
                        )
                    )
                    if sync_errors > MAX_SYNC_ERRORS:
                        continue
                    acquisition_errors = int(np.sum(observed != acquisition))
                    if acquisition_errors > MAX_ACQUISITION_ERRORS:
                        continue
                    after = bits[start + acquisition.size :]
                    if after.size < 8:
                        continue
                    length = _bits_to_bytes_lsb_first(after[:8])[0]
                    needed = 8 + int(length) * 8
                    if length < 8 or after.size < needed:
                        continue
                    payload = _bits_to_bytes_lsb_first(after[8:needed])
                    end = start + acquisition.size + needed
                    score = float(np.mean(np.abs(confidence[start:end])))
                    score -= 0.1 * acquisition_errors
                    candidates.append((score, float(centers[start]), payload))

        # Adjacent clock/phase hypotheses describe the same physical burst.
        # Keep the highest-confidence interpretation from each time cluster.
        candidates.sort(key=lambda item: item[0], reverse=True)
        selected: list[tuple[float, float, bytes]] = []
        cluster_radius = self.sps * 80
        for candidate in candidates:
            if any(
                abs(candidate[1] - existing[1]) < cluster_radius
                for existing in selected
            ):
                continue
            selected.append(candidate)
        selected.sort(key=lambda item: item[1])
        return [payload for _score, _position, payload in selected]

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
