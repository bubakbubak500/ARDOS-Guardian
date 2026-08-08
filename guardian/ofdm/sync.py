"""Finding a burst in a stream of audio.

The modem is never handed a perfectly aligned array. Real audio arrives with an
arbitrary delay, a noise floor before and after, whatever gain the radio felt
like, and -- on an SSB path -- the whole spectrum shifted by the difference
between two dial settings. Acquisition happens in four steps.

The time-domain steps -- the repetition metric, the coarse offset, the correlation
against the training symbol -- run on a copy filtered to the occupied band by
`phy.band_analytic`, because otherwise they compete with noise from ten times the
bandwidth the burst actually uses. What is handed on to the demodulator is *not*
that copy: a brick wall across a burst clips the skirts its own symbol boundaries
produce, which is inter-symbol distortion the cyclic prefix was never sized for.
The demodulator gets the unfiltered signal with only the frequency offset removed,
and selects the band far more cleanly with the FFT it already performs per symbol.

1.  **Detection and coarse timing.** The first preamble symbol repeats after
    fft_size/2 samples (see `phy.OfdmModulator.preamble_symbol`), so correlating
    the signal against itself at that lag produces a plateau exactly where the
    burst starts. The plateau is as wide as the cyclic prefix, because the prefix
    is part of the same periodic run, and its height is a normalised correlation:
    that number *is* the receiver's confidence that it found a burst.

2.  **Frequency offset.** The phase of the same correlation is the offset. One
    repetition spans half a symbol, so the phase advances by pi times the offset
    measured in subcarrier spacings -- unambiguous while the offset stays inside
    one spacing, which on an audio path it comfortably does.

3.  **Fine timing.** The coarse estimate is good to a few samples, but the
    plateau's edges blur in noise and the preamble cannot resolve better than that
    anyway, being periodic. So the receiver matched-filters against the whole
    known burst head -- every preamble and training symbol concatenated -- which
    is not periodic across its full length and therefore has one unambiguous peak,
    and is the longest known sequence available. The search spans a whole preamble
    either way, so it does not matter which preamble symbol the plateau found.

4.  **Frequency offset again, properly.** The plateau estimate is coarse for two
    reasons: its window only partly overlaps the repetition, and the Hilbert
    transform behind the analytic signal is not local, so the two halves of a
    preamble symbol pick up slightly different contributions from the rest of the
    burst however exactly they are aligned. Together those leave a residual that
    shows up as an artificial ~55 dB noise floor on a clean channel. So once the
    symbol boundary is known, the residual is measured in the bin domain instead:
    the two identical training symbols differ only by the phase a residual offset
    accumulates over one symbol period, and an FFT has no edge effects to bias
    that. The plateau still does the coarse work, because the bin-domain
    measurement wraps outside +-N/(2*(N+Ncp)) subcarrier spacings and the plateau
    is unambiguous across a whole spacing.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import OfdmProfile
from .phy import OfdmModulator, analytic, band_analytic


@dataclass
class SyncResult:
    """Where a burst starts, and the corrections already applied to get there."""

    #: Index in `samples` of the burst's very first sample (the first preamble
    #: symbol's cyclic prefix).
    burst_start: int
    #: Schmidl-Cox plateau height, 0..1.
    confidence: float
    #: Estimated carrier frequency offset in Hz, already removed from `samples`.
    cfo_hz: float
    #: The whole input buffer with the frequency offset corrected.
    samples: np.ndarray
    #: RMS of the input over the detected burst, in full-scale units.
    audio_rms: float
    #: Peak-to-RMS of the input over the detected burst, in dB.
    crest_factor_db: float


#: How much of the buffer's loudest window a candidate must carry to be believed.
#:
#: The metric below is a normalised correlation, so it is blind to amplitude --
#: and band-limiting leaves the silence around a burst holding a faint filter
#: tail which, being dominated by a couple of spectral components, is very nearly
#: a sine wave. That correlates with itself beautifully: it scored 0.94 on a guard
#: interval 40 dB quieter than the burst beside it, and the detector locked onto
#: the silence. A burst's envelope is flat by construction (`OfdmModulator.burst`
#: scales the whole waveform at once and lifts the preamble to match), so every
#: part of a real burst sits within a couple of dB of the loudest window;
#: requiring a quarter of that, 6 dB down, excludes the tail with room to spare.
_ENERGY_GATE = 0.25


def repetition_metric(samples, profile: OfdmProfile
                      ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Schmidl-Cox correlation over a buffer.

    Returns `(metric, correlation, energy)` where `metric[d]` is the squared
    correlation coefficient between the two halves of a window starting at `d`,
    `correlation[d]` is the raw complex value whose phase carries the frequency
    offset, and `energy[d]` is how much signal that window holds at all.
    `metric` reaches 1 on a clean repetition and tends to
    `(snr/(1+snr))**2` in noise, where `snr` is measured in the occupied band.

    Both halves normalise it, not just the second. Schmidl and Cox divide by the
    second half alone, which is fine while a burst is arriving but unbounded when
    one is *leaving*: the trailing edge puts a loud first half against a quiet
    second one, and the metric there reached 45 in testing -- forty-five times
    higher than a real burst can score, so the detector locked onto the end of
    every strong burst and found nothing. Dividing by the product of the two makes
    it a genuine correlation coefficient that Cauchy-Schwarz bounds at 1.
    """
    lag = profile.fft_size // 2
    z = band_analytic(samples, profile)
    count = len(z) - 2 * lag + 1
    if count < 1:
        return (np.zeros(0), np.zeros(0, dtype=np.complex128), np.zeros(0))

    # Sliding sums of `lag` terms, from cumulative sums.
    product = np.conj(z[:-lag]) * z[lag:]
    cum_product = np.concatenate([[0.0 + 0.0j], np.cumsum(product)])
    cum_energy = np.concatenate([[0.0], np.cumsum(np.abs(z) ** 2)])
    correlation = cum_product[lag: lag + count] - cum_product[:count]
    first_half = cum_energy[lag: lag + count] - cum_energy[:count]
    second_half = cum_energy[2 * lag: 2 * lag + count] - cum_energy[lag: lag + count]
    floor = np.finfo(np.float64).tiny
    metric = np.abs(correlation) ** 2 / np.maximum(first_half * second_half, floor)
    return metric, correlation, first_half + second_half


