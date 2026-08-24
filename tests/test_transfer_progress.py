import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

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


def test_vara_send_progress_keeps_buffer_semantics() -> None:
    state = transfer_state(
        _snapshot(written=1000, queued=250, direction="send", bitrate=566),
        True,
    )
    assert state.active
    assert state.direction == "send"
    assert state.sent_bytes == 750
    assert state.total_bytes == 1000
    assert state.goodput_bps == 566


def test_vara_receive_progress_uses_wire_bytes() -> None:
    state = transfer_state(
        _snapshot(direction="receive", received=768, receive_total=1024, bitrate=1200),
        True,
    )
    assert state.active
    assert state.direction == "receive"
    assert state.sent_bytes == 768
    assert state.total_bytes == 1024
    assert state.fraction == 0.75


def test_receive_panel_is_visible_before_size_then_reports_progress() -> None:
    _application()
    panel = TransferPanel()
    panel.apply(transfer_state(_snapshot(direction="receive"), True))
    assert not panel.isHidden()
    assert "RECEIVING MESSAGE" in panel.title.text()
    assert "Waiting for incoming payload size" in panel.detail.text()

    panel.apply(transfer_state(
        _snapshot(direction="receive", received=128, receive_total=256, bitrate=566),
        True,
    ))
    assert "128 of 256 B received" in panel.detail.text()
    assert "Transfer speed: 566 bit/s" in panel.detail.text()
