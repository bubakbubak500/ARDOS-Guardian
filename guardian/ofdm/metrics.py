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
    #: Per-carrier SNR in dB from the two known training symbols.
    #:
    #: This is the *random noise* SNR and nothing else. The training symbols are
    #: identical, so the estimate comes from their difference -- and any
    #: impairment that is the same in both, which is every deterministic
    #: distortion a radio path adds, cancels exactly and is not counted. On the
    #: 2026-08-09 IC-705 tests it read 18-20 dB while the link was really
    #: delivering 9-12 dB. Believe `residual_snr_db` over this one.
    snr_db: float | None = None
    #: RMS error-vector magnitude as a fraction. Measured against the symbols
    #: the sender must have transmitted whenever the section decoded
    #: (`evm_source == "reference"`), and decision-directed only as a fallback.
    #:
    #: The distinction matters most exactly where it is least obvious: a
    #: decision-directed figure snaps every symbol to its nearest constellation
    #: point, so a dense constellation that is decoding *badly* reports a
    #: flattering EVM. 64-QAM measured 18.8% decision-directed and 30.5% against
    #: the reference on the same failing burst.
    evm_rms: float | None = None
    evm_source: str | None = None
    #: Post-equalisation SNR in dB, derived from `evm_rms`.
    #:
    #: The honest figure, and the one an MCS decision belongs on: it counts
    #: every impairment between the sender's constellation and the receiver's
    #: equalised symbols -- noise, distortion, channel-estimate error and the
    #: equaliser's own losses. About 3 dB below `snr_db` on a clean simulated
    #: channel, which is the receiver's implementation loss; anything wider than
    #: that on air is distortion the training symbols cannot see.
    residual_snr_db: float | None = None
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
    sample_clock_ppm: float | None = None
    #: Numerical conditioning and implementation loss of the active equalizer.
    equalizer_condition: float | None = None
    noise_enhancement_db: float | None = None
    residual_isi_rms: float | None = None
    equalizer_mode: str | None = None
    equalizer_iterations: int = 0
    harq_combined_blocks: int = 0
    #: Generalized mutual information from reference bits/LLRs, bits per
    #: constellation symbol. Available only after CRC proves the reference.
    gmi_bits_per_symbol: float | None = None
    turbo_iterations: int = 0
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
        if self.residual_snr_db is not None:
            parts.append(f"SNR {self.residual_snr_db:.1f} dB")
        if self.snr_db is not None:
            parts.append(f"noise-only {self.snr_db:.1f} dB")
        if self.evm_rms is not None:
            parts.append(f"EVM {self.evm_rms * 100:.1f} %")
        if self.cfo_hz is not None:
            parts.append(f"CFO {self.cfo_hz:+.1f} Hz")
        if self.sync_confidence is not None:
            parts.append(f"sync {self.sync_confidence:.2f}")
        if self.equalizer_mode:
            parts.append(f"EQ {self.equalizer_mode}/{self.equalizer_iterations}")
        if self.noise_enhancement_db is not None:
            parts.append(f"noise gain {self.noise_enhancement_db:+.1f} dB")
        if self.gmi_bits_per_symbol is not None:
            parts.append(f"GMI {self.gmi_bits_per_symbol:.2f} bit/sym")
        if self.turbo_iterations:
            parts.append(f"turbo {self.turbo_iterations} iter")
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
    direction: str = ""  # send|receive for the active UI progress panel
    mcs: int = 0
    profile: str = ""
    fec: str = "1/2"
    burst_bytes: int = 512
    arq_block_bytes: int = 512
    snr_db: float | None = None
    evm_rms: float | None = None
    remote_snr_db: float | None = None
    remote_evm_rms: float | None = None
    retries: int = 0
    retransmitted_bytes: int = 0
    protocol_overhead_bytes: int = 0
    data_bursts: int = 0
    ack_bursts: int = 0
    ptt_cycles: int = 0
    data_airtime_seconds: float = 0.0
    ack_airtime_seconds: float = 0.0
    train_gap_seconds: float = 0.0
    turnaround_seconds: float = 0.0
    tx_lead_seconds: float = 0.0
    tx_guard_seconds: float = 0.0
    tx_tail_seconds: float = 0.0
    keyed_seconds: float = 0.0
    rx_trigger_wait_seconds: float = 0.0
    rx_capture_seconds: float = 0.0
    rx_hangover_seconds: float = 0.0
    rx_decode_seconds: float = 0.0
    elapsed_seconds: float = 0.0
    goodput_bps: float | None = None
    tx_bytes: int = 0
    rx_bytes: int = 0
    total_bytes: int = 0
    last_block_ok: bool | None = None
    last_burst_blocks: int = 0
    last_first_pass_ok: int = 0
    #: Measured from acknowledged bytes over elapsed time. `None` until enough
    #: has moved to divide by -- never a nominal figure from the profile.
    est_bitrate_bps: float | None = None
    # Identity for the active SC-FTN payload leg.  Operations fills these on a
    # returned snapshot from the negotiated session message; keeping them on
    # the typed status makes the UI independent of the VARA state object.
    transfer_source: str = ""
    transfer_destination: str = ""
    transfer_via: str = ""

    @property
    def keyed_duty_cycle(self) -> float | None:
        if self.elapsed_seconds <= 0.0:
            return None
        return min(1.0, max(0.0, self.keyed_seconds / self.elapsed_seconds))

    @property
    def percent(self) -> int:
        """Transfer progress, 0..100, or 0 when the total is not known yet."""
        moved = max(self.tx_bytes, self.rx_bytes)
        if not self.total_bytes:
            return 0
        return int(min(100.0, 100.0 * moved / self.total_bytes))


@dataclass
class AdaptationState:
    """Measured link history retained beside the active adaptation controller.

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
    #: Post-equalisation SNR per burst -- the honest one, and what an adaptation
    #: controller should fit its thresholds to.
    residual_history: list[float] = field(default_factory=list)
    evm_history: list[float] = field(default_factory=list)
    #: Mean |H|^2 per active carrier, accumulated over every decoded burst. This
    #: is the input a per-subcarrier bit-loading map would be computed from.
    carrier_power: np.ndarray | None = None
    _carrier_bursts: int = 0

    def record_burst(self, metrics: LinkMetrics) -> None:
        """Fold one received burst's measurements into the history."""
        if metrics.snr_db is not None:
            self.snr_history.append(float(metrics.snr_db))
        if metrics.residual_snr_db is not None:
            self.residual_history.append(float(metrics.residual_snr_db))
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
        """Mean noise-only SNR. See `LinkMetrics.snr_db` for why that is not much."""
        return float(np.mean(self.snr_history)) if self.snr_history else None

    @property
    def mean_residual_snr_db(self) -> float | None:
        """Mean post-equalisation SNR -- the figure that predicts what an MCS does."""
        return (float(np.mean(self.residual_history))
                if self.residual_history else None)

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
        # The post-equalisation figure leads, because it is the one that says
        # whether the MCS in use was the right choice. The noise-only figure
        # follows it when both exist, so the gap between them -- which is the
        # distortion in the path -- is visible in the log without arithmetic.
        residual = self.mean_residual_snr_db
        if residual is not None:
            parts.append(f"mean SNR {residual:.1f} dB")
        snr = self.mean_snr_db
        if snr is not None:
            parts.append(f"noise-only {snr:.1f} dB" if residual is not None
                         else f"mean SNR {snr:.1f} dB")
        return ", ".join(parts)
