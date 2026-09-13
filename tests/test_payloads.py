import socket
import struct
import time
import io
import json
import zipfile
import threading
from types import SimpleNamespace

import pytest

from guardian.payload.vara_p2p import (
    DISCONNECT_TIMEOUT,
    MIN_WIRE_SIZE,
    TransferContext,
    TransferIdentity,
    TRANSFER_TIMEOUT,
    VaraP2PBackend,
    airtime_for,
    disconnect_timeout_for,
    encode_envelope,
    transfer_identity_from_bundle,
    transfer_timeout_for,
)
from guardian.message import Attachment, MailMessage
from guardian.protocol import crc16
from guardian.session import Message
from guardian.vara.client import TransferResult, VaraClient


class FakeVara:
    def __init__(self, incoming: bytes = b"") -> None:
        self.connected = True
        self.incoming = bytearray(incoming)
        self.commands = []
        self.written = b""
        self.transfer_result = TransferResult.DRAINED
        self.transfer_timeout = None
        self.closing_timeout = None
        self.link_closes = True
        self.state = SimpleNamespace(
            tx_buffer_bytes=None,
            data_socket_generation=1,
            data_socket_reopens=0,
            tx_bitrate_bps=None,
            data_bytes_read=0,
            ptt_keyings=0,
            transport_lost=False,
        )

    def connect_to(self, callsign: str) -> None:
        self.commands.append(("connect", callsign))

    def listen(self, enabled: bool) -> None:
        self.commands.append(("listen", enabled))

    def wait_link(
        self,
        state: str,
        timeout: float,
        *,
        ptt_grace: float = 0.0,
        max_wait: float | None = None,
    ) -> bool:
        if state == "DISCONNECTED":
            self.closing_timeout = timeout
            return self.link_closes
        return state == "CONNECTED"

    def abort(self) -> None:
        self.commands.append(("abort",))

    def disconnect_link(self) -> None:
        self.commands.append(("disconnect",))

    def prepare_data_transfer(self) -> None:
        self.commands.append(("prepare",))

    def wait_data_ready(self) -> None:
        self.commands.append(("data-ready",))

    def wait_transfer_complete(self, timeout: float, **kwargs) -> TransferResult:
        self.commands.append(("wait-transfer",))
        self.transfer_timeout = timeout
        return self.transfer_result

    def write_data(self, data: bytes) -> None:
        self.written += data
        self.state.tx_buffer_bytes = len(self.written)

    def finish_data_write(self) -> None:
        self.commands.append(("finish-write",))

    def read_exactly(self, size: int, timeout: float, **kwargs) -> bytes:
        result = bytes(self.incoming[:size])
        del self.incoming[:size]
        if len(result) != size:
            raise EOFError("test stream ended early")
        return result


class ContextVara(FakeVara):
    """Fake VARA that records context writes and mirrors client state."""

    def __init__(self, incoming: bytes = b"") -> None:
        super().__init__(incoming)
        self.contexts = []

    def set_transfer_context(
        self,
        source: str,
        destination: str,
        via: str,
    ) -> None:
        self.contexts.append(TransferContext(source, destination, via))
        self.state.transfer_source = source
        self.state.transfer_destination = destination
        self.state.transfer_via = via


@pytest.mark.parametrize("direction", ["send", "receive"])
def test_queued_vara_worker_rechecks_deferred_handoff_after_transfer_lock(direction):
    """A worker queued behind a transfer must wait for that transfer's RF tail."""
    passed_initial_wait = threading.Event()
    acquired_audio = threading.Event()

    class ObservedReady(threading.Event):
        def wait(self, timeout=None):
            result = super().wait(timeout)
            passed_initial_wait.set()
            return result

    vara = FakeVara(encode_envelope(902, b"mail"))
    backend = VaraP2PBackend(vara, on_acquire=acquired_audio.set)
    backend._handoff_ready = ObservedReady()
    backend._handoff_ready.set()
    message = Message(902, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"mail")
    result = []
    worker = threading.Thread(
        target=getattr(backend, "_" + direction), args=(message, result.append),
        daemon=True,
    )
    backend._transfer_lock.acquire()
    try:
        worker.start()
        assert passed_initial_wait.wait(1)
        # The preceding transfer now finishes its payload but must retain RF.
        backend._handoff_ready.clear()
    finally:
        backend._transfer_lock.release()
    try:
        assert not acquired_audio.wait(0.1)
        assert result == []
    finally:
        backend._handoff_ready.set()
        worker.join(2)
        backend.shutdown()
    assert not worker.is_alive()
    assert acquired_audio.is_set()
    assert result == [True]