def _plateaus(metric: np.ndarray, energy: np.ndarray,
              profile: OfdmProfile) -> list[np.ndarray]:
    """Plateaus worth believing, earliest first.

    Order is by position, not by height: a burst produces one plateau per
    preamble symbol at much the same height, so picking the tallest would be a
    coin toss between them, while the earliest is always the burst's own start.
    A noise spike clears the threshold occasionally but is one or two samples
    wide, where a real plateau is as wide as the cyclic prefix, so requiring a
    quarter of that separates them.
    """
    if not len(metric):
        return []
    loud_enough = energy >= _ENERGY_GATE * float(energy.max())
    gated = np.where(loud_enough, metric, 0.0)
    peak = float(gated.max())
    if peak < profile.detection_threshold:
        return []
    # Relative to the peak as well as absolute: on a strong burst this keeps a
    # lucky noise spike far below the bar, and the plateaus are all near the peak.
    threshold = max(profile.detection_threshold, 0.6 * peak)
    above = np.flatnonzero(gated >= threshold)
    if not len(above):
        return []
    minimum_width = max(2, profile.cp_length // 4)
    breaks = np.flatnonzero(np.diff(above) > 1)
    starts = np.concatenate([[0], breaks + 1])
    ends = np.concatenate([breaks + 1, [len(above)]])
    return [above[start:end] for start, end in zip(starts, ends)
            if end - start >= minimum_width]


def _valid_correlation(window: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Cross-correlation at every position where `reference` fully overlaps.

    Through the FFT rather than `np.correlate`, whose direct method would cost
    tens of millions of multiply-adds for a reference this long.
    """
    size = 1 << int(np.ceil(np.log2(len(window) + len(reference))))
    spectrum = np.fft.rfft(window, size) * np.fft.rfft(reference[::-1], size)
    return np.fft.irfft(spectrum, size)[len(reference) - 1: len(window)]


def burst_head(profile: OfdmProfile, modulator: OfdmModulator) -> np.ndarray:
    """The deterministic prefix every burst starts with: preamble then training."""
    return np.concatenate(
        [modulator.preamble_symbol()] * profile.preamble_symbols
        + [modulator.training_symbol()] * profile.training_symbols
    )


def _refine(samples: np.ndarray, coarse_start: int, profile: OfdmProfile,
            modulator: OfdmModulator) -> int | None:
    """Nail the burst start by correlating against the whole known burst head.

    Two things make this the right reference rather than a single training
    symbol. The training symbols are identical to each other, so one of them
    correlates equally well at either position and the frame would land a symbol
    out roughly half the time. And the head is the longest known sequence in the
    burst, so at low SNR it gives the sharpest peak available.

    `coarse_start` marks where *some* preamble symbol begins, to within about half
    a symbol, so the search spans every preamble symbol plus that slack. The head
    is not periodic across its full length, so the peak inside that span is
    unambiguous.
    """
    step = profile.symbol_samples
    reference = burst_head(profile, modulator)
    reference = reference - reference.mean()
    if not np.any(reference):  # pragma: no cover - a profile with no carriers
        return None

    low = max(0, coarse_start - profile.preamble_symbols * step - step)
    high = min(len(samples), coarse_start + step + len(reference))
    if high - low < len(reference):
        return None
    # A plain matched filter: the position of the largest correlation, with no
    # per-position normalisation. Dividing by the local energy would turn this
    # into a correlation coefficient, which sounds better but is the same
    # amplitude-blindness trap as `_ENERGY_GATE` describes -- the quietest
    # positions divide by nearly nothing. Against noise of one variance, and over
    # a span this narrow, the unnormalised peak is the optimal choice anyway.
    window = samples[low:high]
    correlation = np.abs(_valid_correlation(window, reference))
    return low + int(np.argmax(correlation))


def _rotate(envelope: np.ndarray, offset_in_spacings: float,
            profile: OfdmProfile) -> np.ndarray:
    """Undo a frequency offset, given in subcarrier spacings, and return real audio."""
    if not offset_in_spacings:
        return np.real(envelope)
    phase = (-2j * np.pi * offset_in_spacings
             * np.arange(len(envelope)) / profile.fft_size)
    return np.real(envelope * np.exp(phase))


def _residual_offset(samples: np.ndarray, burst_start: int,
                     profile: OfdmProfile) -> float | None:
    """Measure the leftover frequency offset from the training block, in spacings.

    The training symbols are identical, so whatever phase the second one has
    picked up relative to the first is the offset accumulated over one symbol
    period. Working from FFT bins rather than from the analytic signal is the
    point: there is no filter kernel reaching across the burst to bias it.
    """
    p = profile
    step = p.symbol_samples
    first = burst_start + p.preamble_symbols * step + p.cp_length
    if first < 0 or first + step + p.fft_size > len(samples):
        return None
    earlier = np.fft.rfft(samples[first: first + p.fft_size])[p.carriers]
    later = np.fft.rfft(samples[first + step: first + step + p.fft_size])[p.carriers]
    total = np.vdot(earlier, later)
    if total == 0.0:
        return None
    return float(np.angle(total) * p.fft_size / (2.0 * np.pi * step))


#: How many plateaus `candidates` will work up before giving up on a buffer.
#:
#: Any detector has a false-alarm rate, and a false alarm that lands *earlier* in
#: the buffer than the real burst would otherwise cost the whole exchange: the
#: header CRC would reject the noise and the real burst behind it would never be
#: looked at. Working up successive candidates costs one header decode each, a
#: few milliseconds, and removes that failure mode entirely.
MAX_CANDIDATES = 4


def candidates(samples, profile: OfdmProfile,
               modulator: OfdmModulator | None = None,
               limit: int = MAX_CANDIDATES) -> list[SyncResult]:
    """Every plausible burst position in `samples`, earliest first.

    The buffer may be any length and may hold noise before and after. Callers are
    expected to try these in order and stop at the first whose header decodes.
    """
    samples = np.asarray(samples, dtype=np.float64)
    modulator = modulator or OfdmModulator(profile)
    metric, correlation, energy = repetition_metric(samples, profile)
    plateaus = _plateaus(metric, energy, profile)
    if not plateaus:
        return []

    envelope = analytic(samples)
    in_band_raw = band_analytic(samples, profile)
    found: list[SyncResult] = []
    seen: set[int] = set()
    for plateau in plateaus[: max(1, int(limit))]:
        # Summing the raw correlation across the plateau before taking its angle
        # averages the noise down; averaging the angles instead would not.
        coarse = float(np.angle(correlation[plateau].sum()) / np.pi)

        # The plateau spans the prefix, from the symbol's first sample to its body.
        coarse_start = int(round((plateau[0] + plateau[-1]) / 2 - profile.cp_length / 2))
        burst_start = _refine(_rotate(in_band_raw, coarse, profile), coarse_start,
                              profile, modulator)
        if burst_start is None or burst_start < 0 or burst_start in seen:
            continue
        seen.add(burst_start)

        residual = _residual_offset(_rotate(envelope, coarse, profile),
                                    burst_start, profile)
        offset_in_spacings = coarse + (residual or 0.0)
        corrected = _rotate(envelope, offset_in_spacings, profile)

        span = corrected[burst_start: burst_start + modulator.burst_samples(1)]
        rms = float(np.sqrt(np.mean(span ** 2))) if len(span) else 0.0
        peak = float(np.max(np.abs(span))) if len(span) else 0.0
        crest = (float(20.0 * np.log10(peak / rms))
                 if rms > 0.0 and peak > 0.0 else float("nan"))
        found.append(SyncResult(
            burst_start=burst_start,
            confidence=float(metric[plateau].max()),
            cfo_hz=offset_in_spacings * profile.subcarrier_spacing,
            samples=corrected,
            audio_rms=rms,
            crest_factor_db=crest,
        ))
    return found


def detect(samples, profile: OfdmProfile,
           modulator: OfdmModulator | None = None) -> SyncResult | None:
    """The earliest plausible burst in `samples`, or None if there is not one."""
    found = candidates(samples, profile, modulator, limit=1)
    return found[0] if found else None
