"""The shell's payload meter reports native OFDM state without breaking VARA."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from guardian.ofdm.metrics import OfdmStatus
from guardian.qt.transfer_progress import TransferPanel, transfer_state


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _snapshot(*, written: int = 0, queued=None):
    return SimpleNamespace(vara=SimpleNamespace(
        data_bytes_written=written, tx_buffer_bytes=queued
    ))


def test_vara_progress_keeps_its_existing_buffer_semantics() -> None:
    state = transfer_state(_snapshot(written=1000, queued=250), True)
    assert state.active
    assert state.transport == "vara"
    assert state.sent_bytes == 750
    assert state.total_bytes == 1000


def test_ofdm_progress_uses_acknowledged_bytes_and_live_profile() -> None:
    status = OfdmStatus(
        fec="3/4", burst_bytes=4096, arq_block_bytes=512,
        tx_bytes=1536, total_bytes=2048, retries=1,
        retransmitted_bytes=512, last_burst_blocks=4,
        last_first_pass_ok=3, est_bitrate_bps=2180.0, goodput_bps=1760.0,
    )
    state = transfer_state(_snapshot(), True, status)
    assert state.active
    assert state.transport == "ofdm"
    assert state.sent_bytes == 1536
    assert state.fraction == 0.75
    assert (state.fec, state.burst_bytes, state.arq_block_bytes) == (
        "3/4", 4096, 512
    )
    assert state.retransmitted_bytes == 512
    assert state.goodput_bps == 1760.0


def test_ofdm_panel_renders_profile_retry_and_goodput_details() -> None:
    _application()
    panel = TransferPanel()
    status = OfdmStatus(
        fec="5/6", burst_bytes=8192, arq_block_bytes=512,
        tx_bytes=4096, total_bytes=8192, retries=2,
        retransmitted_bytes=1024, last_burst_blocks=8,
        last_first_pass_ok=6, est_bitrate_bps=2440.0, goodput_bps=1995.0,
    )
    panel.apply(transfer_state(_snapshot(), True, status))
    text = panel.detail.text()
    assert "FEC 5/6" in text
    assert "8192 B" in text
    assert "6/8" in text
    assert "1024 B" in text
    assert "1995 bit/s" in text
    assert "2440 bit/s" not in text
