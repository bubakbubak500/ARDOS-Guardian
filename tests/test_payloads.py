import socket
import struct
import time
from types import SimpleNamespace

from guardian.payload.vara_p2p import (
    DISCONNECT_TIMEOUT,
    MIN_WIRE_SIZE,
    TRANSFER_TIMEOUT,
    VaraP2PBackend,
    airtime_for,
    disconnect_timeout_for,
    encode_envelope,
    transfer_timeout_for,
)
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

    def wait_transfer_complete(self, timeout: float) -> TransferResult:
        self.commands.append(("wait-transfer",))
        self.transfer_timeout = timeout
        return self.transfer_result

    def write_data(self, data: bytes) -> None:
        self.written += data
        self.state.tx_buffer_bytes = len(self.written)

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
    assert data.timeouts == [3.0, None]


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
