import struct
from types import SimpleNamespace

from guardian.payload.vara_p2p import (
    MIN_WIRE_SIZE,
    VaraP2PBackend,
    encode_envelope,
)
from guardian.payload.winlink_manual import WinlinkManualBackend
from guardian.protocol import crc16
from guardian.session import Message
from guardian.vara.client import VaraClient


class FakeVara:
    def __init__(self, incoming: bytes = b"") -> None:
        self.connected = True
        self.incoming = bytearray(incoming)
        self.commands = []
        self.written = b""
        self.transfer_complete = True
        self.state = SimpleNamespace(
            tx_buffer_bytes=None, data_socket_generation=1
        )

    def connect_to(self, callsign: str) -> None:
        self.commands.append(("connect", callsign))

    def renew_connection_pair(self) -> None:
        self.state.data_socket_generation += 1
        self.commands.append(("renew-pair",))

    def listen(self, enabled: bool) -> None:
        self.commands.append(("listen", enabled))

    def wait_link(self, state: str, timeout: float) -> bool:
        return state in {"CONNECTED", "DISCONNECTED"}

    def abort(self) -> None:
        self.commands.append(("abort",))

    def disconnect_link(self) -> None:
        self.commands.append(("disconnect",))

    def prepare_data_transfer(self) -> None:
        self.commands.append(("prepare",))

    def wait_data_ready(self) -> None:
        self.commands.append(("data-ready",))

    def wait_data_accepted(self, timeout: float) -> bool:
        self.commands.append(("accepted",))
        self.state.tx_buffer_bytes = len(self.written)
        return True

    def wait_transfer_complete(self, timeout: float) -> bool:
        return self.transfer_complete

    def write_data(self, data: bytes) -> None:
        self.written += data

    def finish_data_write(self) -> None:
        self.commands.append(("finish-write",))

    def read_exactly(self, size: int, timeout: float) -> bytes:
        result = bytes(self.incoming[:size])
        del self.incoming[:size]
        if len(result) != size:
            raise EOFError("test stream ended early")
        return result


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


def test_vara_buffer_notification_confirms_data_port_acceptance() -> None:
    vara = VaraClient()
    vara.prepare_data_transfer()

    assert not vara.wait_data_accepted(0)
    vara._handle_notification("BUFFER 411")

    assert vara.wait_data_accepted(0)
    assert vara.state.tx_buffer_bytes == 411


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


def test_vara_renews_complete_socket_pair_before_payload_session(
    monkeypatch,
) -> None:
    class FakeSocket:
        def __init__(self, local=None, peer=None) -> None:
            self.closed = False
            self.local = local
            self.peer = peer
            self.sent = []

        def shutdown(self, how) -> None:
            pass

        def close(self) -> None:
            self.closed = True

        def settimeout(self, value) -> None:
            pass

        def setsockopt(self, level, option, value) -> None:
            pass

        def sendall(self, data) -> None:
            self.sent.append(data)

        def getsockname(self):
            return self.local

        def getpeername(self):
            return self.peer

    old_cmd = FakeSocket()
    old_data = FakeSocket()
    new_cmd = FakeSocket()
    new_data = FakeSocket(
        ("127.0.0.1", 50123), ("127.0.0.1", 8301)
    )
    sockets = iter((new_cmd, new_data))
    vara = VaraClient()
    vara._cmd = old_cmd
    vara._data = old_data
    vara.state.cmd_connected = True
    vara.state.data_connected = True
    vara.state.mycall = "OK7PS"
    monkeypatch.setattr(
        "guardian.vara.client.socket.create_connection",
        lambda address, timeout: next(sockets),
    )
    monkeypatch.setattr(
        "guardian.vara.client.threading.Thread.start", lambda self: None
    )
    monkeypatch.setattr("guardian.vara.client.time.sleep", lambda timeout: None)

    vara.renew_connection_pair()

    assert old_cmd.closed
    assert old_data.closed
    assert vara._cmd is new_cmd
    assert vara._data is new_data
    assert vara.state.data_socket_generation == 1
    assert vara.state.data_local_endpoint == "127.0.0.1:50123"
    assert vara.state.data_peer_endpoint == "127.0.0.1:8301"
    assert new_cmd.sent == [
        b"PUBLIC ON\r",
        b"COMPRESSION OFF\r",
        b"MYCALL OK7PS\r",
    ]


def test_vara_send_and_receive_preserve_payload_bytes() -> None:
    outgoing = FakeVara()
    send_result = []
    message = Message(12, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"bundle")
    backend = VaraP2PBackend(outgoing)

    backend._send(message, send_result.append)

    assert send_result == [True]
    assert outgoing.commands == [
        ("renew-pair",),
        ("listen", False),
        ("connect", "OK1AAA"),
        ("data-ready",),
        ("prepare",),
        ("accepted",),
        ("finish-write",),
        ("disconnect",),
        ("listen", True),
    ]
    assert outgoing.written == encode_envelope(12, b"bundle")

    incoming = FakeVara(outgoing.written)
    receive_result = []
    received = Message(12, "OK7PS", "OK1AAA", "OK1AAA")

    VaraP2PBackend(incoming)._receive(received, receive_result.append)

    assert receive_result == [True]
    assert incoming.commands == [
        ("renew-pair",),
        ("listen", True),
        ("disconnect",),
    ]
    assert received.payload_bytes == b"bundle"


def test_vara_qsy_happens_before_handoff_and_restore_after_release() -> None:
    events = []
    backend = VaraP2PBackend(
        FakeVara(),
        on_qsy=lambda callsign: events.append(("qsy", callsign)),
        on_acquire=lambda: events.append("acquire"),
        on_release=lambda: events.append("release"),
        on_unqsy=lambda: events.append("restore"),
    )

    backend._send(
        Message(14, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"x"),
        lambda ok: events.append(("done", ok)),
    )

    assert events[:2] == [("qsy", "OK1AAA"), "acquire"]
    assert events[-3:] == ["release", "restore", ("done", True)]


def test_vara_send_keeps_codec_until_rf_transfer_finishes() -> None:
    events = []

    class OrderedVara(FakeVara):
        def wait_link(self, state: str, timeout: float) -> bool:
            if state == "DISCONNECTED":
                events.append("rf-finished")
            return super().wait_link(state, timeout)

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
    assert vara.commands.index(("finish-write",)) < vara.commands.index(
        ("disconnect",)
    )


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


def test_vara_aborts_empty_session_when_data_port_never_queues_payload() -> None:
    class RejectingVara(FakeVara):
        def wait_data_accepted(self, timeout: float) -> bool:
            self.commands.append(("accepted",))
            return False

    vara = RejectingVara()
    result = []

    VaraP2PBackend(vara)._send(
        Message(19, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"x"),
        result.append,
    )

    assert result == [False]
    assert ("abort",) in vara.commands
    assert ("disconnect",) not in vara.commands
    assert ("finish-write",) not in vara.commands


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


def test_winlink_handoff_preserves_prompt_and_resource_order() -> None:
    events = []

    def prompt(role, message, done):
        events.append(("prompt", role, message.msg_id))
        done(True)

    backend = WinlinkManualBackend(
        prompt=prompt,
        on_acquire=lambda: events.append("acquire"),
        on_release=lambda: events.append("release"),
    )
    result = []

    backend.start_send(
        Message(13, "OK7PS", "OK1AAA", "OK1AAA"),
        result.append,
    )

    assert events == ["acquire", ("prompt", "send", 13), "release"]
    assert result == [True]
