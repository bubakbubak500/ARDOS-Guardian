"""Receiver measurements and the state a future adaptation controller reads.

Nothing here decides anything. Phase 1 measures the link and records what it
measured; stepping the MCS up and down on the strength of those numbers is the
next milestone, and inventing thresholds before there is on-air data to fit
them to would only encode a guess.

The one rule this module enforces is that an unavailable measurement is `None`.
A faked SNR would eventually be fed to an adaptation loop that trusted it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class LinkMetrics:
    """What the demodulator measured about one received burst."""

    #: Schmidl-Cox plateau height, 0..1. High means the two halves of the
    #: preamble matched, which is the receiver's own confidence that it found a
    #: burst rather than a coincidence in the noise.
    sync_confidence: float | None = None
    #: Carrier frequency offset in Hz, as estimated from the preamble.
    cfo_hz: float | None = None
    #: Per-carrier SNR in dB, from the known training symbols. Honest: it
    #: compares against symbols the receiver knew in advance.
    snr_db: float | None = None
    #: RMS error-vector magnitude as a fraction, decision-directed on the data
    #: section unless `evm_source` says otherwise.
    evm_rms: float | None = None
    evm_source: str | None = None
    #: Complex channel response, one entry per active carrier, in carrier order.
    channel_response: np.ndarray | None = None
    #: RMS of the received audio over the burst, in 0..1 full scale.
    audio_rms: float | None = None
    #: Peak-to-RMS of the received audio in dB -- how close the path came to
    #: clipping, and the reason a burst may have failed despite a good SNR.
    crest_factor_db: float | None = None
    #: Residual phase slope across the band per symbol, in radians per carrier.
    #: A non-zero mean is a timing or sample-clock offset, not noise.
    phase_slope: float | None = None
    frame_ok: bool = False
    mcs: int | None = None
    #: Why a burst failed, for the log. None when it did not.
    error: str | None = None

    @property
    def snr_available(self) -> bool:
        return self.snr_db is not None

    def summary(self) -> str:
        """One line for the operator log."""
        parts = []
        if self.snr_db is not None:
            parts.append(f"SNR {self.snr_db:.1f} dB")
        if self.evm_rms is not None:
            parts.append(f"EVM {self.evm_rms * 100:.1f} %")
        if self.cfo_hz is not None:
            parts.append(f"CFO {self.cfo_hz:+.1f} Hz")
        if self.sync_confidence is not None:
            parts.append(f"sync {self.sync_confidence:.2f}")
        if self.mcs is not None:
            parts.append(f"MCS{self.mcs}")
        parts.append("ok" if self.frame_ok else (self.error or "failed"))
        return ", ".join(parts)


@dataclass
class OfdmStatus:
    """A snapshot of the backend, shaped for UI polling.

    Deliberately plain data with no Qt anywhere near it: the transfer panel
    polls this the same way it polls `VaraSnapshot`.
    """

    state: str = "idle"  # idle|synchronizing|receiving|transmitting|waiting_ack|failed
    mcs: int = 0
    profile: str = ""
    snr_db: float | None = None
    evm_rms: float | None = None
    retries: int = 0
    tx_bytes: int = 0
    rx_bytes: int = 0
    total_bytes: int = 0
    last_block_ok: bool | None = None
    #: Measured from acknowledged bytes over elapsed time. `None` until enough
    #: has moved to divide by -- never a nominal figure from the profile.
    est_bitrate_bps: float | None = None

    @property
    def percent(self) -> int:
        """Transfer progress, 0..100, or 0 when the total is not known yet."""
        moved = max(self.tx_bytes, self.rx_bytes)
        if not self.total_bytes:
            return 0
        return int(min(100.0, 100.0 * moved / self.total_bytes))


@dataclass
class AdaptationState:
    """History an adaptation controller would consume. Phase 1 only fills it.

    Accumulated per transfer rather than per burst so a single bad burst cannot
    move a decision, which is the failure mode of every naive rate-control loop.
    """

    blocks_sent: int = 0
    blocks_acked: int = 0
    blocks_nacked: int = 0
    blocks_received: int = 0
    duplicates: int = 0
    retransmissions: int = 0
    snr_history: list[float] = field(default_factory=list)
    evm_history: list[float] = field(default_factory=list)
    #: Mean |H|^2 per active carrier, accumulated over every decoded burst. This
    #: is the input a per-subcarrier bit-loading map would be computed from.
    carrier_power: np.ndarray | None = None
    _carrier_bursts: int = 0

    def record_burst(self, metrics: LinkMetrics) -> None:
        """Fold one received burst's measurements into the history."""
        if metrics.snr_db is not None:
            self.snr_history.append(float(metrics.snr_db))
        if metrics.evm_rms is not None:
            self.evm_history.append(float(metrics.evm_rms))
        if metrics.channel_response is not None:
            power = np.abs(np.asarray(metrics.channel_response)) ** 2
            if self.carrier_power is None or self.carrier_power.shape != power.shape:
                self.carrier_power = power.astype(np.float64).copy()
                self._carrier_bursts = 1
            else:
                self._carrier_bursts += 1
                # Running mean, so a long transfer does not weight its first
                # burst more heavily than its last.
                self.carrier_power += (power - self.carrier_power) / self._carrier_bursts

    @property
    def packet_error_rate(self) -> float | None:
        """Fraction of blocks that were never acknowledged at all.

        Distinct from `retransmission_rate`: a block that needed three tries and
        got through is a healthy link under stress, not a lost packet.
        """
        if not self.blocks_sent:
            return None
        return (self.blocks_sent - self.blocks_acked) / self.blocks_sent

    @property
    def retransmission_rate(self) -> float | None:
        """Retransmissions per block sent."""
        if not self.blocks_sent:
            return None
        return self.retransmissions / self.blocks_sent

    @property
    def mean_snr_db(self) -> float | None:
        return float(np.mean(self.snr_history)) if self.snr_history else None

    @property
    def worst_carriers(self) -> np.ndarray | None:
        """Active-carrier indices ordered worst first by accumulated power."""
        if self.carrier_power is None:
            return None
        return np.argsort(self.carrier_power)

    def summary(self) -> str:
        """One line for the log at the end of a transfer.

        Leads with whichever role this station played: a receiver never sends a
        block, so reporting "0/0 blocks acked" at the end of a good reception
        would read as a failure.
        """
        if self.blocks_sent:
            parts = [f"{self.blocks_acked}/{self.blocks_sent} blocks acked"]
        else:
            parts = [f"{self.blocks_received} blocks accepted"]
        if self.retransmissions:
            parts.append(f"{self.retransmissions} retransmissions")
        if self.duplicates:
            parts.append(f"{self.duplicates} duplicates dropped")
        if self.blocks_nacked:
            parts.append(f"{self.blocks_nacked} nacked")
        snr = self.mean_snr_db
        if snr is not None:
            parts.append(f"mean SNR {snr:.1f} dB")
        return ", ".join(parts)
