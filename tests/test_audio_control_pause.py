"""Control audio must leave the codec exclusively to the payload modem."""

from types import SimpleNamespace

import pytest

from guardian.modem.audio import AudioControlTransport
from guardian.protocol import ControlFrame, FrameType
from guardian.session import Orchestrator, SessionState


def _transport(monkeypatch):
    class Stream:
        def __init__(self, *, device, **kwargs):
            self.device = device

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    backend = SimpleNamespace(
        InputStream=Stream,
        query_devices=lambda index, kind: {"name": "Test USB codec"},
        check_input_settings=lambda **kwargs: None,
        check_output_settings=lambda **kwargs: None,
    )
    monkeypatch.setattr("guardian.modem.audio._import_sounddevice", lambda: backend)
    transport = AudioControlTransport(input_device=4, output_device=7)
    played = []
    monkeypatch.setattr("guardian.modem.audio.transmit_waveform",
                        lambda sd, samples, **kwargs: played.append(len(samples)))
    transport.start()
    return transport, played


def test_payload_suspension_holds_new_controls_and_cw_until_rx_resumes(monkeypatch):
    transport, played = _transport(monkeypatch)
    try:
        start = ControlFrame(FrameType.START_VARA, source="OK7PS", destination="OK2IPW")
        transport.send(start)
        assert transport.suspend(timeout=2.0)
        assert len(played) == 1  # Already admitted START is drained first.
        assert transport._stream is None

        relay = ControlFrame(FrameType.MULTIHOP_RREP, source="OK2JLD", destination="OK7PS")
        transport.send(relay)
        transport.send(relay)
        assert transport.send_morse_after_pending("OK7PS")
        assert transport.wait_tx_idle(timeout=0.01)
        assert len(played) == 1

        transport.start()
        assert transport.wait_tx_idle(timeout=2.0)
        assert len(played) == 3  # One held relay plus CW, after reopening RX.
        assert transport._stream is not None
    finally:
        transport.stop()


def test_failed_suspend_releases_held_work_and_keeps_rx(monkeypatch):
    transport, played = _transport(monkeypatch)
    original_wait = transport.wait_tx_idle
    relay = ControlFrame(FrameType.MULTIHOP_RREP, source="OK2JLD", destination="OK7PS")

    def timeout(timeout):
        transport.send(relay)  # Arrives while the drain barrier is active.
        assert played == []
        return False

    try:
        monkeypatch.setattr(transport, "wait_tx_idle", timeout)
        assert not transport.suspend(timeout=0.01)
        assert transport._stream is not None
        assert original_wait(timeout=2.0)
        assert len(played) == 1
    finally:
        transport.stop()


def test_user_stop_discards_held_controls_and_rejects_later_sends(monkeypatch):
    transport, played = _transport(monkeypatch)
    assert transport.suspend(timeout=2.0)
    frame = ControlFrame(FrameType.BEACON, source="OK7PS")
    transport.send(frame)
    transport.stop()
    transport.send(frame)
    assert not transport.send_morse_after_pending("OK7PS")
    transport.start()
    try:
        assert transport.wait_tx_idle(timeout=2.0)
        assert played == []
    finally:
        transport.stop()


@pytest.mark.parametrize("ending", ["cancel", "failure", "discovery-cancel", "remote-cancel"])
def test_finished_mail_never_replays_held_requests_but_preserves_receipts(
    monkeypatch, ending,
):
    transport, _played = _transport(monkeypatch)
    aired = []
    monkeypatch.setattr(transport, "_tx", aired.append)
    station = Orchestrator("OK7PS", transport, discovery_mode="assisted")
    cancelled = []

    def cancel_payload(message):
        cancelled.append(message.msg_id)
        # Completion can race a user cancellation or a session timeout.
        station._on_send_done(message, True)

    station.payload = SimpleNamespace(cancel=cancel_payload)
    try:
        assert transport.suspend(timeout=2.0)
        if ending == "discovery-cancel":
            message = station.send_message("OK2JLD", "queued", msg_id=901)
            assert message.discovery_query_id in station.discovery.pending
            assert any(value.type is FrameType.MULTIHOP_RREQ
                       for kind, value in transport._deferred_tx)
        else:
            message = station.send_message("OK2IPW", "queued", msg_id=901,
                                           next_hop="OK2IPW")
            station._enter(message, SessionState.TRANSFERRING)
            station._send(FrameType.START_VARA, message)
        receipt = ControlFrame(FrameType.RECEIVED, source="OK7PS", message_id=901)
        other = ControlFrame(FrameType.HAVE_MSG, source="OK7PS", message_id=902)
        transport.send(receipt)
        transport.send(other)

        if ending == "failure":
            station._fail(message, "payload made no progress")
            assert message.error == "payload made no progress"
            assert message.state is SessionState.FAILED
        elif ending == "remote-cancel":
            station._rx_cancel(ControlFrame(FrameType.CANCEL, source="OK2IPW",
                                           message_id=message.msg_id))
            assert message.state is SessionState.CANCELLED
        else:
            station.cancel(message.msg_id, notify=False)
            assert message.state is SessionState.CANCELLED
        assert cancelled == [901]
        assert message.payload_sent_at is None
        assert message.discovery_query_id not in station.discovery.pending

        transport.start()
        assert transport.wait_tx_idle(timeout=2.0)
        assert len(aired) == 2
        assert receipt in aired and other in aired
    finally:
        transport.stop()
