import struct

from guardian.payload.vara_p2p import VaraP2PBackend, encode_envelope
from guardian.payload.winlink_manual import WinlinkManualBackend
from guardian.protocol import crc16
from guardian.session import Message


class FakeVara:
    def __init__(self, incoming: bytes = b"") -> None:
        self.connected = True
        self.incoming = bytearray(incoming)
        self.commands = []
        self.written = b""

    def connect_to(self, callsign: str) -> None:
        self.commands.append(("connect", callsign))

    def listen(self, enabled: bool) -> None:
        self.commands.append(("listen", enabled))

    def wait_link(self, state: str, timeout: float) -> bool:
        return state == "CONNECTED"

    def write_data(self, data: bytes) -> None:
        self.written += data

    def read_exactly(self, size: int, timeout: float) -> bytes:
        result = bytes(self.incoming[:size])
        del self.incoming[:size]
        if len(result) != size:
            raise EOFError("test stream ended early")
        return result


def test_vara_envelope_contains_id_payload_and_crc() -> None:
    envelope = encode_envelope(0x12345678, b"hello")
    magic, msg_id, length = struct.unpack(">4sII", envelope[:12])
    crc = struct.unpack(">H", envelope[-2:])[0]

    assert magic == b"GPLD"
    assert msg_id == 0x12345678
    assert length == 5
    assert envelope[12:-2] == b"hello"
    assert crc == crc16(envelope[:-2])


def test_vara_send_and_receive_preserve_payload_bytes() -> None:
    outgoing = FakeVara()
    send_result = []
    message = Message(12, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"bundle")
    backend = VaraP2PBackend(outgoing)

    backend._send(message, send_result.append)

    assert send_result == [True]
    assert outgoing.commands == [("connect", "OK1AAA")]
    assert outgoing.written == encode_envelope(12, b"bundle")

    incoming = FakeVara(outgoing.written)
    receive_result = []
    received = Message(12, "OK7PS", "OK1AAA", "OK1AAA")

    VaraP2PBackend(incoming)._receive(received, receive_result.append)

    assert receive_result == [True]
    assert incoming.commands == [("listen", True)]
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
    assert events[-2:] == ["release", "restore"]


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