@pytest.mark.parametrize("direction", ["send", "receive"])
def test_completed_vara_transfer_retains_success_after_delayed_rf_handoff(direction):
    vara = FakeVara(encode_envelope(903, b"mail"))
    vara.idle = False
    vara.wait_radio_idle = lambda timeout: vara.idle
    continuations = []
    result = []
    backend = VaraP2PBackend(
        vara, on_acquire=lambda: None, on_handoff_failed=continuations.append,
    )
    message = Message(903, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"mail")
    getattr(backend, "_" + direction)(message, result.append)
    assert result == []
    assert len(continuations) == 1
    vara.idle = True
    assert continuations[0]()
    assert result == [True]


@pytest.mark.parametrize("compression", ["zip", "xz"])
def test_native_vara_tcp_pair_writes_bundle_after_connected_break(compression):
    """Exercise real TCP streams and notification parsing, without a radio."""
    mail = MailMessage(
        msg_id=904, source="OK7PS", final_dest="OK2IPW",
        subject="direct TCP regression", body="A message ready for VARA.",
    )
    standard = mail.to_bundle()
    payload = standard if compression == "zip" else mail.to_guardian_bundle(standard).data
    expected = encode_envelope(mail.msg_id, payload)
    command_app, command_modem = socket.socketpair()
    data_app, data_modem = socket.socketpair()
    for endpoint in (command_modem, data_modem):
        endpoint.settimeout(4)
    vara = VaraClient()
    vara._cmd, vara._data = command_app, data_app
    vara.state.cmd_connected = vara.state.data_connected = True
    vara.state.mycall = "OK7PS"
    received = bytearray()
    errors = []

    def read_command():
        line = bytearray()
        while not line.endswith(b"\r"):
            chunk = command_modem.recv(1)
            if not chunk:
                raise EOFError("application closed command socket")
            line.extend(chunk)
        return bytes(line)

    def native_modem():
        try:
            assert read_command() == b"CONNECT OK7PS OK2IPW\r"
            command_modem.sendall(b"CONNECTED OK7PS OK2IPW\rBREAK\r")
            while len(received) < len(expected):
                chunk = data_modem.recv(len(expected) - len(received))
                if not chunk:
                    raise EOFError("application closed data socket before payload")
                received.extend(chunk)
            command_modem.sendall(f"BUFFER {len(received)}\rBUFFER 0\r".encode())
            assert read_command() == b"DISCONNECT\r"
            command_modem.sendall(b"DISCONNECTED\r")
        except Exception as exc:
            errors.append(exc)

    reader = threading.Thread(target=vara._reader, args=(command_app,), daemon=True)
    modem = threading.Thread(target=native_modem, daemon=True)
    result = []
    backend = VaraP2PBackend(vara, on_acquire=lambda: None)
    reader.start()
    modem.start()
    try:
        backend._send(
            Message(904, "OK7PS", "OK2IPW", "OK2IPW", payload_bytes=payload),
            result.append,
        )
        modem.join(4)
        assert not modem.is_alive()
        assert errors == []
        assert bytes(received) == expected
        assert vara.state.data_bytes_written == len(expected)
        assert result == [True]
    finally:
        backend.shutdown()
        vara.disconnect()
        command_modem.close()
        data_modem.close()
        reader.join(2)
        modem.join(2)


@pytest.mark.parametrize("cancel_at", ["ready", "drain", "close"])
def test_cancel_active_vara_aborts_link_and_never_reports_success(cancel_at):
    message = Message(905, "OK7PS", "OK2IPW", "OK2IPW", payload_bytes=b"mail")

    class CancelVara(FakeVara):
        def wait_data_ready(self):
            if cancel_at == "ready":
                backend.cancel(message)

        def wait_transfer_complete(self, timeout, **kwargs):
            if cancel_at == "drain":
                backend.cancel(message)
            return TransferResult.NO_BUFFER_REPORTS

        def wait_link(self, state, timeout, **kwargs):
            if state == "DISCONNECTED" and cancel_at == "close":
                backend.cancel(message)
            return super().wait_link(state, timeout, **kwargs)

    vara = CancelVara()
    result = []
    backend = VaraP2PBackend(vara)
    backend._send(message, result.append)
    assert ("abort",) in vara.commands
    assert result == [False]
    if cancel_at == "ready":
        assert vara.written == b""


def test_cancel_queued_vara_does_not_abort_another_active_message():
    queued = Message(906, "OK7PS", "OK2IPW", "OK2IPW", payload_bytes=b"later")
    active = Message(907, "OK7PS", "OK2IPW", "OK2IPW", payload_bytes=b"now")

    class CancelOtherVara(FakeVara):
        def wait_data_ready(self):
            backend.cancel(queued)

    vara = CancelOtherVara()
    result = []
    backend = VaraP2PBackend(vara)
    backend._send(active, result.append)
    assert result == [True]
    assert ("abort",) not in vara.commands
    first_payload = vara.written
    backend._send(queued, result.append)
    assert result == [True, False]
    assert vara.written == first_payload


