"""Physical layer for the production SC-FTN waveform.

The modulator and receiver produce ordinary real audio and expose the compact
contract consumed by :mod:`guardian.waveforms.framing`. Timing, CFO, pilot
tracking and MMSE equalisation are kept here so byte framing remains independent
of DSP details.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from ..ofdm.metrics import LinkMetrics
from ..ofdm.phy import analytic
from .config import WaveformProfile

_SEED = 0x47573231
_SC_PREAMBLE_HALF = 64
_SC_TRAINING = 96
# The learned 81-tap equalizer consumes 40 symbol decisions at each edge.  A
# shorter tail silently zero-filled the end of the final codeword: CRC often
# survived through FEC, but EVM/GMI were badly biased and dense constellations
# paid for deterministic erasures.  Forty-eight covers the largest configured
# radius with a small timing margin and is paid once per physical burst.
_SC_GUARD = 48


def _bpsk(rng: np.random.Generator, count: int) -> np.ndarray:
    return (2.0 * rng.integers(0, 2, count) - 1.0).astype(np.complex128)


def _normalise(waveform: np.ndarray, target_rms: float) -> np.ndarray:
    waveform = np.asarray(waveform, dtype=np.float64)
    rms = float(np.sqrt(np.mean(waveform * waveform))) if len(waveform) else 0.0
    if rms > 0.0:
        waveform = waveform * (target_rms / rms)
    return np.clip(waveform, -1.0, 1.0)


def _crest_db(samples) -> float | None:
    values = np.asarray(samples, dtype=np.float64)
    if not len(values):
        return None
    rms = float(np.sqrt(np.mean(values * values)))
    peak = float(np.max(np.abs(values)))
    if rms <= 0.0 or peak <= 0.0:
        return None
    return float(20.0 * np.log10(peak / rms))


def _rrc(beta: float, samples_per_symbol: float, span: int = 12) -> np.ndarray:
    """Unit-energy root-raised-cosine impulse response."""
    sps = float(samples_per_symbol)
    half = int(np.ceil(span * sps / 2.0))
    t = np.arange(-half, half + 1, dtype=np.float64) / sps
    taps = np.empty_like(t)
    for index, value in enumerate(t):
        if abs(value) < 1e-12:
            taps[index] = 1.0 + beta * (4.0 / np.pi - 1.0)
        elif beta and abs(abs(value) - 1.0 / (4.0 * beta)) < 1e-10:
            taps[index] = (beta / np.sqrt(2.0)) * (
                (1.0 + 2.0 / np.pi) * np.sin(np.pi / (4.0 * beta))
                + (1.0 - 2.0 / np.pi) * np.cos(np.pi / (4.0 * beta))
            )
        else:
            numerator = (
                np.sin(np.pi * value * (1.0 - beta))
                + 4.0 * beta * value * np.cos(np.pi * value * (1.0 + beta))
            )
            denominator = np.pi * value * (1.0 - (4.0 * beta * value) ** 2)
            taps[index] = numerator / denominator
    return taps / np.sqrt(np.sum(taps * taps))


def _lanczos_weights(position: float, indices: np.ndarray, half: int = 8) -> np.ndarray:
    delta = float(position) - indices.astype(np.float64)
    weights = np.sinc(delta) * np.sinc(delta / float(half))
    weights[np.abs(delta) >= half] = 0.0
    total = float(np.sum(weights))
    return weights / total if abs(total) > 1e-12 else weights


def _fractional_impulses(symbols: np.ndarray, spacing: float) -> np.ndarray:
    """Place symbols on a possibly fractional sample grid with bounded sinc taps."""
    values = np.asarray(symbols, dtype=np.complex128).reshape(-1)
    if not len(values):
        return np.zeros(0, dtype=np.complex128)
    rounded = int(round(spacing))
    if abs(spacing - rounded) < 1e-12:
        result = np.zeros((len(values) - 1) * rounded + 1, dtype=np.complex128)
        result[::rounded] = values
        return result
    length = int(np.ceil((len(values) - 1) * spacing)) + 1
    result = np.zeros(length, dtype=np.complex128)
    half = 8
    for number, value in enumerate(values):
        position = number * spacing
        first = max(0, int(np.floor(position)) - half + 1)
        last = min(length, int(np.floor(position)) + half + 1)
        indices = np.arange(first, last)
        result[indices] += value * _lanczos_weights(position, indices, half)
    return result


def _fractional_samples(values: np.ndarray, start: float, spacing: float,
                        count: int | None = None) -> np.ndarray:
    """Sample a band-limited stream on a fractional symbol clock."""
    stream = np.asarray(values, dtype=np.complex128).reshape(-1)
    if not len(stream) or start >= len(stream):
        return np.zeros(0, dtype=np.complex128)
    available = max(0, int(np.floor((len(stream) - 1 - start) / spacing)) + 1)
    wanted = available if count is None else min(available, max(0, int(count)))
    if wanted < 1:
        return np.zeros(0, dtype=np.complex128)
    rounded_start = int(round(start))
    rounded_spacing = int(round(spacing))
    if (abs(start - rounded_start) < 1e-12
            and abs(spacing - rounded_spacing) < 1e-12):
        return stream[
            rounded_start:rounded_start + wanted * rounded_spacing:rounded_spacing
        ][:wanted]
    result = np.empty(wanted, dtype=np.complex128)
    half = 8
    # Bounded batches preserve the same normalized Lanczos interpolation while
    # avoiding one Python call per symbol when tracking a real audio clock.
    offsets = np.arange(-half + 1, half + 1)
    for begin in range(0, wanted, 4096):
        end = min(wanted, begin + 4096)
        positions = start + np.arange(begin, end) * spacing
        indices = np.floor(positions).astype(np.int64)[:, None] + offsets
        delta = positions[:, None] - indices
        weights = np.sinc(delta) * np.sinc(delta / half)
        weights[(indices < 0) | (indices >= len(stream)) | (np.abs(delta) >= half)] = 0
        totals = weights.sum(axis=1, keepdims=True)
        weights = weights / np.where(np.abs(totals) > 1e-12, totals, 1.0)
        result[begin:end] = np.sum(weights * stream[np.clip(indices, 0, len(stream)-1)], axis=1)
    return result


def _pilot_clock_ppm(matched, first_sample, spacing, prefix_length,
                     block_size, pilot_positions, pilots, coeff, sample_rate):
    """Estimate clock mismatch from known pilots, without payload/CRC feedback."""
    if len(pilot_positions) < 3:
        return 0.0
    available = int((len(matched) - first_sample) / spacing) - prefix_length
    if available * spacing / sample_rate < 2.0:
        return 0.0
    radius = len(coeff) // 2
    rows = max(0, (available - radius - block_size) // block_size)
    if rows < 32:
        return 0.0
    selected = np.unique(np.linspace(rows // 8, rows - 1, min(96, rows), dtype=int))
    coordinates = (prefix_length + selected[:, None, None] * block_size
                   + pilot_positions[None, :, None] + np.arange(-radius, radius+1))
    pivot = prefix_length / 2
    axis = np.arange(len(matched))
    x = np.asarray(pilot_positions, dtype=float)
    x = x - x.mean()
    x_energy = np.dot(x, x)

    def score(ppm):
        positions = first_sample + spacing * (coordinates + (coordinates-pivot)*ppm/1e6)
        observed = (np.interp(positions.ravel(), axis, matched.real)
                    + 1j*np.interp(positions.ravel(), axis, matched.imag)).reshape(positions.shape)
        observed = (observed @ coeff) * np.conj(pilots)
        phases = np.unwrap(np.angle(observed), axis=1)
        slopes = (phases @ x) / x_energy
        corrected = observed * np.exp(-1j*(phases.mean(axis=1)[:, None] + slopes[:, None]*x))
        errors = np.mean(np.abs(corrected - 1.0)**2, axis=1)
        # Independent row sets must both support the correction.
        return np.array([errors[::2].mean(), errors[1::2].mean()])

    scores = {float(ppm): score(ppm) for ppm in range(-50, 51, 5)}
    best = min(scores, key=lambda ppm: float(scores[ppm].mean()))
    for ppm in (best-2, best-1, best+1, best+2):
        if -50 <= ppm <= 50:
            scores[ppm] = score(ppm)
    best = min(scores, key=lambda ppm: float(scores[ppm].mean()))
    baseline = scores[0.0]
    if (scores[best].mean() < baseline.mean()*0.90
            and np.all(scores[best] < baseline)):
        return best
    return 0.0


def _symbol_noise_covariance(pulse: np.ndarray, spacing: float, taps: int) -> np.ndarray:
    """Matched-filter noise covariance on the packed symbol grid."""
    response = np.convolve(pulse, pulse[::-1], mode="full")
    centre = len(response) // 2
    offsets = np.arange(-(taps - 1), taps, dtype=np.float64) * spacing + centre
    axis = np.arange(len(response), dtype=np.float64)
    sampled = np.interp(offsets, axis, response, left=0.0, right=0.0)
    middle = max(float(sampled[taps - 1]), 1e-12)
    correlation = sampled / middle
    indices = np.arange(taps)
    covariance = correlation[(indices[:, None] - indices[None, :]) + taps - 1]
    # Finite pulse truncation and interpolation can introduce tiny negative
    # eigenvalues; a diagonal floor keeps the regularizer positive definite.
    covariance = (covariance + covariance.T) / 2.0
    minimum = float(np.min(np.linalg.eigvalsh(covariance)))
    if minimum < 1e-9:
        covariance += np.eye(taps) * (1e-9 - minimum)
    return covariance.astype(np.complex128)


def _fft_filter_valid(values: np.ndarray, coefficients: np.ndarray) -> np.ndarray:
    """Apply the learned FIR by bounded-memory overlap-save FDE."""
    x = np.asarray(values, dtype=np.complex128)
    h = np.asarray(coefficients, dtype=np.complex128)[::-1]
    if not len(h) or len(x) < len(h):
        return np.zeros(0, dtype=np.complex128)
    # Four filter lengths keeps transform overhead small without allocating an
    # FFT as long as a multi-second 20 kHz superframe.
    size = 1 << max(8, (4 * len(h) - 1).bit_length())
    fresh = size - len(h) + 1
    spectrum = np.fft.fft(h, size)
    padded = np.concatenate([np.zeros(len(h) - 1, dtype=np.complex128), x])
    causal: list[np.ndarray] = []
    position = 0
    while position < len(x):
        block = padded[position:position + size]
        take = min(fresh, len(x) - position)
        if len(block) < size:
            block = np.pad(block, (0, size - len(block)))
        filtered = np.fft.ifft(np.fft.fft(block, size) * spectrum)
        causal.append(filtered[len(h) - 1:len(h) - 1 + take])
        position += take
    full = np.concatenate(causal)
    first = len(h) - 1
    return full[first:first + len(x) - len(h) + 1]


def _fft_valid_correlation(signal: np.ndarray, reference: np.ndarray) -> tuple[int, float]:
    """Return the strongest normalized real correlation and its start index."""
    x = np.asarray(signal, dtype=np.float64)
    ref = np.asarray(reference, dtype=np.float64)
    if len(x) < len(ref) or not len(ref):
        return 0, 0.0
    total = len(x) + len(ref) - 1
    size = 1 << (total - 1).bit_length()
    convolution = np.fft.irfft(
        np.fft.rfft(x, size) * np.fft.rfft(ref[::-1], size), size
    )[:total]
    correlation = convolution[len(ref) - 1: len(x)]
    energy = np.cumsum(np.concatenate([[0.0], x * x]))
    windows = energy[len(ref):] - energy[:-len(ref)]
    ref_energy = float(np.sum(ref * ref))
    score = np.abs(correlation) / np.sqrt(np.maximum(windows * ref_energy, 1e-20))
    index = int(np.argmax(score))
    return index, float(score[index])


def correlation_candidates(signal: np.ndarray, reference: np.ndarray, *,
                           threshold: float, limit: int = 32) -> list[int]:
    """Strong, well-separated reference starts, returned in time order."""
    x = np.asarray(signal, dtype=np.float64)
    ref = np.asarray(reference, dtype=np.float64)
    if len(x) < len(ref) or not len(ref):
        return []
    total = len(x) + len(ref) - 1
    size = 1 << (total - 1).bit_length()
    convolution = np.fft.irfft(
        np.fft.rfft(x, size) * np.fft.rfft(ref[::-1], size), size
    )[:total]
    correlation = convolution[len(ref) - 1: len(x)]
    energy = np.cumsum(np.concatenate([[0.0], x * x]))
    windows = energy[len(ref):] - energy[:-len(ref)]
    ref_energy = float(np.sum(ref * ref))
    score = np.abs(correlation) / np.sqrt(
        np.maximum(windows * ref_energy, 1e-20)
    )
    available = score.copy()
    starts: list[int] = []
    separation = max(1, len(ref) // 2)
    for _ in range(max(1, int(limit))):
        index = int(np.argmax(available))
        if float(available[index]) < float(threshold):
            break
        starts.append(index)
        available[max(0, index - separation):index + separation + 1] = 0.0
    return sorted(starts)


@dataclass(frozen=True)
class _ScKnown:
    preamble_half: np.ndarray
    training: np.ndarray
    pilots: np.ndarray

    @property
    def prefix(self) -> np.ndarray:
        return np.concatenate([self.preamble_half, self.preamble_half, self.training])


@lru_cache(maxsize=None)
def _sc_known(profile: WaveformProfile) -> _ScKnown:
    rng = np.random.default_rng(_SEED)
    return _ScKnown(
        preamble_half=_bpsk(rng, _SC_PREAMBLE_HALF),
        training=_bpsk(rng, _SC_TRAINING),
        pilots=_bpsk(rng, profile.pilot_symbols_per_block),
    )


class SingleCarrierModulator:
    """RRC-shaped passband single carrier; tau < 1 is genuine FTN spacing."""

    def __init__(self, profile: WaveformProfile) -> None:
        self.profile = profile
        self.known = _sc_known(profile)
        self.spacing = profile.symbol_spacing_samples
        self.taps = _rrc(profile.rolloff, profile.pulse_samples_per_symbol)
        self.pilot_positions = np.linspace(
            0, profile.physical_symbols_per_block - 1,
            profile.pilot_symbols_per_block, dtype=int,
        )
        mask = np.ones(profile.physical_symbols_per_block, dtype=bool)
        mask[self.pilot_positions] = False
        self.data_positions = np.flatnonzero(mask)

    def _physical_payload(self, data_grid) -> np.ndarray:
        grid = np.asarray(data_grid, dtype=np.complex128)
        if grid.size == 0:
            return np.zeros(0, dtype=np.complex128)
        grid = np.atleast_2d(grid)
        if grid.shape[1] != self.profile.points_per_block:
            raise ValueError("single-carrier grid has the wrong number of data symbols")
        rows = np.zeros(
            (len(grid), self.profile.physical_symbols_per_block), dtype=np.complex128
        )
        rows[:, self.pilot_positions] = self.known.pilots
        rows[:, self.data_positions] = grid
        return rows.reshape(-1)

    def _render_symbols(self, symbols: np.ndarray) -> np.ndarray:
        up = _fractional_impulses(symbols, self.spacing)
        baseband = np.convolve(up, self.taps, mode="full")
        time = np.arange(len(baseband), dtype=np.float64) / self.profile.sample_rate
        return np.real(baseband * np.exp(2j * np.pi * self.profile.center_hz * time))

    def burst(self, data_grid) -> np.ndarray:
        symbols = np.concatenate([
            np.zeros(_SC_GUARD, dtype=np.complex128),
            self.known.prefix,
            self._physical_payload(data_grid),
            np.zeros(_SC_GUARD, dtype=np.complex128),
        ])
        return _normalise(self._render_symbols(symbols), self.profile.tx_rms)

    @lru_cache(maxsize=1)
    def reference(self) -> np.ndarray:
        symbols = np.concatenate([
            np.zeros(_SC_GUARD, dtype=np.complex128),
            self.known.prefix,
            np.zeros(_SC_GUARD, dtype=np.complex128),
        ])
        return _normalise(self._render_symbols(symbols), self.profile.tx_rms)

    def burst_samples(self, data_blocks: int) -> int:
        count = (
            2 * _SC_GUARD + len(self.known.prefix)
            + int(data_blocks) * self.profile.physical_symbols_per_block
        )
        return int(np.ceil((count - 1) * self.spacing)) + 1 + len(self.taps) - 1


class SingleCarrierReceiver:
    def __init__(self, profile: WaveformProfile, samples,
                 start_hint: int | None = None) -> None:
        self.profile = profile
        self.modulator = SingleCarrierModulator(profile)
        self.known = self.modulator.known
        self.samples = np.asarray(samples, dtype=np.float64)
        self.metrics = LinkMetrics(
            audio_rms=(float(np.sqrt(np.mean(self.samples ** 2)))
                       if len(self.samples) else 0.0),
            crest_factor_db=_crest_db(self.samples),
        )
        self._data = np.zeros((0, profile.points_per_block), dtype=np.complex128)
        self._variance = 1.0
        self._acquire(start_hint)

    @property
    def snr_db(self) -> float | None:
        return self.metrics.snr_db

    @property
    def channel(self) -> np.ndarray:
        return (self.metrics.channel_response if self.metrics.channel_response is not None
                else np.ones(1, dtype=np.complex128))

    def _acquire(self, start_hint: int | None = None) -> None:
        if start_hint is None:
            start, confidence = _fft_valid_correlation(
                self.samples, self.modulator.reference()
            )
        else:
            start, confidence = int(start_hint), 1.0
        self.burst_start = int(start)
        self.metrics.sync_confidence = confidence
        if confidence < 0.20:
            raise ValueError("no single-carrier burst detected")

        fs = self.profile.sample_rate
        n = np.arange(len(self.samples), dtype=np.float64)
        baseband = analytic(self.samples) * np.exp(
            -2j * np.pi * self.profile.center_hz * n / fs
        )
        matched = np.convolve(baseband, self.modulator.taps, mode="full")
        nominal = float(
            start + len(self.modulator.taps) - 1
            + _SC_GUARD * self.modulator.spacing
        )

        # The raw correlation aligns the waveform to within a sample.  Select
        # the polyphase whose known preamble has the largest coherent response.
        best = (0.0, nominal)
        radius_samples = max(1, int(np.ceil(self.modulator.spacing / 2.0)))
        for shift in range(-radius_samples, radius_samples + 1):
            first = nominal + shift
            stop = first + len(self.known.prefix) * self.modulator.spacing
            if first < 0 or stop >= len(matched):
                continue
            observed = _fractional_samples(
                matched, first, self.modulator.spacing, len(self.known.prefix)
            )
            score = abs(np.vdot(self.known.prefix, observed))
            if score > best[0]:
                best = (float(score), first)
        first_sample = best[1]

        raw = _fractional_samples(matched, first_sample, self.modulator.spacing)
        if len(raw) < len(self.known.prefix):
            raise ValueError("single-carrier burst is truncated before training")
        half = _SC_PREAMBLE_HALF
        phase = np.angle(np.vdot(raw[:half], raw[half:2 * half]))
        separation = half * self.modulator.spacing / fs
        cfo = float(phase / (2.0 * np.pi * separation))
        self.metrics.cfo_hz = cfo

        if abs(cfo) > 1e-9:
            corrected = baseband * np.exp(-2j * np.pi * cfo * n / fs)
            matched = np.convolve(corrected, self.modulator.taps, mode="full")
            raw = _fractional_samples(matched, first_sample, self.modulator.spacing)

        prefix = self.known.prefix
        taps = min(self.profile.equalizer_taps, len(prefix) // 3 * 2 + 1)
        if taps % 2 == 0:
            taps += 1
        radius = taps // 2
        if len(raw) < taps:
            raise ValueError("single-carrier burst is too short for equalization")
        windows = np.lib.stride_tricks.sliding_window_view(raw, taps)
        train_rows = windows[:len(prefix) - taps + 1]
        target = prefix[radius:len(prefix) - radius]
        gram = train_rows.conj().T @ train_rows
        self.metrics.equalizer_condition = float(np.linalg.cond(gram))

        # Compare only the stationary middle of both repeated halves. Their
        # edges see different neighbouring symbols through the long RRC pulse;
        # counting that deterministic boundary ISI as random noise caused a
        # grossly over-regularized FTN inverse even in a clean loopback.
        pulse_memory = int(np.ceil(len(self.modulator.taps)
                                   / self.modulator.spacing / 2.0))
        margin = min(half // 4, max(2, pulse_memory))
        repeated_error = (
            raw[margin:half - margin]
            - raw[half + margin:2 * half - margin]
        )
        noise_power = max(float(np.mean(np.abs(repeated_error) ** 2) / 2.0), 1e-12)
        observed_power = max(float(np.mean(np.abs(train_rows) ** 2)), 1e-12)
        noise_fraction = min(1.0, noise_power / observed_power)
        # `gram` accumulates many overlapping training rows whereas the noise
        # estimate is per raw symbol. Applying the raw fraction directly here
        # over-regularizes by roughly the training-window reuse factor. The
        # bounded 0.02 conversion was selected by clean/noisy replay of the
        # known prefix and still rises automatically with measured noise.
        ridge_fraction = max(
            self.profile.equalizer_ridge_floor, noise_fraction * 0.02
        )
        ridge_scale = max(float(np.trace(gram).real) / taps, 1.0) * ridge_fraction
        covariance = (
            _symbol_noise_covariance(self.modulator.taps, self.modulator.spacing, taps)
            if self.profile.noise_whitening else np.eye(taps, dtype=np.complex128)
        )
        weights = np.ones(len(train_rows), dtype=np.float64)
        coeff = np.zeros(taps, dtype=np.complex128)
        completed_iterations = 0
        for iteration in range(self.profile.equalizer_iterations):
            weighted = train_rows * np.sqrt(weights)[:, None]
            wanted = target * np.sqrt(weights)
            normal = weighted.conj().T @ weighted + ridge_scale * covariance
            coeff = np.linalg.solve(normal, weighted.conj().T @ wanted)
            completed_iterations = iteration + 1
            if iteration + 1 >= self.profile.equalizer_iterations:
                break
            fit_error = train_rows @ coeff - target
            scale = max(float(np.median(np.abs(fit_error))) * 1.4826, 1e-9)
            weights = 1.0 / (1.0 + (np.abs(fit_error) / (2.5 * scale)) ** 2)

        if self.profile.symbol_clock_tracking:
            ppm = _pilot_clock_ppm(
                matched, first_sample, self.modulator.spacing, len(prefix),
                self.profile.physical_symbols_per_block,
                self.modulator.pilot_positions, self.known.pilots, coeff, fs)
            self.metrics.sample_clock_ppm = ppm
            if ppm:
                tracked_spacing = self.modulator.spacing * (1.0 + ppm / 1e6)
                tracked_first = first_sample - len(prefix)/2 * self.modulator.spacing * ppm/1e6
                raw = _fractional_samples(matched, tracked_first, tracked_spacing)
                windows = np.lib.stride_tricks.sliding_window_view(raw, taps)

        equalised = windows @ coeff
        aligned = np.zeros(len(raw), dtype=np.complex128)
        aligned[radius:radius + len(equalised)] = equalised
        self.metrics.equalizer_mode = self.profile.equalizer_mode
        self.metrics.equalizer_iterations = completed_iterations
        white_gain = float(np.real(np.vdot(coeff, covariance @ coeff)))
        self.metrics.noise_enhancement_db = float(
            10.0 * np.log10(max(white_gain, 1e-12))
        )

        recovered = aligned[radius:len(prefix) - radius]
        expected = prefix[radius:len(prefix) - radius]
        residual = recovered - expected
        self.metrics.residual_isi_rms = float(np.sqrt(np.mean(np.abs(residual) ** 2)))
        self._variance = max(float(np.mean(np.abs(residual) ** 2)), 1e-9)
        signal = max(float(np.mean(np.abs(expected) ** 2)), 1e-12)
        self.metrics.snr_db = float(10.0 * np.log10(signal / self._variance))
        self.metrics.channel_response = np.asarray(coeff, dtype=np.complex128)

        physical = aligned[len(prefix):]
        block_size = self.profile.physical_symbols_per_block
        count = len(physical) // block_size
        if count < 1:
            return
        rows = physical[:count * block_size].reshape(count, block_size)
        corrected_rows = []
        positions = np.arange(block_size, dtype=np.float64)
        for row in rows:
            pilots = row[self.modulator.pilot_positions] * np.conj(self.known.pilots)
            phases = np.unwrap(np.angle(pilots))
            slope, intercept = np.polyfit(
                self.modulator.pilot_positions.astype(np.float64), phases, 1
            )
            row = row * np.exp(-1j * (intercept + slope * positions))
            corrected_rows.append(row[self.modulator.data_positions])
        self._data = np.asarray(corrected_rows, dtype=np.complex128)

    def available_data_symbols(self) -> int:
        return len(self._data)

    def data_symbols(self, first: int, count: int):
        rows = self._data[int(first):int(first) + int(count)]
        values = rows.reshape(-1)
        variance = np.full(len(values), self._variance, dtype=np.float64)
        return values, variance, 0.0



def make_modulator(profile: WaveformProfile) -> SingleCarrierModulator:
    """Construct the production SC-FTN modulator."""
    if not profile.is_single_carrier:
        raise ValueError("SC-FTN is the only supported waveform family")
    return SingleCarrierModulator(profile)


def make_receiver(profile: WaveformProfile, samples,
                  start_hint: int | None = None) -> SingleCarrierReceiver:
    """Construct the production SC-FTN receiver."""
    if not profile.is_single_carrier:
        raise ValueError("SC-FTN is the only supported waveform family")
    return SingleCarrierReceiver(profile, samples, start_hint)
