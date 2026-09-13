"""Slow RF progress must reach both the payload and session watchdogs."""

import socket
from types import SimpleNamespace

import pytest

from guardian.payload.vara_p2p import VaraP2PBackend, encode_envelope
from guardian.session import LoopbackBus, Message, Orchestrator, SessionState
from guardian.session.orchestrator import session_transfer_hard_timeout_for
from guardian.vara.client import TransferResult, VaraClient
from test_payloads import FakeVara


@pytest.mark.parametrize("direction", ["send", "receive"])
@pytest.mark.parametrize("relay", [False, True])
def test_slow_vara_progress_survives_original_deadlines(monkeypatch, direction, relay):
    clock = [100.0]
    monkeypatch.setattr("guardian.vara.client.time.monotonic", lambda: clock[0])
    station = Orchestrator("OK7PS", LoopbackBus().endpoint("local"))
    payload = b"x" * 30_000
    message = Message(
        117, "OK2IPW", "OK1AAA" if relay else "OK7PS", "OK7PS",
        payload_bytes=payload if direction == "send" else None,
        direction="out" if direction == "send" else "in",
    )
    station.sessions[117] = message
    station.tick(clock[0])
    active = SessionState.TRANSFERRING if direction == "send" else SessionState.RECEIVING
    station._enter(message, active)
    envelope = bytearray(encode_envelope(117, payload))
    client = VaraClient()
    client.state.link_state = "CONNECTED"
    client.state.transfer_direction = direction

    def advance():
        clock[0] += 100
        station.tick(clock[0], control_available=False)
        assert message.state is active, message.error

    class SlowSocket:
        def settimeout(self, timeout):
            pass

        def recv(self, size):
            advance()
            part = bytes(envelope[:min(size, 1024)])
            del envelope[:len(part)]
            return part

    class SlowDrain:
        def is_set(self):
            return False

        def wait(self, seconds):
            advance()
            client.state.tx_buffer_bytes = max(0, client.state.tx_buffer_bytes - 1024)

    vara = FakeVara()
    if direction == "receive":
        client._data = SlowSocket()
        vara.read_exactly = client.read_exactly
    else:
        client.state.tx_buffer_bytes = len(envelope)
        client._buffer_nonzero.set()
        client._stop = SlowDrain()
        vara.wait_transfer_complete = client.wait_transfer_complete
    results = []
    backend = VaraP2PBackend(vara)
    getattr(backend, "_" + direction)(message, results.append)
    assert results == [True]
    assert clock[0] - 100 > 2500  # also exceeds the original size-based TX budget
    assert message.payload_progress_bytes >= 30_000
    if direction == "receive":
        assert message.payload_bytes == payload
        assert message.payload_wire_size == 30_014


@pytest.mark.parametrize("direction", ["send", "receive"])
def test_vara_stall_and_absolute_session_cap_still_fail(direction):
    station = Orchestrator("OK7PS", LoopbackBus().endpoint("local"))
    message = Message(118, "OK2IPW", "OK7PS", "OK7PS", direction=direction)
    station.sessions[118] = message
    active = SessionState.TRANSFERRING if direction == "send" else SessionState.RECEIVING
    station._enter(message, active)
    message.payload_progress_bytes = 12
    station.tick(100, control_available=False)
    station.tick(281, control_available=False)
    assert message.state is SessionState.FAILED
    assert "no progress" in message.error
    station._enter(message, active)
    message.payload_progress_bytes = 24
    station.tick(282 + session_transfer_hard_timeout_for(message), control_available=False)
    assert message.state is SessionState.FAILED
    assert "absolute safety" in message.error


def test_blocked_receive_cancels_without_consuming_the_next_transfer(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("guardian.vara.client.time.monotonic", lambda: clock[0])
    timeouts = []

    class QuietSocket:
        def settimeout(self, timeout):
            timeouts.append(timeout)

        def recv(self, size):
            clock[0] += 0.25
            raise socket.timeout()

    client = VaraClient()
    client._data = QuietSocket()

    def cancelled():
        if clock[0] >= 0.5:
            raise RuntimeError("payload cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        client.read_exactly(30_000, 2400, check_cancelled=cancelled)
    assert clock[0] == 0.5
    assert timeouts[-1] is None


def test_repeated_buffer_reports_do_not_extend_vara_drain(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("guardian.vara.client.time.monotonic", lambda: clock[0])
    client = VaraClient()
    client.state.link_state = "CONNECTED"
    client.state.tx_buffer_bytes = 30_000
    client._buffer_nonzero.set()
    client._stop = SimpleNamespace(
        is_set=lambda: False,
        wait=lambda seconds: clock.__setitem__(0, clock[0] + 1),
    )
    progress = []
    assert client.wait_transfer_complete(3, on_progress=progress.append) is TransferResult.TIMEOUT
    assert progress == []
    assert clock[0] == 3