def test_cancel_vara_while_waiting_handoff_cannot_report_delayed_success():
    vara = FakeVara()
    vara.idle = False
    vara.wait_radio_idle = lambda timeout: vara.idle
    continuations, result = [], []
    backend = VaraP2PBackend(
        vara, on_acquire=lambda: None, on_handoff_failed=continuations.append,
    )
    message = Message(908, "OK7PS", "OK2IPW", "OK2IPW", payload_bytes=b"mail")
    backend._send(message, result.append)
    backend.cancel(message)
    vara.idle = True
    assert continuations[0]()
    assert result == [False]


def test_vara_envelope_contains_id_payload_and_crc() -> None:
    envelope = encode_envelope(0x12345678, b"hello")
    magic, msg_id, length = struct.unpack(">4sII", envelope[:12])
    crc_offset = 12 + length
    crc = struct.unpack(">H", envelope[crc_offset : crc_offset + 2])[0]

    assert magic == b"GPLD"
    assert msg_id == 0x12345678
    assert length == 5
    assert envelope[12:crc_offset] == b"hello"
    assert crc == crc16(envelope[:crc_offset])
    assert len(envelope) == MIN_WIRE_SIZE
    assert not envelope[crc_offset + 2 :].strip(b"\0")


def test_transfer_identity_reads_only_bounded_manifest_from_both_bundle_codecs() -> None:
    mail = MailMessage(
        msg_id=701,
        source="ok1aaa",
        final_dest="ok2bbb",
        subject="status",
        body="hello",
        attachments=[Attachment("large.bin", b"x" * 50_000)],
        hops=["OK3CCC"],
    )
    standard = mail.to_bundle()
    guardian = mail.to_guardian_bundle(standard).data

    for bundle in (standard, guardian):
        identity = transfer_identity_from_bundle(bundle)
        assert identity.source == "OK1AAA"
        assert identity.destination == "OK2BBB"


def test_transfer_identity_rejects_oversized_or_invalid_manifest_fields() -> None:
    manifest = {
        "source": "OK1AAA",
        "final_dest": "OK2BBB",
        "hops": ["OK3CCC"],
    }
    oversized = json.dumps({"source": "OK1AAA", "padding": "x" * 20_000})
    bad_source = json.dumps({**manifest, "source": "not a callsign"})

    def bundle_for(text: str) -> bytes:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", text)
            zf.writestr("body.txt", "not read")
        return stream.getvalue()

    assert transfer_identity_from_bundle(bundle_for(oversized)) == TransferIdentity()
    identity = transfer_identity_from_bundle(bundle_for(bad_source))
    assert identity.source == ""
    assert identity.destination == "OK2BBB"

    ssid = transfer_identity_from_bundle(
        bundle_for(json.dumps({"source": "ok1aaa-1", "final_dest": "ok2bbb-2"}))
    )
    assert (ssid.source, ssid.destination) == ("OK1AAA-1", "OK2BBB-2")


def test_vara_send_publishes_manifest_identity_then_clears_it() -> None:
    vara = ContextVara()
    contexts = vara.contexts
    vara.state.mycall = "OK7PS"
    bundle = MailMessage(
        msg_id=702,
        source="OK1AAA",
        final_dest="OK2BBB",
    ).to_bundle()
    VaraP2PBackend(vara)._send(
        Message(702, "OK7PS", "OK2BBB", "OK3CCC", payload_bytes=bundle),
        lambda _ok: None,
    )

    assert contexts[0] == TransferContext("OK1AAA", "OK2BBB", "OK3CCC")
    assert contexts[-1] == TransferContext()


def test_vara_receive_shows_immediate_peer_before_manifest_origin_then_clears() -> None:
    bundle = MailMessage(
        msg_id=703,
        source="OK1AAA",
        final_dest="OK2BBB",
    ).to_bundle()
    vara = ContextVara(encode_envelope(703, bundle))
    contexts = vara.contexts
    VaraP2PBackend(vara)._receive(
        Message(703, "OK3CCC", "OK2BBB", "OK7PS", direction="in"),
        lambda _ok: None,
    )

    assert contexts[0] == TransferContext("", "OK2BBB", "OK3CCC")
    assert contexts[1] == TransferContext("OK1AAA", "OK2BBB", "OK3CCC")
    assert contexts[-1] == TransferContext()


def test_transfer_identity_never_opens_body_or_attachment_entries(monkeypatch) -> None:
    bundle = MailMessage(
        msg_id=704,
        source="OK1AAA",
        final_dest="OK2BBB",
        attachments=[Attachment("large.bin", b"x" * 100_000)],
    ).to_bundle()
    opened = []
    original_open = zipfile.ZipFile.open

    def spy_open(self, name, mode="r", pwd=None, *, force_zip64=False):
        opened.append(getattr(name, "filename", name))
        return original_open(
            self,
            name,
            mode,
            pwd,
            force_zip64=force_zip64,
        )

    monkeypatch.setattr(zipfile.ZipFile, "open", spy_open)
    identity = transfer_identity_from_bundle(bundle)

    assert identity == TransferIdentity("OK1AAA", "OK2BBB")
    assert opened == ["manifest.json"]


