"""Physical layers for Guardian G2 SC-HS, SC-FTN and SEFDM experiments.

All variants produce ordinary real 48 kHz audio.  They deliberately share a
small receiver contract with the framing layer while keeping their modulation
mathematics independent from the established OFDM implementation.
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
_SC_GUARD = 8
_SEFDM_PREAMBLE = 2
_SEFDM_TRAINING = 2
_SEFDM_EQUALIZER_TAPS = 97
_SEFDM_EQUALIZER_RIDGE = 1e-4


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


def _occupied_analytic(samples, profile: WaveformProfile) -> np.ndarray:
    """Analytic audio restricted to the SEFDM occupied band plus one carrier."""
    values = np.asarray(samples, dtype=np.float64)
    count = len(values)
    if count == 0:
        return np.zeros(0, dtype=np.complex128)
    low, high = profile.occupied_band
    margin = profile.carrier_spacing_hz
    scale = count / profile.sample_rate
    first = max(1, int(np.floor((low - margin) * scale)))
    last = min(count // 2, int(np.ceil((high + margin) * scale)))
    spectrum = np.fft.fft(values)
    kept = np.zeros(count, dtype=np.complex128)
    kept[first:last + 1] = 2.0 * spectrum[first:last + 1]
    return np.fft.ifft(kept)


def _rrc(beta: float, samples_per_symbol: int, span: int = 12) -> np.ndarray:
    """Unit-energy root-raised-cosine impulse response."""
    sps = int(samples_per_symbol)
    t = np.arange(-span * sps // 2, span * sps // 2 + 1, dtype=np.float64) / sps
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
        up = np.zeros((len(symbols) - 1) * self.spacing + 1, dtype=np.complex128)
        if len(symbols):
            up[::self.spacing] = symbols
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
        return (count - 1) * self.spacing + 1 + len(self.taps) - 1


class SingleCarrierReceiver:
    def __init__(self, profile: WaveformProfile, samples) -> None:
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
        self._acquire()

    @property
    def snr_db(self) -> float | None:
        return self.metrics.snr_db

    @property
    def channel(self) -> np.ndarray:
        return (self.metrics.channel_response if self.metrics.channel_response is not None
                else np.ones(1, dtype=np.complex128))

    def _acquire(self) -> None:
        start, confidence = _fft_valid_correlation(
            self.samples, self.modulator.reference()
        )
        self.metrics.sync_confidence = confidence
        if confidence < 0.20:
            raise ValueError("no single-carrier burst detected")

        fs = self.profile.sample_rate
        n = np.arange(len(self.samples), dtype=np.float64)
        baseband = analytic(self.samples) * np.exp(
            -2j * np.pi * self.profile.center_hz * n / fs
        )
        matched = np.convolve(baseband, self.modulator.taps, mode="full")
        nominal = (
            start + len(self.modulator.taps) - 1
            + _SC_GUARD * self.modulator.spacing
        )

        # The raw correlation aligns the waveform to within a sample.  Select
        # the polyphase whose known preamble has the largest coherent response.
        best = (0.0, nominal)
        for shift in range(-self.modulator.spacing // 2,
                           self.modulator.spacing // 2 + 1):
            first = nominal + shift
            stop = first + len(self.known.prefix) * self.modulator.spacing
            if first < 0 or stop >= len(matched):
                continue
            observed = matched[first:stop:self.modulator.spacing][:len(self.known.prefix)]
            score = abs(np.vdot(self.known.prefix, observed))
            if score > best[0]:
                best = (float(score), first)
        first_sample = best[1]

        raw = matched[first_sample::self.modulator.spacing]
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
            raw = matched[first_sample::self.modulator.spacing]

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
        ridge = np.eye(taps, dtype=np.complex128) * (
            max(float(np.trace(gram).real), 1.0) * 1e-7
        )
        coeff = np.linalg.solve(gram + ridge, train_rows.conj().T @ target)
        equalised = windows @ coeff
        aligned = np.zeros(len(raw), dtype=np.complex128)
        aligned[radius:radius + len(equalised)] = equalised

        recovered = aligned[radius:len(prefix) - radius]
        expected = prefix[radius:len(prefix) - radius]
        residual = recovered - expected
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


@dataclass(frozen=True)
class _SefdmKnown:
    preamble: np.ndarray
    training: np.ndarray
    pilots: np.ndarray


@lru_cache(maxsize=None)
def _sefdm_known(profile: WaveformProfile) -> _SefdmKnown:
    rng = np.random.default_rng(_SEED ^ 0x5EFD)
    return _SefdmKnown(
        preamble=_bpsk(rng, profile.num_carriers),
        training=np.asarray([
            _bpsk(rng, profile.num_carriers),
            _bpsk(rng, profile.num_carriers),
        ]),
        pilots=_bpsk(rng, profile.num_pilots),
    )


class SefdmModulator:
    """Bandwidth-compressed non-orthogonal multicarrier audio modem."""

    def __init__(self, profile: WaveformProfile) -> None:
        self.profile = profile
        self.known = _sefdm_known(profile)
        self.frequencies = (
            profile.first_carrier_hz
            + np.arange(profile.num_carriers) * profile.carrier_spacing_hz
        )
        n = np.arange(profile.fft_size, dtype=np.float64)
        self.basis = np.exp(
            2j * np.pi * n[:, None] * self.frequencies[None, :] / profile.sample_rate
        ) / np.sqrt(profile.fft_size)
        self.pilot_positions = np.arange(0, profile.num_carriers, profile.pilot_spacing)
        mask = np.ones(profile.num_carriers, dtype=bool)
        mask[self.pilot_positions] = False
        self.data_positions = np.flatnonzero(mask)

    def symbol(self, values) -> np.ndarray:
        body = np.real(self.basis @ np.asarray(values, dtype=np.complex128))
        return np.concatenate([body[-self.profile.cp_length:], body])

    def _rows(self, data_grid) -> np.ndarray:
        grid = np.asarray(data_grid, dtype=np.complex128)
        if grid.size == 0:
            return np.zeros((0, self.profile.num_carriers), dtype=np.complex128)
        grid = np.atleast_2d(grid)
        rows = np.zeros((len(grid), self.profile.num_carriers), dtype=np.complex128)
        rows[:, self.pilot_positions] = self.known.pilots
        rows[:, self.data_positions] = grid
        return rows

    def burst(self, data_grid) -> np.ndarray:
        rows = [self.known.preamble] * _SEFDM_PREAMBLE
        rows.extend(self.known.training)
        rows.extend(self._rows(data_grid))
        waveform = np.concatenate([self.symbol(row) for row in rows])
        return _normalise(waveform, self.profile.tx_rms)

    @lru_cache(maxsize=1)
    def reference(self) -> np.ndarray:
        rows = [self.known.preamble] * _SEFDM_PREAMBLE
        rows.extend(self.known.training)
        return _normalise(
            np.concatenate([self.symbol(row) for row in rows]), self.profile.tx_rms
        )

    def burst_samples(self, data_blocks: int) -> int:
        return (_SEFDM_PREAMBLE + _SEFDM_TRAINING + int(data_blocks)) * self.profile.symbol_samples


class SefdmReceiver:
    def __init__(self, profile: WaveformProfile, samples) -> None:
        self.profile = profile
        self.modulator = SefdmModulator(profile)
        self.known = self.modulator.known
        self.samples = np.asarray(samples, dtype=np.float64)
        self.metrics = LinkMetrics(
            audio_rms=(float(np.sqrt(np.mean(self.samples ** 2)))
                       if len(self.samples) else 0.0),
            crest_factor_db=_crest_db(self.samples),
        )
        self._analytic = analytic(self.samples)
        self._band_analytic = _occupied_analytic(self.samples, profile)
        self._equalized = self.samples.copy()
        self._data = np.zeros((0, profile.points_per_block), dtype=np.complex128)
        self._variance = np.ones(profile.points_per_block, dtype=np.float64)
        self._demod = self._real_demodulator(0.0)
        self._acquire()

    @property
    def snr_db(self) -> float | None:
        return self.metrics.snr_db

    @property
    def channel(self) -> np.ndarray:
        return (self.metrics.channel_response if self.metrics.channel_response is not None
                else np.ones(self.profile.num_carriers, dtype=np.complex128))

    def _carrier_basis(self, cfo_hz: float) -> np.ndarray:
        n = np.arange(self.profile.fft_size, dtype=np.float64)
        return np.exp(
            2j * np.pi * n[:, None]
            * (self.modulator.frequencies[None, :] + cfo_hz)
            / self.profile.sample_rate
        ) / np.sqrt(self.profile.fft_size)

    def _real_demodulator(self, cfo_hz: float) -> np.ndarray:
        """Exact real I/Q least-squares detector for the SEFDM carrier basis."""
        basis = self._carrier_basis(cfo_hz)
        real_basis = np.concatenate([basis.real, -basis.imag], axis=1)
        return np.linalg.pinv(real_basis, rcond=1e-10)

    def _time_equalizer(self, start: int, cfo_hz: float) -> tuple[np.ndarray, np.ndarray]:
        """Learn one passband FIR inverse from the complete known prefix.

        The SSB-like radio path is linear in real audio but is not diagonal in a
        compressed carrier basis.  Removing CFO first and equalising the time
        waveform therefore restores the condition under which the exact SEFDM
        matrix was derived.
        """
        n = np.arange(len(self.samples), dtype=np.float64)
        reference = self.modulator.reference()
        corrected_complex = (
            self._analytic
            * np.exp(-2j * np.pi * cfo_hz * n / self.profile.sample_rate)
        )
        observed_known = corrected_complex[start:start + len(reference)]
        if len(observed_known) < len(reference):
            raise ValueError("SEFDM burst is truncated during phase estimation")
        provisional = self._real_demodulator(0.0)
        correlations = []
        cfo_real = np.real(corrected_complex)
        count = self.profile.num_carriers
        for index, known in enumerate(self.known.training):
            block = _SEFDM_PREAMBLE + index
            offset = (
                start + block * self.profile.symbol_samples
                + self.profile.cp_length
            )
            body = cfo_real[offset:offset + self.profile.fft_size]
            if len(body) < self.profile.fft_size:
                raise ValueError("SEFDM burst is truncated during phase estimation")
            coefficients = provisional @ body
            values = coefficients[:count] + 1j * coefficients[count:]
            correlations.append(np.vdot(known, values))
        common_phase = float(np.angle(np.sum(correlations)))
        corrected = np.real(corrected_complex * np.exp(-1j * common_phase))
        observed = corrected[start:start + len(reference)]
        taps = min(
            _SEFDM_EQUALIZER_TAPS,
            self.profile.cp_length + 1,
            len(reference) // 4 * 2 + 1,
        )
        if taps % 2 == 0:
            taps -= 1
        if len(observed) < len(reference) or taps < 3:
            raise ValueError("SEFDM burst is truncated during time equalisation")
        radius = taps // 2
        windows = np.lib.stride_tricks.sliding_window_view(observed, taps)
        target = reference[radius:radius + len(windows)]
        gram = windows.T @ windows
        identity = np.zeros(taps, dtype=np.float64)
        identity[radius] = 1.0
        ridge = np.eye(taps, dtype=np.float64) * (
            max(float(np.trace(gram)), 1.0) * _SEFDM_EQUALIZER_RIDGE
        )
        coefficients = identity + np.linalg.solve(
            gram + ridge,
            windows.T @ (target - windows @ identity),
        )
        padded = np.pad(corrected, (radius, radius))
        all_windows = np.lib.stride_tricks.sliding_window_view(padded, taps)
        equalized = all_windows @ coefficients
        return equalized, coefficients

    def _block(self, samples: np.ndarray, start: int, index: int,
               cfo_hz: float = 0.0) -> np.ndarray:
        offset = start + index * self.profile.symbol_samples + self.profile.cp_length
        body = self._equalized[offset:offset + self.profile.fft_size]
        if len(body) < self.profile.fft_size:
            raise IndexError("SEFDM burst is truncated")
        coefficients = self._demod @ body
        count = self.profile.num_carriers
        values = coefficients[:count] + 1j * coefficients[count:]
        # The shifted basis is local to this body.  Remove the phase accumulated
        # before its first sample so one stationary channel estimate serves all
        # blocks in the burst.
        return values * np.exp(-2j * np.pi * cfo_hz * offset / self.profile.sample_rate)

    def _acquire(self) -> None:
        start, confidence = _fft_valid_correlation(
            self.samples, self.modulator.reference()
        )
        self.metrics.sync_confidence = confidence
        if confidence < 0.18:
            raise ValueError("no SEFDM burst detected")
        complex_audio = self._band_analytic
        symbol = self.profile.symbol_samples
        body0 = complex_audio[
            start + self.profile.cp_length:start + symbol
        ]
        body1 = complex_audio[
            start + symbol + self.profile.cp_length:start + 2 * symbol
        ]
        if len(body0) < self.profile.fft_size or len(body1) < self.profile.fft_size:
            raise ValueError("SEFDM burst is truncated before its preamble")
        phase = np.angle(np.vdot(body0, body1))
        cfo = float(phase / (2.0 * np.pi * symbol / self.profile.sample_rate))
        # Finite-burst Hilbert edges leave a few millihertz of apparent offset.
        # Feeding that numerical residue into an intentionally non-orthogonal
        # inverse needlessly perturbs every carrier; below 0.05 Hz it is far
        # smaller than any correction this burst duration can resolve.
        if abs(cfo) < 0.05:
            cfo = 0.0
        self.metrics.cfo_hz = cfo
        self._equalized, equalizer = self._time_equalizer(start, cfo)
        self._demod = self._real_demodulator(0.0)

        observed_training = np.asarray([
            self._block(self.samples, start, _SEFDM_PREAMBLE + index, 0.0)
            for index in range(_SEFDM_TRAINING)
        ])
        estimates = observed_training / self.known.training
        residual_channel = estimates.mean(axis=0)
        difference = (estimates[0] - estimates[1]) / 2.0
        noise = np.maximum(np.abs(difference) ** 2, 1e-9)
        self.metrics.channel_response = equalizer.astype(np.complex128)
        signal = max(float(np.mean(np.abs(estimates) ** 2)), 1e-12)
        noise_mean = max(float(np.mean(noise)), 1e-12)
        self.metrics.snr_db = float(10.0 * np.log10(signal / noise_mean))

        total = max(0, (len(self.samples) - start) // symbol)
        rows = []
        slopes = []
        positions = np.arange(self.profile.num_carriers, dtype=np.float64)
        for index in range(_SEFDM_PREAMBLE + _SEFDM_TRAINING, total):
            observed = (
                self._block(self.samples, start, index, 0.0)
                / residual_channel
            )
            residual = (
                observed[self.modulator.pilot_positions] * np.conj(self.known.pilots)
            )
            slope, intercept = np.polyfit(
                self.modulator.pilot_positions.astype(np.float64),
                np.unwrap(np.angle(residual)), 1,
            )
            slopes.append(float(slope))
            amplitude = max(float(np.mean(np.abs(residual))), 1e-9)
            corrected = (
                observed * np.exp(-1j * (intercept + slope * positions))
                / amplitude
            )
            rows.append(corrected[self.modulator.data_positions])
        if rows:
            self._data = np.asarray(rows, dtype=np.complex128)
        gain = np.abs(residual_channel[self.modulator.data_positions]) ** 2
        self._variance = (
            noise[self.modulator.data_positions] / np.maximum(gain, 1e-12)
        )
        self.metrics.phase_slope = float(np.mean(slopes)) if slopes else 0.0

    def available_data_symbols(self) -> int:
        return len(self._data)

    def data_symbols(self, first: int, count: int):
        rows = self._data[int(first):int(first) + int(count)]
        values = rows.reshape(-1)
        variance = np.tile(self._variance, len(rows))
        return values, variance, self.metrics.phase_slope or 0.0


def make_modulator(profile: WaveformProfile):
    return (SingleCarrierModulator(profile) if profile.is_single_carrier
            else SefdmModulator(profile))


def make_receiver(profile: WaveformProfile, samples):
    return (SingleCarrierReceiver(profile, samples) if profile.is_single_carrier
            else SefdmReceiver(profile, samples))
