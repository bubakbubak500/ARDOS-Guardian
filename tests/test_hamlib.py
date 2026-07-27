from guardian.radio.hamlib import HamlibRadio


class ChunkedSocket:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)
        self.sent: list[bytes] = []

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, size: int) -> bytes:
        return self.chunks.pop(0)


def test_hamlib_consumes_complete_multiline_mode_reply() -> None:
    radio = HamlibRadio()
    sock = ChunkedSocket([
        b"145237500\n",
        b"FM\n15000\n",
        b"0\n",
        b"-54\n",
    ])
    radio._sock = sock

    state = radio.get_state()

    assert state.frequency_hz == 145_237_500
    assert state.mode == "FM"
    assert state.ptt is False
    assert state.signal == -54
    assert state.error is None
    assert sock.sent == [b"f\n", b"m\n", b"t\n", b"l STRENGTH\n"]


def test_hamlib_line_reader_preserves_partial_and_following_lines() -> None:
    radio = HamlibRadio()
    sock = ChunkedSocket([b"F", b"M\n150", b"00\n"])
    radio._sock = sock

    with radio._lock:
        reply = radio._command("m", reply_lines=2)

    assert reply == ["FM", "15000"]