def test_failed_send_clears_transfer_context() -> None:
    vara = ContextVara()
    contexts = vara.contexts
    vara.state.mycall = "OK7PS"
    vara.transfer_result = TransferResult.PEER_CLOSED_EARLY
    result = []
    VaraP2PBackend(vara)._send(
        Message(
            705,
            "OK7PS",
            "OK2BBB",
            "OK3CCC",
            payload_bytes=MailMessage(
                msg_id=705,
                source="OK1AAA",
                final_dest="OK2BBB",
            ).to_bundle(),
        ),
        result.append,
    )

    assert result == [False]
    assert contexts[0] == TransferContext("OK1AAA", "OK2BBB", "OK3CCC")
    assert contexts[-1] == TransferContext()
    assert (
        vara.state.transfer_source,
        vara.state.transfer_destination,
        vara.state.transfer_via,
    ) == ("", "", "")


def test_failed_receive_clears_immediate_peer_context() -> None:
    vara = ContextVara(b"corrupt")
    contexts = vara.contexts
    result = []
    VaraP2PBackend(vara)._receive(
        Message(706, "OK3CCC", "OK2BBB", "OK7PS", direction="in"),
        result.append,
    )

    assert result == [False]
    assert contexts[0] == TransferContext("", "OK2BBB", "OK3CCC")
    assert contexts[-1] == TransferContext()
    assert (
        vara.state.transfer_source,
        vara.state.transfer_destination,
        vara.state.transfer_via,
    ) == ("", "", "")


def test_new_transfer_replaces_previous_identity_without_stale_route() -> None:
    vara = ContextVara()
    contexts = vara.contexts
    backend = VaraP2PBackend(vara)
    first = MailMessage(
        msg_id=707,
        source="OK1AAA",
        final_dest="OK2BBB",
    ).to_bundle()
    second = MailMessage(
        msg_id=708,
        source="OK4DDD",
        final_dest="OK5EEE",
    ).to_bundle()
    backend._send(
        Message(707, "OK7PS", "OK2BBB", "OK3CCC", payload_bytes=first),
        lambda _ok: None,
    )
    backend._send(
        Message(708, "OK7PS", "OK5EEE", "OK6FFF", payload_bytes=second),
        lambda _ok: None,
    )

    assert contexts == [
        TransferContext("OK1AAA", "OK2BBB", "OK3CCC"),
        TransferContext(),
        TransferContext("OK4DDD", "OK5EEE", "OK6FFF"),
        TransferContext(),
    ]
    assert (
        vara.state.transfer_source,
        vara.state.transfer_destination,
        vara.state.transfer_via,
    ) == ("", "", "")


