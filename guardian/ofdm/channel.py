"""A deterministic channel simulator, so the modem is tested before it is aired.

Every impairment here is seeded and repeatable: the same spec and seed produce
the same samples, which is what makes a failure something you can go and look at
rather than something that happened once.

**How SNR is defined.** `snr_db` is the *in-band* SNR: the ratio of signal power
to the noise power that lands on the active subcarriers. That is the quantity the
receiver measures from its training symbols and the quantity Eb/N0 theory is
written in, so a stated 8 dB and a measured 8 dB mean the same thing. White noise
at that density also fills the rest of the audio band, where the demodulator
never looks, so the *wideband* SNR of the resulting audio is lower -- by
10*log10(1/bandwidth_fraction), about 9.9 dB for the BENCH profile. Quoting the
wideband figure would make the modem look roughly 10 dB better than it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import OfdmProfile
from .phy import analytic


@dataclass(frozen=True)
class ChannelSpec:
    """Which impairments to apply, and how much of each."""

    #: In-band SNR in dB. None means no noise at all.
    snr_db: float | None = None
    #: Flat gain. Covers both a hot soundcard and a quiet one.
    gain: float = 1.0
    #: Samples of leading delay, i.e. how late in the buffer the burst starts.
    delay: int = 0
    #: Carrier frequency offset in Hz (an SSB dial difference), and a fixed
    #: phase rotation. Both act on the analytic signal, which is what a real
    #: frequency-translating path does to real audio.
    freq_offset_hz: float = 0.0
    phase_offset: float = 0.0
    #: Hard clip at this fraction of the signal's own peak. None means no clip.
    clip_ratio: float | None = None
    #: Echoes as (delay in samples, complex gain). A complex gain is a phase
    #: shift, which is what an echo in a passband channel actually is.
    multipath: tuple[tuple[int, complex], ...] = ()
    #: Frequency-selective attenuation as (low Hz, high Hz, dB). dB is negative.
    notch: tuple[float, float, float] | None = None
    #: Sample-clock offset in parts per million, as between two soundcards.
    ppm: float = 0.0
    #: Extra samples of noise floor appended after the burst.
    trailing: int = 0

    def describe(self) -> str:
        """One line naming what is switched on."""
        parts = []
        if self.snr_db is not None:
            parts.append(f"SNR {self.snr_db:g} dB in-band")
        if self.gain != 1.0:
            parts.append(f"gain x{self.gain:g}")
        if self.delay:
            parts.append(f"delay {self.delay} samples")
        if self.freq_offset_hz:
            parts.append(f"CFO {self.freq_offset_hz:+g} Hz")
        if self.phase_offset:
            parts.append(f"phase {self.phase_offset:+.2f} rad")
        if self.clip_ratio is not None:
            parts.append(f"clip at {self.clip_ratio:g} of peak")
        if self.multipath:
            echoes = ", ".join(f"{g:.2f}@{d}" for d, g in self.multipath)
            parts.append(f"multipath [{echoes}]")
        if self.notch:
            low, high, db = self.notch
            parts.append(f"notch {low:.0f}-{high:.0f} Hz {db:g} dB")
        if self.ppm:
            parts.append(f"clock {self.ppm:+g} ppm")
        return ", ".join(parts) or "ideal"


@dataclass
class Channel:
    """Applies a `ChannelSpec` to real audio. Callable, so it drops into a pipe."""

    profile: OfdmProfile
    spec: ChannelSpec = field(default_factory=ChannelSpec)
    seed: int = 0xA5

    def __post_init__(self) -> None:
        self._rng = np.random.default_rng(self.seed)

    def reset(self) -> None:
        """Rewind the noise generator, so a repeated run is bit-identical."""
        self._rng = np.random.default_rng(self.seed)

    # -- individual impairments ---------------------------------------------

    def multipath(self, x: np.ndarray) -> np.ndarray:
        """Sum delayed, phase-rotated copies of the signal."""
        taps = self.spec.multipath
        if not taps:
            return x
        span = max(int(delay) for delay, _ in taps)
        z = analytic(x)
        out = np.zeros(len(x) + span, dtype=np.complex128)
        for delay, gain in taps:
            out[int(delay): int(delay) + len(z)] += complex(gain) * z
        return np.real(out)

    def notch(self, x: np.ndarray) -> np.ndarray:
        """Attenuate one band, the way an interferer or a filter notch would."""
        if self.spec.notch is None:
            return x
        low, high, db = self.spec.notch
        spectrum = np.fft.rfft(x)
        freqs = np.fft.rfftfreq(len(x), 1.0 / self.profile.sample_rate)
        spectrum[(freqs >= low) & (freqs <= high)] *= 10.0 ** (db / 20.0)
        return np.fft.irfft(spectrum, n=len(x))

    def resample(self, x: np.ndarray) -> np.ndarray:
        """Stretch the timebase, emulating a sample-clock offset in ppm."""
        if not self.spec.ppm:
            return x
        ratio = 1.0 + self.spec.ppm / 1e6
        count = int(len(x) / ratio)
        positions = np.arange(count) * ratio
        return np.interp(positions, np.arange(len(x)), x)

    def translate(self, x: np.ndarray) -> np.ndarray:
        """Shift the whole spectrum, as an SSB path with a dial error does."""
        if not self.spec.freq_offset_hz and not self.spec.phase_offset:
            return x
        n = np.arange(len(x))
        angle = (2.0 * np.pi * self.spec.freq_offset_hz * n / self.profile.sample_rate
                 + self.spec.phase_offset)
        return np.real(analytic(x) * np.exp(1j * angle))

    def clip(self, x: np.ndarray) -> np.ndarray:
        """Hard-limit the peaks, as an overdriven input stage does."""
        if self.spec.clip_ratio is None:
            return x
        limit = float(np.max(np.abs(x))) * float(self.spec.clip_ratio)
        return np.clip(x, -limit, limit)

    def noise(self, count: int, signal_power: float) -> np.ndarray:
        """Gaussian noise at the density the requested in-band SNR implies."""
        if self.spec.snr_db is None or signal_power <= 0.0:
            return np.zeros(count)
        in_band = signal_power / (10.0 ** (self.spec.snr_db / 10.0))
        # Spread that in-band power over the whole audio band.
        sigma = float(np.sqrt(in_band / self.profile.bandwidth_fraction))
        return self._rng.normal(0.0, sigma, count)

    # -- the whole path -----------------------------------------------------

    def __call__(self, samples) -> np.ndarray:
        """Push one burst through the path, in the order a radio link would.

        Gain and echoes first (the transmitter and the propagation), then the
        selective response, then the clock and dial errors, then clipping in the
        receiver's input stage, and only then the noise the soundcard adds --
        which is why noise is not clipped, and why the leading and trailing
        silence carries the same noise floor as the burst.
        """
        x = np.asarray(samples, dtype=np.float64) * float(self.spec.gain)
        x = self.multipath(x)
        x = self.notch(x)
        x = self.resample(x)
        x = self.translate(x)
        x = self.clip(x)
        # The reference for SNR is the burst itself, measured before it is
        # buried in silence -- padding first would understate the signal power
        # and hand back a quieter channel than was asked for.
        signal_power = float(np.mean(x ** 2)) if len(x) else 0.0
        padded = np.concatenate([
            np.zeros(max(0, int(self.spec.delay))),
            x,
            np.zeros(max(0, int(self.spec.trailing))),
        ])
        return padded + self.noise(len(padded), signal_power)


def ideal(profile: OfdmProfile) -> Channel:
    """A channel that changes nothing -- the array-to-array reference case."""
    return Channel(profile, ChannelSpec())


def realistic(profile: OfdmProfile, snr_db: float = 15.0, seed: int = 0xA5) -> Channel:
    """A conservative everything-at-once path, for bench runs and smoke tests.

    Late start, quiet radio, a 1 ms echo, a dial error and a clock offset. Each
    figure is small on its own; the point is that they are all present at the
    same time, which is the situation a burst has to survive on air.
    """
    one_ms = int(round(profile.sample_rate / 1000))
    return Channel(
        profile,
        ChannelSpec(
            snr_db=snr_db,
            gain=0.7,
            delay=3 * profile.symbol_samples + 137,
            freq_offset_hz=2.0,
            phase_offset=0.8,
            multipath=((0, 1.0), (one_ms, 0.35 + 0.15j)),
            ppm=5.0,
            trailing=profile.symbol_samples,
        ),
        seed=seed,
    )
