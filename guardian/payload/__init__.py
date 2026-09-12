"""Payload-transport backends.

The control handshake (Phase 2) negotiates *who/when/next-hop*. The payload
backend is the swappable piece that actually moves the message bytes once a hop
is agreed:

  * VaraP2PBackend — Guardian owns VARA, opens a peer-to-peer session and pumps
                     the payload itself (immediate, self-contained). The default.
  * OfdmVhfBackend — Guardian SC-FTN modem over the soundcard and Guardian's
                     own PTT, with no VARA at all.

An operator-driven Winlink hand-off backend existed until 0.6.26. It was a
fallback while `vara_p2p` was unproven on air; once two-station transfers
worked it only offered a way to configure the station into a slower manual
workflow. An unrecognised name maps onto VARA P2P, so a config written by an
older Guardian still loads and a typo cannot leave a station with no transport.
"""

from .base import PayloadBackend
from .ofdm_vhf import OfdmVhfBackend
from .negotiated import NegotiatedPayload
from .vara_p2p import VaraP2PBackend

__all__ = [
    "NegotiatedPayload", "OfdmVhfBackend", "PayloadBackend",
    "VaraP2PBackend", "make_backend",
]


def _make_vara(*, vara=None, on_log=None, on_qsy=None, on_receive_qsy=None,
               on_unqsy=None, on_acquire=None, on_release=None,
               on_handoff_failed=None, **_unused):
    """Build the VARA P2P backend, ignoring dependencies it has no use for."""
    return VaraP2PBackend(
        vara=vara,
        on_log=on_log,
        on_qsy=on_qsy,
        on_receive_qsy=on_receive_qsy,
        on_unqsy=on_unqsy,
        on_acquire=on_acquire,
        on_release=on_release,
        on_handoff_failed=on_handoff_failed,
    )


def _make_ofdm(*, on_log=None, on_qsy=None, on_receive_qsy=None, on_unqsy=None,
               on_acquire=None, on_release=None, ofdm_profile="BENCH", ofdm_mcs=1,
               ofdm_tx_lead_ms=60, ofdm_tx_tail_ms=60, ofdm_max_retries=4,
               ofdm_adaptive_fec=True, ofdm_modern_ldpc=False, ofdm_fec="1/2",
               ofdm_adaptive_burst=True, ofdm_burst_bytes=4096,
               ofdm_min_burst_bytes=512, ofdm_max_burst_bytes=8192,
               ofdm_arq_block_bytes=512, ofdm_timeout_multiplier=1.0,
               ofdm_legacy_mode=False,
               ofdm_train_bursts=1, ofdm_adaptive_train=False,
               ofdm_superframe=False, ofdm_train_gap_ms=30,
               ofdm_max_train_seconds=20.0,
               g2_waveform="sc_ftn", g2_bandwidth="2K7", g2_mcs=2,
               g2_adaptive_mcs=False, g2_tx_scale=1.0, g2_automatic=True,
               radio_backend="", radio_model="",
               audio_input=None, audio_output=None, ptt=None, ptt_turnaround_ms=0,
               **_unused):
    """Build the Guardian SC-FTN backend. Takes no ``vara``."""
    return OfdmVhfBackend(
        ofdm_profile=ofdm_profile,
        ofdm_mcs=ofdm_mcs,
        ofdm_tx_lead_ms=ofdm_tx_lead_ms,
        ofdm_tx_tail_ms=ofdm_tx_tail_ms,
        ofdm_max_retries=ofdm_max_retries,
        ofdm_adaptive_fec=ofdm_adaptive_fec,
        ofdm_modern_ldpc=ofdm_modern_ldpc,
        ofdm_fec=ofdm_fec,
        ofdm_adaptive_burst=ofdm_adaptive_burst,
        ofdm_burst_bytes=ofdm_burst_bytes,
        ofdm_min_burst_bytes=ofdm_min_burst_bytes,
        ofdm_max_burst_bytes=ofdm_max_burst_bytes,
        ofdm_arq_block_bytes=ofdm_arq_block_bytes,
        ofdm_timeout_multiplier=ofdm_timeout_multiplier,
        ofdm_legacy_mode=ofdm_legacy_mode,
        ofdm_train_bursts=ofdm_train_bursts,
        ofdm_adaptive_train=ofdm_adaptive_train,
        ofdm_superframe=ofdm_superframe,
        ofdm_train_gap_ms=ofdm_train_gap_ms,
        ofdm_max_train_seconds=ofdm_max_train_seconds,
        g2_waveform=g2_waveform,
        g2_bandwidth=g2_bandwidth,
        g2_mcs=g2_mcs,
        g2_adaptive_mcs=g2_adaptive_mcs,
        g2_tx_scale=g2_tx_scale,
        g2_automatic=g2_automatic,
        radio_backend=radio_backend,
        radio_model=radio_model,
        audio_input=audio_input,
        audio_output=audio_output,
        ptt=ptt,
        ptt_turnaround_ms=ptt_turnaround_ms,
        on_log=on_log,
        on_qsy=on_qsy,
        on_receive_qsy=on_receive_qsy,
        on_unqsy=on_unqsy,
        on_acquire=on_acquire,
        on_release=on_release,
    )


_BACKENDS = {"vara_p2p": _make_vara, "ofdm_vhf": _make_ofdm}


def make_backend(name: str = "vara_p2p", **deps):
    """Build a payload backend by config name.

    Every backend is handed the same dependency bag and takes what it needs, so
    Operations does not have to know which transport wants a VARA client and which
    wants a soundcard. Surplus keyword arguments -- an operator `prompt` callback
    from a release that had one, for instance -- are accepted and ignored so an
    older config still loads.

    An unknown name falls back to VARA P2P rather than raising: the alternative is
    a station that cannot move mail because of a bad string in a JSON file.
    """
    return _BACKENDS.get(name, _make_vara)(**deps)