def test_corrupt_manifest_does_not_abort_send_or_claim_local_origin() -> None:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", "{this is not json")
        zf.writestr("body.txt", "payload")
    vara = ContextVara()
    contexts = vara.contexts
    vara.state.mycall = "OK7PS"
    result = []
    VaraP2PBackend(vara)._send(
        Message(
            709,
            "OK7PS",
            "OK2BBB",
            "OK3CCC",
            payload_bytes=stream.getvalue(),
        ),
        result.append,
    )

    assert result == [True]
    assert contexts[0] == TransferContext("", "OK2BBB", "OK3CCC")
    assert contexts[-1] == TransferContext()
    assert vara.written


def test_unreadable_bundle_with_local_session_source_stays_unknown() -> None:
    vara = ContextVara()
    contexts = vara.contexts
    vara.state.mycall = "OK7PS"
    result = []
    VaraP2PBackend(vara)._send(
        Message(
            710,
            "OK7PS",
            "OK2BBB",
            "OK3CCC",
            payload_bytes=b"this is not a ZIP bundle",
        ),
        result.append,
    )

    assert result == [True]
    assert contexts[0] == TransferContext("", "OK2BBB", "OK3CCC")
    assert contexts[-1] == TransferContext()


def test_vara_buffer_notification_updates_transmit_queue_telemetry() -> None:
    vara = VaraClient()
    vara.prepare_data_transfer()

    assert vara.state.tx_buffer_bytes is None
    vara._handle_notification("BUFFER 411")

    assert vara.state.tx_buffer_bytes == 411


def test_vara_transfer_wait_requires_nonzero_buffer_before_drain() -> None:
    vara = VaraClient()
    vara.state.link_state = "CONNECTED"
    vara.prepare_data_transfer()
    vara._handle_notification("BUFFER 0")

    assert (
        vara.wait_transfer_complete(timeout=0, ingest_timeout=0)
        is TransferResult.NO_BUFFER_REPORTS
    )

    vara._handle_notification("BUFFER 411")
    vara._handle_notification("BUFFER 0")

    assert (
        vara.wait_transfer_complete(timeout=0)
        is TransferResult.DRAINED
    )


def test_vara_transfer_wait_rejects_peer_close_before_buffer_drain() -> None:
    vara = VaraClient()
    vara.state.link_state = "CONNECTED"
    vara.prepare_data_transfer()
    vara._handle_notification("BUFFER 411")
    vara.state.link_state = "DISCONNECTED"

    assert (
        vara.wait_transfer_complete(timeout=0)
        is TransferResult.PEER_CLOSED_EARLY
    )


def test_vara_read_exactly_restores_blocking_data_socket() -> None:
    class FakeDataSocket:
        def __init__(self) -> None:
            self.timeouts = []

        def settimeout(self, value) -> None:
            self.timeouts.append(value)

        def recv(self, size: int) -> bytes:
            return b"payload"[:size]

    data = FakeDataSocket()
    vara = VaraClient()
    vara._data = data

    assert vara.read_exactly(7, timeout=3.0) == b"payload"
    assert data.timeouts == [0.25, None]


def test_vara_reconnects_the_complete_tcp_pair_when_existing_state_is_dead(
    monkeypatch,
) -> None:
    class FakeSocket:
        def __init__(self) -> None:
            self.closed = False
            self.timeout = "unchanged"

        def shutdown(self, how) -> None:
            pass

        def close(self) -> None:
            self.closed = True

        def settimeout(self, value) -> None:
            self.timeout = value

        def setsockopt(self, level, option, value) -> None:
            pass

    old_cmd = FakeSocket()
    old_data = FakeSocket()
    fresh_cmd = FakeSocket()
    fresh_data = FakeSocket()
    sockets = iter((fresh_cmd, fresh_data))
    vara = VaraClient(cmd_port=8300, data_port=8301)
    vara._cmd = old_cmd
    vara._data = old_data
    vara.state.cmd_connected = False
    vara.state.data_connected = True
    monkeypatch.setattr(
        "guardian.vara.client.socket.create_connection",
        lambda address, timeout: next(sockets),
    )
    monkeypatch.setattr(
        "guardian.vara.client.threading.Thread.start", lambda self: None
    )

    vara.connect()

    assert old_cmd.closed
    assert old_data.closed
    assert vara._cmd is fresh_cmd
    assert vara._data is fresh_data
    assert fresh_cmd.timeout is None
    assert fresh_data.timeout is None
    assert vara.state.cmd_connected
    assert vara.state.data_connected


def test_vara_send_and_receive_preserve_payload_bytes() -> None:
    outgoing = FakeVara()
    send_result = []
    message = Message(12, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"bundle")
    backend = VaraP2PBackend(outgoing)

    backend._send(message, send_result.append)

    assert send_result == [True]
    assert outgoing.commands == [
        ("connect", "OK1AAA"),
        ("data-ready",),
        ("prepare",),
        ("wait-transfer",),
        ("disconnect",),
    ]
    assert outgoing.written == encode_envelope(12, b"bundle")

    incoming = FakeVara(outgoing.written)
    receive_result = []
    received = Message(12, "OK7PS", "OK1AAA", "OK1AAA")

    VaraP2PBackend(incoming)._receive(received, receive_result.append)

    assert receive_result == [True]
    assert incoming.commands == []
    assert received.payload_bytes == b"bundle"


class BufferedVara(FakeVara):
    """A data socket with leftovers from an earlier session still buffered."""

    def __init__(self, stale: bytes, fresh: bytes) -> None:
        super().__init__(stale + fresh)
        self._stale_length = len(stale)

    def drain_stale_data(self) -> int:
        dropped = min(self._stale_length, len(self.incoming))
        del self.incoming[:dropped]
        self._stale_length = 0
        return dropped


def test_receive_discards_a_previous_sessions_envelope_first() -> None:
    # The field fault: every message arrived one behind, because the
    # persistent 8301 socket still held the envelope of an earlier failed or
    # unclaimed exchange and read_exactly() served that one first.
    old = encode_envelope(11, b"yesterday's message")
    fresh = encode_envelope(12, b"today's message")
    logs: list[str] = []
    vara = BufferedVara(old, fresh)
    received = Message(12, "OK7PS", "OK1AAA", "OK1AAA")
    result: list[bool] = []

    VaraP2PBackend(vara, on_log=logs.append)._receive(received, result.append)

    assert result == [True]
    assert received.payload_bytes == b"today's message"
    assert any("stale" in line for line in logs)

    # And the regression this guards against: without the drain, the same
    # buffer state delivers the previous session's payload.
    undrained = FakeVara(bytes(old + fresh))
    behind = Message(12, "OK7PS", "OK1AAA", "OK1AAA")
    VaraP2PBackend(undrained)._receive(behind, result.append)
    assert behind.payload_bytes == b"yesterday's message"


def test_client_drain_discards_only_what_is_already_buffered() -> None:
    ours, varas = socket.socketpair()
    client = VaraClient()
    client._data = ours
    try:
        varas.sendall(b"leftover from a dead session")
        time.sleep(0.05)
        assert client.drain_stale_data() == 28

        varas.sendall(encode_envelope(5, b"fresh"))
        time.sleep(0.05)
        head = client.read_exactly(12, timeout=2.0)
        magic, msg_id, length = struct.unpack(">4sII", head)
        assert (magic, msg_id, length) == (b"GPLD", 5, 5)
        assert client.read_exactly(length, timeout=2.0) == b"fresh"
    finally:
        client._data = None
        ours.close()
        varas.close()


def test_vara_qsy_happens_after_control_handoff_and_restores_before_release() -> None:
    events = []
    backend = VaraP2PBackend(
        FakeVara(),
        on_qsy=lambda message: events.append(("qsy", message.next_hop)),
        on_acquire=lambda: events.append("acquire"),
        on_release=lambda: events.append("release"),
        on_unqsy=lambda: events.append("restore"),
    )

    backend._send(
        Message(14, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"x"),
        lambda ok: events.append(("done", ok)),
    )

    assert events[:2] == ["acquire", ("qsy", "OK1AAA")]
    assert events[-3:] == ["restore", "release", ("done", True)]


def test_vara_receive_uses_the_agreed_channel_and_returns_before_confirmation() -> None:
    events = []
    incoming = FakeVara(encode_envelope(141, b"x"))
    backend = VaraP2PBackend(
        incoming,
        on_receive_qsy=lambda message: events.append(("qsy", message.source)),
        on_acquire=lambda: events.append("acquire"),
        on_unqsy=lambda: events.append("restore"),
        on_release=lambda: events.append("release"),
    )

    backend._receive(
        Message(141, "OK7PS", "OK1AAA", "OK1AAA"),
        lambda ok: events.append(("done", ok)),
    )

    assert events == [
        "acquire",
        ("qsy", "OK7PS"),
        "restore",
        "release",
        ("done", True),
    ]


def test_failed_working_qsy_never_starts_vara() -> None:
    vara = FakeVara()
    events = []
    backend = VaraP2PBackend(
        vara,
        on_qsy=lambda _message: False,
        on_acquire=lambda: events.append("acquire"),
        on_unqsy=lambda: events.append("restore"),
        on_release=lambda: events.append("release"),
    )

    backend._send(
        Message(142, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"x"),
        lambda ok: events.append(("done", ok)),
    )

    assert events == ["acquire", "restore", "release", ("done", False)]
    assert not [command for command in vara.commands if command[0] == "connect"]


def test_vara_send_keeps_codec_until_rf_transfer_finishes() -> None:
    events = []

    class OrderedVara(FakeVara):
        def wait_link(self, state: str, timeout: float, **kwargs) -> bool:
            if state == "DISCONNECTED":
                events.append("rf-finished")
            return super().wait_link(state, timeout, **kwargs)

    backend = VaraP2PBackend(
        OrderedVara(),
        on_acquire=lambda: events.append("acquire"),
        on_release=lambda: events.append("release"),
    )
    backend._send(
        Message(16, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"x"),
        lambda ok: events.append(("done", ok)),
    )

    assert events == ["acquire", "rf-finished", "release", ("done", True)]


def test_vara_disconnect_follows_data_handoff_barrier() -> None:
    vara = FakeVara()
    result = []

    VaraP2PBackend(vara)._send(
        Message(18, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"x"),
        result.append,
    )

    assert result == [True]
    assert vara.commands.index(("wait-transfer",)) < vara.commands.index(
        ("disconnect",)
    )
    assert ("finish-write",) not in vara.commands


def test_short_messages_no_longer_pay_for_a_kilobyte_of_padding() -> None:
    # The 1024-byte floor was ~14 s of airtime at 566 bps on every short
    # operational message, and it was chosen while transfers were failing for
    # an unrelated reason.
    assert MIN_WIRE_SIZE == 256
    assert airtime_for(MIN_WIRE_SIZE, 566) < 8.0

    envelope = encode_envelope(1, b"QRV")
    assert len(envelope) == MIN_WIRE_SIZE

    # Anything past the floor is never padded, so attachments are unaffected.
    big = encode_envelope(2, b"z" * 5000)
    assert len(big) == 12 + 5000 + 2


def test_slow_unregistered_link_gets_more_than_the_flat_disconnect_budget() -> None:
    # Anything of real size on VARA FM's unregistered 566 bps rate outruns the
    # old flat 30 s disconnect budget, which aborted transfers while VARA was
    # still transmitting them. A 4 KB block is ~2 minutes there.
    assert airtime_for(4096, 566) > DISCONNECT_TIMEOUT
    assert disconnect_timeout_for(4096, 566) > 300.0
    # A fast registered link must not be slowed down to that budget.
    assert disconnect_timeout_for(4096, 25_000) == DISCONNECT_TIMEOUT
    # A short message stays inside the flat budget, as it always did.
    assert disconnect_timeout_for(MIN_WIRE_SIZE, 566) == DISCONNECT_TIMEOUT


def test_degraded_send_budgets_disconnect_from_the_reported_bitrate() -> None:
    vara = FakeVara()
    vara.transfer_result = TransferResult.NO_BUFFER_REPORTS
    vara.state.tx_bitrate_bps = 566
    logs = []
    result = []

    payload = b"x" * 4000
    VaraP2PBackend(vara, on_log=logs.append)._send(
        Message(30, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=payload),
        result.append,
    )

    assert result == [True]
    wire = len(encode_envelope(30, payload))
    assert vara.closing_timeout == disconnect_timeout_for(wire, 566)
    assert vara.closing_timeout > DISCONNECT_TIMEOUT
    assert any("566 bps" in line for line in logs)


def test_wait_link_holds_the_session_while_vara_keeps_keying() -> None:
    vara = VaraClient()
    vara.state.link_state = "CONNECTED"
    vara._handle_notification("PTT ON")

    # Transmitting: the elapsed timeout must not be treated as a failure yet.
    assert vara.ptt_quiet_for() == 0.0
    assert vara.wait_link("DISCONNECTED", 0.01, ptt_grace=0.05, max_wait=0.2) is False

    # Quiet modem: the same wait gives up promptly instead of hanging.
    vara._handle_notification("PTT OFF")
    vara._last_ptt_activity -= 60.0
    assert vara.ptt_quiet_for() > 10.0
    assert vara.wait_link("DISCONNECTED", 0.01, ptt_grace=0.05) is False


def test_send_never_toggles_listen_around_a_connection() -> None:
    # VARA's native command reference: LISTEN ON and LISTEN OFF each "will
    # cause a disconnection if it is received in the middle of a VARA
    # connection".  The documented outbound flow is MYCALL, LISTEN ON, CONNECT.
    vara = FakeVara()

    VaraP2PBackend(vara)._send(
        Message(34, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"x"),
        lambda _ok: None,
    )

    assert not [command for command in vara.commands if command[0] == "listen"]
    assert ("connect", "OK1AAA") in vara.commands


def test_vara_rejection_is_surfaced_instead_of_silently_ignored() -> None:
    vara = VaraClient()
    notes = []
    vara.on_notification = notes.append
    vara._last_command = "PUBLIC ON"

    vara._handle_notification("WRONG")

    assert vara.state.rejected_commands == 1
    assert any("PUBLIC ON" in note for note in notes)


def test_lost_vara_tcp_session_is_not_reported_as_a_closed_rf_link() -> None:
    # Killing VARA drops the TCP pair, which forces link_state to
    # DISCONNECTED -- the same value a graceful RF close produces.  Waiting
    # for "DISCONNECTED" must not accept that as a completed transfer.
    vara = VaraClient()
    vara.state.link_state = "DISCONNECTED"
    vara.state.transport_lost = True

    assert vara.wait_link("DISCONNECTED", 0.05) is False


def test_degraded_send_reports_a_killed_vara_as_an_unconfirmed_payload() -> None:
    vara = FakeVara()
    vara.transfer_result = TransferResult.NO_BUFFER_REPORTS
    vara.state.transport_lost = True
    vara.link_closes = False
    logs = []
    result = []

    VaraP2PBackend(vara, on_log=logs.append)._send(
        Message(32, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"x"),
        result.append,
    )

    assert result == [False]
    assert any("NOT confirmed on the air" in line for line in logs)
    # A dead command port cannot carry an ABORT; do not pretend otherwise.
    assert ("abort",) not in vara.commands


def test_write_data_reconnects_a_data_socket_vara_has_closed() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    port = listener.getsockname()[1]
    opened = []

    vara = VaraClient(data_port=port)
    vara._data = socket.create_connection(("127.0.0.1", port), timeout=5)
    opened.append(vara._data)
    server_side = listener.accept()[0]
    try:
        # VARA drops its end; a lone sendall would then vanish silently.
        server_side.close()
        deadline = time.monotonic() + 5.0
        while vara.data_socket_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not vara.data_socket_alive()

        vara.write_data(b"payload")
        opened.append(vara._data)
        reconnected = listener.accept()[0]
        opened.append(reconnected)

        assert vara.state.data_socket_reopens == 1
        assert reconnected.recv(16) == b"payload"
    finally:
        for sock in (*opened, listener):
            try:
                sock.close()
            except OSError:
                pass


def test_vara_bitrate_notification_is_parsed_for_airtime_estimates() -> None:
    vara = VaraClient()
    vara._handle_notification("BITRATE (1)  566 bps TX")

    assert vara.state.tx_bitrate_bps == 566


def test_vara_counts_keyings_and_buffer_reports_for_diagnostics() -> None:
    vara = VaraClient()
    vara.prepare_data_transfer()
    for notification in ("PTT ON", "PTT OFF", "PTT ON", "BUFFER 128"):
        vara._handle_notification(notification)

    assert vara.state.ptt_keyings == 2
    assert vara.state.buffer_reports == 1


def test_vara_send_waits_for_connected_link_to_settle_before_data_write() -> None:
    events = []

    class SettlingVara(FakeVara):
        def wait_data_ready(self) -> None:
            events.append("ready")

        def write_data(self, data: bytes) -> None:
            events.append("write")
            super().write_data(data)

    result = []
    VaraP2PBackend(SettlingVara())._send(
        Message(20, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"x"),
        result.append,
    )

    assert result == [True]
    assert events == ["ready", "write"]


def test_vara_send_without_buffer_notifications_uses_degraded_barrier() -> None:
    vara = FakeVara()
    vara.transfer_result = TransferResult.NO_BUFFER_REPORTS
    result = []

    VaraP2PBackend(vara)._send(
        Message(19, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"x"),
        result.append,
    )

    assert result == [True]
    assert ("abort",) not in vara.commands
    assert ("finish-write",) in vara.commands
    assert ("disconnect",) in vara.commands


def test_vara_send_reports_peer_close_before_drain_as_failure() -> None:
    vara = FakeVara()
    vara.transfer_result = TransferResult.PEER_CLOSED_EARLY
    result = []

    VaraP2PBackend(vara)._send(
        Message(22, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"x"),
        result.append,
    )

    assert result == [False]
    assert ("abort",) in vara.commands
    assert ("disconnect",) not in vara.commands


def test_vara_transfer_timeout_scales_for_large_envelopes() -> None:
    assert transfer_timeout_for(MIN_WIRE_SIZE) == TRANSFER_TIMEOUT
    assert transfer_timeout_for(10_000) > TRANSFER_TIMEOUT


def test_vara_payload_session_keeps_startup_tcp_pair_and_inbound_listener() -> None:
    outgoing = FakeVara()
    incoming = FakeVara(encode_envelope(21, b"x"))
    outgoing_generation = outgoing.state.data_socket_generation
    incoming_generation = incoming.state.data_socket_generation

    send_result = []
    receive_result = []
    VaraP2PBackend(outgoing)._send(
        Message(21, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"x"),
        send_result.append,
    )
    VaraP2PBackend(incoming)._receive(
        Message(21, "OK7PS", "OK1AAA", "OK1AAA"),
        receive_result.append,
    )

    assert send_result == [True]
    assert receive_result == [True]
    assert outgoing.state.data_socket_generation == outgoing_generation
    assert incoming.state.data_socket_generation == incoming_generation
    assert ("listen", True) not in incoming.commands
    assert ("disconnect",) not in incoming.commands


def test_vara_receive_releases_codec_before_received_callback() -> None:
    events = []
    incoming = FakeVara(encode_envelope(17, b"x"))
    backend = VaraP2PBackend(
        incoming,
        on_acquire=lambda: events.append("acquire"),
        on_release=lambda: events.append("release"),
    )
    backend._receive(
        Message(17, "OK1AAA", "OK7PS", "OK7PS"),
        lambda ok: events.append(("done", ok)),
    )

    assert events == ["acquire", "release", ("done", True)]


def test_vara_does_not_connect_when_audio_handoff_fails() -> None:
    vara = FakeVara()
    result = []
    backend = VaraP2PBackend(
        vara,
        on_acquire=lambda: (_ for _ in ()).throw(
            TimeoutError("control TX still active")
        ),
    )

    backend._send(
        Message(15, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"x"),
        result.append,
    )

    assert result == [False]
    assert vara.commands == []




def test_abort_is_only_sent_when_there_is_a_link_to_abort() -> None:
    # VARA answers WRONG to an ABORT with no link up. That left a permanent
    # "VARA rejected: ABORT" in the diagnostics of a station whose *peer* had
    # failed to transmit -- an alarming line about the one component that was
    # working correctly.
    from guardian.payload.vara_p2p import VaraP2PBackend

    vara = FakeVara()
    backend = VaraP2PBackend(vara=vara)

    vara.state.link_state = "DISCONNECTED"
    backend._abort_link()
    assert ("abort",) not in vara.commands

    vara.state.link_state = "CONNECTED"
    backend._abort_link()
    assert ("abort",) in vara.commands
