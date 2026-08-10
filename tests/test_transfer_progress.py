"""The shell's payload meter reports native OFDM state without breaking VARA."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from guardian.ofdm.metrics import OfdmStatus
from guardian.qt.transfer_progress import TransferPanel, transfer_state


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _snapshot(
    *,
    written: int = 0,
    queued=None,
    direction: str = "",
    received: int = 0,
    receive_total: int = 0,
    bitrate: int | None = None,
):
    return SimpleNamespace(vara=SimpleNamespace(
        data_bytes_written=written,
        tx_buffer_bytes=queued,
        transfer_direction=direction,
        rx_transfer_bytes=received,
        rx_transfer_total=receive_total,
        tx_bitrate_bps=bitrate,
    ))


def test_vara_progress_keeps_its_existing_buffer_semantics() -> None:
    state = transfer_state(
        _snapshot(written=1000, queued=250, direction="send", bitrate=566),
        True,
    )
    assert state.active
    assert state.transport == "vara"
    assert state.direction == "send"
    assert state.sent_bytes == 750
    assert state.total_bytes == 1000
    assert state.goodput_bps == 566


def test_vara_receive_progress_uses_wire_bytes_and_reported_speed() -> None:
    state = transfer_state(
        _snapshot(
            direction="receive",
            received=768,
            receive_total=1024,
            bitrate=1200,
        ),
        True,
    )
    assert state.active
    assert state.transport == "vara"
    assert state.direction == "receive"
    assert state.sent_bytes == 768
    assert state.total_bytes == 1024
    assert state.fraction == 0.75
    assert state.goodput_bps == 1200


def test_ofdm_progress_uses_acknowledged_bytes_and_live_profile() -> None:
    status = OfdmStatus(
        direction="send",
        fec="3/4", burst_bytes=4096, arq_block_bytes=512,
        tx_bytes=1536, total_bytes=2048, retries=1,
        retransmitted_bytes=512, last_burst_blocks=4,
        last_first_pass_ok=3, est_bitrate_bps=2180.0, goodput_bps=1760.0,
    )
    state = transfer_state(_snapshot(), True, status)
    assert state.active
    assert state.transport == "ofdm"
    assert state.direction == "send"
    assert state.sent_bytes == 1536
    assert state.fraction == 0.75
    assert (state.fec, state.burst_bytes, state.arq_block_bytes) == (
        "3/4", 4096, 512
    )
    assert state.retransmitted_bytes == 512
    assert state.goodput_bps == 1760.0


def test_ofdm_receive_progress_uses_received_bytes_and_speed() -> None:
    status = OfdmStatus(
        direction="receive",
        rx_bytes=3072,
        total_bytes=4096,
        goodput_bps=2120.0,
    )
    state = transfer_state(_snapshot(), True, status)
    assert state.active
    assert state.transport == "ofdm"
    assert state.direction == "receive"
    assert state.sent_bytes == 3072
    assert state.fraction == 0.75
    assert state.goodput_bps == 2120.0


def test_both_receivers_show_the_panel_before_the_total_is_known() -> None:
    vara = transfer_state(_snapshot(direction="receive"), True)
    ofdm = transfer_state(
        _snapshot(),
        True,
        OfdmStatus(direction="receive", state="synchronizing"),
    )
    assert vara.active and vara.direction == "receive"
    assert ofdm.active and ofdm.direction == "receive"

    _application()
    panel = TransferPanel()
    panel.apply(vara)
    assert not panel.isHidden()
    assert "RECEIVING MESSAGE" in panel.title.text()
    assert "Waiting for incoming payload size" in panel.detail.text()
    assert "Transfer speed: measuring" in panel.detail.text()


def test_ofdm_panel_renders_profile_retry_and_goodput_details() -> None:
    _application()
    panel = TransferPanel()
    status = OfdmStatus(
        direction="send",
        fec="5/6", burst_bytes=8192, arq_block_bytes=512,
        tx_bytes=4096, total_bytes=8192, retries=2,
        retransmitted_bytes=1024, last_burst_blocks=8,
        last_first_pass_ok=6, est_bitrate_bps=2440.0, goodput_bps=1995.0,
    )
    panel.apply(transfer_state(_snapshot(), True, status))
    text = panel.detail.text()
    assert not panel.isHidden()
    assert "SENDING MESSAGE" in panel.title.text()
    assert "Transfer speed: 1995 bit/s" in text
    assert "FEC 5/6" in text
    assert "8192 B" in text
    assert "6/8" in text
    assert "1024 B" in text
    assert "1995 bit/s" in text
    assert "2440 bit/s" not in text


def test_vara_send_panel_is_visible_with_send_title_and_speed() -> None:
    _application()
    panel = TransferPanel()
    state = transfer_state(
        _snapshot(
            written=1024,
            queued=256,
            direction="send",
            bitrate=1200,
        ),
        True,
    )
    panel.apply(state)
    assert not panel.isHidden()
    assert "SENDING MESSAGE" in panel.title.text()
    assert "VARA" in panel.title.text()
    assert "768 of 1024 B on the air" in panel.detail.text()
    assert "Transfer speed: 1200 bit/s" in panel.detail.text()


def test_vara_receive_panel_is_visible_with_receive_title_and_speed() -> None:
    _application()
    panel = TransferPanel()
    state = transfer_state(
        _snapshot(
            direction="receive",
            received=128,
            receive_total=256,
            bitrate=566,
        ),
        True,
    )
    panel.apply(state)
    assert not panel.isHidden()
    assert "RECEIVING MESSAGE" in panel.title.text()
    assert "VARA" in panel.title.text()
    assert "128 of 256 B received" in panel.detail.text()
    assert "Transfer speed: 566 bit/s" in panel.detail.text()


def test_ofdm_receive_panel_is_visible_with_receive_title_and_speed() -> None:
    _application()
    panel = TransferPanel()
    status = OfdmStatus(
        direction="receive",
        rx_bytes=2048,
        total_bytes=4096,
        goodput_bps=1800.0,
    )
    panel.apply(transfer_state(_snapshot(), True, status))
    assert not panel.isHidden()
    assert "RECEIVING MESSAGE" in panel.title.text()
    assert "OFDM VHF" in panel.title.text()
    assert "2048 of 4096 B received" in panel.detail.text()
    assert "Transfer speed: 1800 bit/s" in panel.detail.text()
