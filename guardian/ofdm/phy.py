"""The OFDM symbol engine: bits and bins in, real audio out, and back again.

Guardian sends ordinary real-valued audio through a soundcard, not complex IQ
into an SDR, so this modem is built the DMT way: the transmit spectrum is
assembled on positive-frequency bins only and the time samples come out of
`numpy.fft.irfft`, which imposes the Hermitian symmetry `X[N-k] = conj(X[k])`
implicitly. The samples are therefore real *by construction* -- there is no
complex waveform whose imaginary half gets quietly discarded -- and subcarrier
index k maps straight onto audio frequency k*fs/N, which is what makes the
occupied bandwidth a property of the active carrier set rather than a hidden
constant.

The alternative -- complex baseband mixed up to an audio centre frequency -- is
equally correct and was rejected deliberately: it adds a mixer, an image to
suppress, and one more frequency parameter, and buys nothing at these
bandwidths. See docs/ofdm-vhf.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from .config import OfdmProfile

# One fixed seed generates every known sequence in the waveform. Both stations
# derive the preamble, the training symbols and the pilot values from it plus
# the profile geometry, so there is nothing to exchange and nothing to get out
# of step.
_SEQUENCE_SEED = 0x0FDA55E5


def band_analytic(x, profile: OfdmProfile, margin_carriers: float = 1.0) -> np.ndarray:
    """The analytic signal of a real vector, restricted to the occupied band.

    One FFT does two jobs here, and the second one is worth about 10 dB.

    The Hilbert half is the same as `analytic`: a real waveform cannot say which
    way a frequency offset went, and the analytic form can.

    The band-pass half matters because every *time-domain* step of acquisition --
    the repetition metric, the offset estimate, the correlation against the
    training symbol -- sees the whole audio band, while the burst only occupies a
    tenth of it. White noise fills the rest. Left unfiltered, a burst at 8 dB
    in-band SNR presents about -2 dB to the detector and simply is not found,
    even though the demodulator downstream would have decoded it comfortably: the
    FFT it works from discards the out-of-band bins for free. Filtering first
    puts acquisition on the same footing as demodulation.

    `margin_carriers` keeps a little room outside the outermost carriers so the
    brick wall does not clip the skirts of the burst's own spectrum.
    """
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    if n == 0:
        return np.zeros(0, dtype=np.complex128)
    spacing = profile.subcarrier_spacing
    low, high = profile.occupied_band
    scale = n / profile.sample_rate
    first = max(1, int(np.floor((low - margin_carriers * spacing) * scale)))
    last = min(n // 2, int(np.ceil((high + margin_carriers * spacing) * scale)))
    spectrum = np.fft.fft(x)
    kept = np.zeros(n, dtype=np.complex128)
    # Doubling the positive frequencies is what makes real(result) the
    # band-limited signal itself rather than half of it.
    kept[first: last + 1] = 2.0 * spectrum[first: last + 1]
    return np.fft.ifft(kept)


def analytic(x) -> np.ndarray:
    """The analytic signal of a real vector: `x + j*hilbert(x)`.

    Needed because a real audio waveform cannot show which way a frequency
    offset went -- the Schmidl-Cox correlation of a real signal is itself real,
    so its phase is 0 or pi and carries no offset information. The analytic form
    restores the sign, and a frequency shift applied by an SSB path is exactly a
    complex rotation of it.

    Implemented with numpy's FFT rather than a scipy Hilbert transform: keep the
    positive frequencies at double weight, drop the negative ones.
    """
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    if n == 0:
        return np.zeros(0, dtype=np.complex128)
    weight = np.zeros(n)
    weight[0] = 1.0
    if n % 2 == 0:
        weight[1: n // 2] = 2.0
        weight[n // 2] = 1.0
    else:
        weight[1: (n + 1) // 2] = 2.0
    return np.fft.ifft(np.fft.fft(x) * weight)


@dataclass(frozen=True)
class KnownSequences:
    """The parts of a burst both stations already know."""

    preamble: np.ndarray   # one value per even-indexed active bin
    training: np.ndarray   # one value per active bin
    pilots: np.ndarray     # one value per pilot bin


@lru_cache(maxsize=None)
def known_sequences(profile: OfdmProfile) -> KnownSequences:
    """Derive the preamble, training and pilot values for a profile.

    All BPSK: nothing here carries information, and +-1 keeps the channel
    estimate a multiplication rather than a division.
    """
    rng = np.random.default_rng(_SEQUENCE_SEED)

    def bpsk(count: int) -> np.ndarray:
        return (rng.integers(0, 2, count) * 2.0 - 1.0).astype(np.complex128)

    return KnownSequences(
        preamble=bpsk(len(profile.preamble_carriers)),
        training=bpsk(profile.num_carriers),
        pilots=bpsk(profile.num_pilots),
    )


class OfdmModulator:
    """Assembles bursts. Pure numpy -- knows nothing about radios or bytes."""

    def __init__(self, profile: OfdmProfile) -> None:
        self.profile = profile
        self.known = known_sequences(profile)
        self._preamble = self._build_preamble()
        self._training = self.symbol(self.known.training)

    # -- one symbol ---------------------------------------------------------

    def symbol(self, values) -> np.ndarray:
        """Turn one value per active carrier into `symbol_samples` real samples."""
        p = self.profile
        spectrum = np.zeros(p.fft_size // 2 + 1, dtype=np.complex128)
        spectrum[p.carriers] = np.asarray(values, dtype=np.complex128)
        body = np.fft.irfft(spectrum, n=p.fft_size)
        return np.concatenate([body[-p.cp_length:], body])

    def _build_preamble(self) -> np.ndarray:
        p = self.profile
        values = np.zeros(p.num_carriers, dtype=np.complex128)
        positions = p.preamble_carriers - p.first_carrier
        values[positions] = self.known.preamble * np.sqrt(
            p.num_carriers / len(positions)
        )
        return self.symbol(values)

    def preamble_symbol(self) -> np.ndarray:
        """A symbol whose two time-domain halves are identical.

        Filling only even bins makes `x[n] == x[n + N/2]`, which is what the
        receiver's detector correlates against. Half the carriers therefore
        carry the whole symbol, so they are lifted to keep the burst's envelope
        flat -- a step in RMS partway through a burst would upset both any AGC
        in the path and the detector's own normalisation.
        """
        return self._preamble

    def training_symbol(self) -> np.ndarray:
        """A known symbol on every active carrier, for the channel estimate."""
        return self._training

    def data_symbol(self, data_values) -> np.ndarray:
        """A payload symbol: pilots on the pilot carriers, data on the rest."""
        p = self.profile
        values = np.zeros(p.num_carriers, dtype=np.complex128)
        values[p.pilot_positions] = self.known.pilots
        values[p.data_positions] = np.asarray(data_values, dtype=np.complex128)
        return self.symbol(values)

    # -- whole burst --------------------------------------------------------

    def burst(self, data_grid) -> np.ndarray:
        """Preamble, training and the given data symbols as one real waveform.

        `data_grid` is (symbols, data carriers). The result is scaled once, as a
        whole, to the profile's target RMS: scaling per symbol would put a step
        in the channel the equaliser then has to undo.
        """
        p = self.profile
        grid = np.atleast_2d(np.asarray(data_grid, dtype=np.complex128))
        if grid.shape[1] != p.num_data_carriers:
            raise ValueError(
                f"expected {p.num_data_carriers} data carriers per symbol, "
                f"got {grid.shape[1]}"
            )
        parts = [self.preamble_symbol()] * p.preamble_symbols
        parts += [self.training_symbol()] * p.training_symbols
        parts += [self.data_symbol(row) for row in grid]
        waveform = np.concatenate(parts)
        rms = float(np.sqrt(np.mean(waveform ** 2)))
        if rms > 0.0:
            waveform = waveform * (p.tx_rms / rms)
        # OFDM peaks well above its RMS; at the profile's target level nothing
        # should reach full scale, but never hand a soundcard a sample above it.
        return np.clip(waveform, -1.0, 1.0)

    def overhead_symbols(self) -> int:
        """Symbols before the first data-bearing one."""
        return self.profile.preamble_symbols + self.profile.training_symbols

    def burst_samples(self, data_symbols: int) -> int:
        """Length in samples of a burst carrying `data_symbols` data symbols."""
        return (self.overhead_symbols() + int(data_symbols)) * self.profile.symbol_samples

    def crest_factor_db(self, waveform) -> float:
        """Peak-to-RMS of a waveform in dB -- the headroom the radio path needs."""
        waveform = np.asarray(waveform, dtype=np.float64)
        rms = float(np.sqrt(np.mean(waveform ** 2)))
        peak = float(np.max(np.abs(waveform))) if len(waveform) else 0.0
        if rms <= 0.0 or peak <= 0.0:
            return float("nan")
        return float(20.0 * np.log10(peak / rms))


class BurstReceiver:
    """Channel estimation, equalisation and per-carrier soft weights.

    Constructed around an already-aligned, already-CFO-corrected burst: finding
    the burst is `sync.py`'s job, decoding bytes out of it is `framing.py`'s.
    """

    def __init__(self, profile: OfdmProfile, samples, burst_start: int) -> None:
        self.profile = profile
        self.samples = np.asarray(samples, dtype=np.float64)
        self.burst_start = int(burst_start)
        self.known = known_sequences(profile)
        self.channel, self.noise_var = self._estimate_channel()

    # -- geometry -----------------------------------------------------------

    def body_start(self, symbol_index: int) -> int:
        """First sample of symbol `symbol_index`'s body (its prefix skipped)."""
        p = self.profile
        return self.burst_start + symbol_index * p.symbol_samples + p.cp_length

    def bins(self, symbol_index: int) -> np.ndarray:
        """Active-carrier bins of one symbol.

        The cyclic prefix is discarded rather than used: its whole purpose is to
        be the part a delayed echo may corrupt.
        """
        p = self.profile
        start = self.body_start(symbol_index)
        body = self.samples[start: start + p.fft_size]
        if len(body) < p.fft_size:
            raise IndexError(f"burst ends before symbol {symbol_index}")
        return np.fft.rfft(body)[p.carriers]

    def available_data_symbols(self) -> int:
        """How many data-bearing symbols the buffer actually holds."""
        p = self.profile
        first = self.body_start(p.preamble_symbols + p.training_symbols)
        remaining = len(self.samples) - first
        if remaining < p.fft_size:
            return 0
        return 1 + max(0, (remaining - p.fft_size) // p.symbol_samples)

    # -- channel ------------------------------------------------------------

    def _estimate_channel(self) -> tuple[np.ndarray, float]:
        """Least-squares channel estimate and noise power, from the training block.

        The training symbols are identical, so averaging them gives the channel
        and differencing them gives the noise: with T symbols the estimate's own
        noise is sigma^2/T, which is then subtracted back out of the signal
        power so the reported SNR is not inflated by it.
        """
        p = self.profile
        first = p.preamble_symbols
        observed = np.array([self.bins(first + i) for i in range(p.training_symbols)])
        # Training values are +-1, so dividing by them is multiplying by them.
        estimate = observed.mean(axis=0) * self.known.training
        difference = (observed[0] - observed[1]) / 2.0
        noise_var = float(2.0 * np.mean(np.abs(difference) ** 2))
        return estimate, noise_var

    @property
    def snr_db(self) -> float | None:
        """Mean per-carrier SNR in dB, or None if the estimate is not usable."""
        if self.noise_var <= 0.0:
            # A perfectly clean channel: report the dynamic range honestly
            # rather than dividing by zero or claiming infinity.
            return 99.0
        power = float(np.mean(np.abs(self.channel) ** 2))
        signal = power - self.noise_var / self.profile.training_symbols
        if signal <= 0.0:
            return None
        return float(10.0 * np.log10(signal / self.noise_var))

    # -- equalisation -------------------------------------------------------

    def _equalise(self, symbol_index: int) -> tuple[np.ndarray, float]:
        """Zero-forcing equalise one symbol and remove its pilot phase error.

        Fitting a straight line through the pilot phases removes two things at
        once: the constant term is the common phase error a residual frequency
        offset leaves behind, and the slope is a timing or sample-clock offset,
        which is what two independent soundcards will produce on a real link.
        """
        p = self.profile
        equalised = self.bins(symbol_index) / self.channel
        residual = equalised[p.pilot_positions] * np.conj(self.known.pilots)
        phase = np.unwrap(np.angle(residual))
        slope, intercept = np.polyfit(p.pilot_positions.astype(np.float64), phase, 1)
        correction = np.exp(-1j * (intercept + slope * np.arange(p.num_carriers)))
        return equalised * correction, float(slope)

    def data_symbols(self, first: int, count: int) -> tuple[np.ndarray, np.ndarray, float]:
        """Equalised payload symbols, their noise power, and the mean phase slope.

        `first` counts data-bearing symbols, so 0 is the symbol right after the
        training block. Symbols are returned flattened in transmission order
        (symbol by symbol, carrier by carrier within each), which is the order
        `framing.py` serialised the coded bits into.

        The noise power returned is per carrier: zero-forcing divides by the
        channel, so a carrier the channel attenuated comes back with its noise
        amplified by the same factor. Handing that to the demapper is what lets
        the decoder discount a notched carrier instead of trusting it.
        """
        p = self.profile
        offset = p.preamble_symbols + p.training_symbols + int(first)
        rows = []
        slopes = []
        for index in range(int(count)):
            equalised, slope = self._equalise(offset + index)
            rows.append(equalised[p.data_positions])
            slopes.append(slope)
        symbols = (np.concatenate(rows) if rows
                   else np.zeros(0, dtype=np.complex128))
        gain = np.abs(self.channel[p.data_positions]) ** 2
        variance = self.noise_var / np.maximum(gain, np.finfo(np.float64).tiny)
        return symbols, np.tile(variance, max(1, len(rows)))[: len(symbols)], (
            float(np.mean(slopes)) if slopes else 0.0
        )
