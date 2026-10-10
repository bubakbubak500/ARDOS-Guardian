import os
from types import SimpleNamespace

import pytest

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
    source: str = "",
    destination: str = "",
    via: str = "",
):
    return SimpleNamespace(vara=SimpleNamespace(
        data_bytes_written=written,
        tx_buffer_bytes=queued,
        transfer_direction=direction,
        rx_transfer_bytes=received,
        rx_transfer_total=receive_total,
        tx_bitrate_bps=bitrate,
        transfer_source=source,
        transfer_destination=destination,
        transfer_via=via,
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


def test_transfer_state_carries_origin_destination_and_immediate_peer() -> None:
    state = transfer_state(
        _snapshot(
            written=256,
            queued=128,
            direction="send",
            source="OK1AAA",
            destination="OK2BBB",
            via="OK3CCC",
        ),
        True,
    )

    assert (state.source, state.destination, state.via) == (
        "OK1AAA",
        "OK2BBB",
        "OK3CCC",
    )


def test_transfer_panel_marks_unknown_inbound_origin_but_keeps_peer() -> None:
    _application()
    panel = TransferPanel()
    panel.apply(
        transfer_state(
            _snapshot(
                direction="receive",
                received=32,
                receive_total=256,
                destination="OK2BBB",
                via="OK3CCC",
            ),
            True,
        )
    )

    assert "From unknown" in panel.detail.text()
    assert "To OK2BBB" in panel.detail.text()
    assert "@ OK3CCC" in panel.detail.text()


def test_transfer_panel_hides_redundant_direct_hop_marker() -> None:
    _application()
    panel = TransferPanel()
    panel.apply(
        transfer_state(
            _snapshot(
                written=256,
                queued=0,
                direction="send",
                source="OK1AAA",
                destination="OK2BBB",
                via="OK2BBB",
            ),
            True,
        )
    )

    assert "From OK1AAA" in panel.detail.text()
    assert "To OK2BBB" in panel.detail.text()
    assert "@ OK2BBB" not in panel.detail.text()


@pytest.mark.parametrize("direction,state_name", [
    ("send", "sending"), ("receive", "receiving"),
])
def test_ardop_progress_uses_own_body_counts_and_route(
    direction, state_name, monkeypatch
) -> None:
    from guardian import i18n

    monkeypatch.setattr(i18n, "_language", i18n.Language.ENGLISH)
    status = SimpleNamespace(
        state=state_name, direction=direction, total_bytes=400,
        progress_bytes=100, total_bytes_exact=True,
        transfer_source="OK1AAA", transfer_destination="OK2BBB",
        transfer_via="OK3CCC",
    )
    view = transfer_state(
        _snapshot(written=9000, queued=0, direction="receive",
                  received=8000, receive_total=9000, bitrate=9600,
                  source="OLD1", destination="OLD2", via="OLD3"),
        True, ardop_status=status, payload_backend="ardop",
    )
    assert view.active
    assert view.transport == "ardop"
    assert view.direction == direction
    assert (view.sent_bytes, view.total_bytes, view.fraction) == (100, 400, 0.25)
    assert view.total_bytes_exact
    assert view.goodput_bps is None
    assert (view.source, view.destination, view.via) == (
        "OK1AAA", "OK2BBB", "OK3CCC",
    )
    _application()
    panel = TransferPanel()
    try:
        panel.apply(view)
        assert "ARDOP" in panel.title.text()
        assert "VARA" not in panel.title.text()
        assert "100 of 400 B" in panel.detail.text()
        assert "@ OK3CCC" in panel.detail.text()
        assert "OLD" not in panel.detail.text()
    finally:
        panel.close()


def test_ardop_receive_waits_for_size_and_idle_clears_stale_vara() -> None:
    # The ARDOP branch must not even need a VARA snapshot for an active RX.
    status = SimpleNamespace(state="receiving", direction="receive",
                             total_bytes=0, progress_bytes=0,
                             total_bytes_exact=False)
    view = transfer_state(SimpleNamespace(), True,
                          ardop_status=status, payload_backend="ardop")
    assert view.active and view.direction == "receive"
    assert not view.total_bytes_exact
    _application()
    panel = TransferPanel()
    try:
        panel.apply(view)
        assert not panel.isHidden()
        idle = transfer_state(_snapshot(written=9000, queued=0), True,
                              payload_backend="ardop")
        assert not idle.active
        panel.apply(idle)
        assert panel.isHidden()
        assert panel.bar.fraction == 0.0
        assert not panel.detail.text()
    finally:
        panel.close()
