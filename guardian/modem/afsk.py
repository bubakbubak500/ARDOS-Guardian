"""AFSK 1200 (Bell 202) modem in numpy.

Mark = 1200 Hz, Space = 2200 Hz, 1200 baud. Phase-continuous FSK on TX;
non-coherent correlator demod on RX.

On-air framing (LSB-first within each byte):

    preamble : 0x55 * N      alternating tones, lets RX settle + find bit edges
    sync     : unique sync marker
    length   : repeated three times
    payload  : repeated three times and recovered by bitwise majority vote

This is a clean custom framing (not AX.25/APRS-compatible) — Guardian defines
its own control protocol, so we don't need HDLC/NRZI/bit-stuffing.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

MARK = 1200.0
SPACE = 2200.0
BAUD = 1200.0
LEGACY_SYNC = bytes((0x2D, 0xD4))
FEC_SYNC = bytes((0x69, 0x96, 0xC3, 0x3C))
SYNC = LEGACY_SYNC  # compatibility for callers importing the old name
FEC_REPETITIONS = 3
PREAMBLE = b"\x55" * 24
POSTAMBLE_BITS = 32  # keep the radio modulator settled through the USB/PTT tail
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

    def airtime(self, payload_bytes: int) -> float:
        """Seconds on air for a frame of this payload size."""
        frame_bytes = (
            len(PREAMBLE)
            + len(FEC_SYNC)
            + (1 + payload_bytes) * FEC_REPETITIONS
        )
        return (frame_bytes * 8 + POSTAMBLE_BITS) / self.baud

    # ------------------------------------------------------------------ #
    #  Transmit                                                           #
    # ------------------------------------------------------------------ #
    def modulate(self, payload: bytes, amplitude: float = 0.4) -> np.ndarray:
        if len(payload) > 255:
            raise ValueError("control payload must be <= 255 bytes")
        frame = (
            PREAMBLE
            + FEC_SYNC
            + bytes((len(payload),)) * FEC_REPETITIONS
            + payload * FEC_REPETITIONS
        )
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

    def _select_candidates(
        self,
        candidates: list[tuple[float, float, bytes]],
        validator: Callable[[bytes], bool] | None = None,
    ) -> list[bytes]:
        """Collapse clock hypotheses while retaining a CRC-valid payload."""
        cluster_radius = self.sps * 80
        clusters: list[list[tuple[float, float, bytes]]] = []
        for candidate in sorted(candidates, key=lambda item: item[1]):
            cluster = next(
                (
                    existing
                    for existing in clusters
                    if abs(candidate[1] - existing[0][1]) < cluster_radius
                ),
                None,
            )
            if cluster is None:
                clusters.append([candidate])
            else:
                cluster.append(candidate)

        selected: list[tuple[float, float, bytes]] = []
        for cluster in clusters:
            ranked = sorted(cluster, key=lambda item: item[0], reverse=True)
            chosen = ranked[0]
            if validator is not None:
                chosen = next(
                    (candidate for candidate in ranked if validator(candidate[2])),
                    chosen,
                )
            selected.append(chosen)
        selected.sort(key=lambda item: item[1])
        return [payload for _score, _position, payload in selected]

    def demodulate(
        self,
        samples: np.ndarray,
        validator: Callable[[bytes], bool] | None = None,
    ) -> list[bytes]:
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
            ACQUISITION_PREAMBLE_BITS + len(LEGACY_SYNC) * 8 + 8
        ):
            return []
        preamble = _bits_lsb_first(PREAMBLE)[-ACQUISITION_PREAMBLE_BITS:]
        sample_axis = np.arange(len(soft))
        candidates: list[tuple[float, float, bytes]] = []
        formats = (
            (_bits_lsb_first(FEC_SYNC), True),
            (_bits_lsb_first(LEGACY_SYNC), False),
        )

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
                for sync, has_fec in formats:
                    acquisition = np.concatenate((preamble, sync))
                    if bits.size < acquisition.size + 8:
                        continue
                    correlation = np.correlate(
                        bits * 2 - 1,
                        acquisition * 2 - 1,
                        mode="valid",
                    )
                    threshold = acquisition.size - 2 * MAX_ACQUISITION_ERRORS
                    for start in np.flatnonzero(correlation >= threshold):
                        observed = bits[start : start + acquisition.size]
                        sync_errors = int(np.sum(
                            observed[ACQUISITION_PREAMBLE_BITS:] != sync
                        ))
                        if sync_errors > MAX_SYNC_ERRORS:
                            continue
                        acquisition_errors = int(np.sum(observed != acquisition))
                        if acquisition_errors > MAX_ACQUISITION_ERRORS:
                            continue
                        after = bits[start + acquisition.size :]
                        decoded = (
                            self._decode_fec_payload(after)
                            if has_fec
                            else self._decode_legacy_payload(after)
                        )
                        if decoded is None:
                            continue
                        payload, needed = decoded
                        end = start + acquisition.size + needed
                        score = float(np.mean(np.abs(confidence[start:end])))
                        score -= 0.1 * acquisition_errors
                        candidates.append(
                            (score, float(centers[start]), payload)
                        )

        # Adjacent clock/phase hypotheses describe the same physical burst.
        # A radio path can give a slightly mistimed hypothesis more energy than
        # the correct one. When the caller knows the payload format, prefer a
        # hypothesis that passes its integrity check (ControlFrame CRC) instead
        # of discarding it before validation.
        return self._select_candidates(candidates, validator)

    @staticmethod
    def _majority_bits(copies: np.ndarray) -> np.ndarray:
        threshold = copies.shape[0] // 2 + 1
        return (np.sum(copies, axis=0) >= threshold).astype(np.int8)

    def _decode_fec_payload(
        self,
        after: np.ndarray,
    ) -> tuple[bytes, int] | None:
        length_bits = 8 * FEC_REPETITIONS
        if after.size < length_bits:
            return None
        length_copies = after[:length_bits].reshape(FEC_REPETITIONS, 8)
        length = _bits_to_bytes_lsb_first(
            self._majority_bits(length_copies)
        )[0]
        payload_bits = int(length) * 8
        needed = length_bits + payload_bits * FEC_REPETITIONS
        if length < 8 or after.size < needed:
            return None
        copies = after[length_bits:needed].reshape(
            FEC_REPETITIONS,
            payload_bits,
        )
        payload = _bits_to_bytes_lsb_first(self._majority_bits(copies))
        return payload, needed

    @staticmethod
    def _decode_legacy_payload(
        after: np.ndarray,
    ) -> tuple[bytes, int] | None:
        if after.size < 8:
            return None
        length = _bits_to_bytes_lsb_first(after[:8])[0]
        needed = 8 + int(length) * 8
        if length < 8 or after.size < needed:
            return None
        return _bits_to_bytes_lsb_first(after[8:needed]), needed

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
