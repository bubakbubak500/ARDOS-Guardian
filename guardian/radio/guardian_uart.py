"""Guardian firmware runtime UART protocols for UV-K5 and UV-K61 radios.

The two firmwares expose the same protocol-v3 VFO payloads through different
wire envelopes.  This module deliberately has no Qt dependency and accepts a
pyserial-like object so framing and failure paths can be tested without RF
hardware.
"""

from __future__ import annotations

import re
import secrets
import struct
import time
from dataclasses import dataclass
from typing import Protocol


K5_OBFUSCATION = bytes.fromhex(
    "16 6C 14 E6 2E 91 0D 40 21 35 D5 40 13 03 E9 80"
)
K5_MAX_PAYLOAD = 1024
PROTOCOL_VERSION = 3
GET_CAPABILITIES = 0x06A0
SET_PROFILE = 0x06A2
GET_VFOS = 0x06A4
SET_BOTH_VFOS = 0x06A8
READ_EEPROM = 0x051B
UNCHANGED_PROFILE = 0xFF

RESULT_NAMES = {
    1: "invalid VFO",
    2: "invalid RX frequency",
    3: "TX frequency rejected by the radio frequency lock",
    4: "invalid Guardian RF profile",
    5: "radio is transmitting",
    6: "unsupported flags",
    7: "invalid Dual Watch state",
}


class GuardianProtocolError(RuntimeError):
    """A UART frame was missing, malformed, or inconsistent."""


class GuardianCompatibilityError(GuardianProtocolError):
    """The connected firmware does not provide the required UART protocol."""


class GuardianRadioRejected(GuardianProtocolError):
    """The radio understood a request but rejected its values."""

    def __init__(self, result: int):
        self.result = int(result)
        super().__init__(RESULT_NAMES.get(self.result, f"radio error {self.result}"))


class SerialPort(Protocol):
    def read(self, size: int = 1) -> bytes: ...
    def write(self, data: bytes) -> int: ...
    def reset_input_buffer(self) -> None: ...


@dataclass(frozen=True, slots=True)
class GuardianVfoState:
    protocol_version: int
    active_vfo: int
    active_profile: int
    dual_watch: bool
    vfo_a_rx: int
    vfo_a_tx: int
    vfo_b_rx: int
    vfo_b_tx: int
    register_43: int = 0

    @property
    def active_frequency(self) -> int:
        return self.vfo_a_rx if self.active_vfo == 0 else self.vfo_b_rx

    def tuning_tuple(self) -> tuple[int, int, int, int, int, bool]:
        return (
            self.vfo_a_rx,
            self.vfo_a_tx,
            self.vfo_b_rx,
            self.vfo_b_tx,
            self.active_vfo,
            self.dual_watch,
        )


def crc16_xmodem(data: bytes, initial: int = 0) -> int:
    crc = initial & 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = (
                ((crc << 1) ^ 0x1021) & 0xFFFF
                if crc & 0x8000
                else (crc << 1) & 0xFFFF
            )
    return crc


def _read_exact(port: SerialPort, size: int, deadline: float) -> bytes:
    result = bytearray()
    while len(result) < size:
        if time.monotonic() >= deadline:
            raise GuardianProtocolError(
                f"UART timeout after {len(result)}/{size} bytes"
            )
        block = port.read(size - len(result))
        if block:
            result.extend(block)
    return bytes(result)


def _write_all(port: SerialPort, data: bytes) -> None:
    if port.write(data) != len(data):
        raise GuardianProtocolError("serial driver accepted only part of the command")
    flush = getattr(port, "flush", None)
    if callable(flush):
        flush()


def _parse_state(payload: bytes, *, set_reply: bool = False) -> GuardianVfoState:
    if len(payload) != 24:
        raise GuardianProtocolError(
            f"Guardian VFO reply has {len(payload)} bytes instead of 24"
        )
    first, active, profile, dual, arx, atx, brx, btx, register, _ = struct.unpack(
        "<BBBBIIIIHH", payload
    )
    if set_reply:
        if first:
            raise GuardianRadioRejected(first)
        version = PROTOCOL_VERSION
    else:
        version = first
        if version != PROTOCOL_VERSION:
            raise GuardianCompatibilityError(
                f"Guardian UART protocol v{version} is unsupported; v3 is required"
            )
    if active not in (0, 1):
        raise GuardianProtocolError(f"invalid active VFO {active}")
    if dual not in (0, 1):
        raise GuardianProtocolError(f"invalid Dual Watch value {dual}")
    if not all((arx, atx, brx, btx)):
        raise GuardianProtocolError("radio returned an empty VFO frequency")
    return GuardianVfoState(
        version,
        active,
        profile,
        bool(dual),
        arx,
        atx,
        brx,
        btx,
        register,
    )


def _state_request(
    token: int,
    state: GuardianVfoState,
    *,
    active_vfo: int,
    vfo_b_frequency: int | None = None,
) -> bytes:
    brx = state.vfo_b_rx if vfo_b_frequency is None else int(vfo_b_frequency)
    btx = state.vfo_b_tx if vfo_b_frequency is None else int(vfo_b_frequency)
    return struct.pack(
        "<IIIIIBBBB",
        token,
        state.vfo_a_rx,
        state.vfo_a_tx,
        brx,
        btx,
        active_vfo,
        UNCHANGED_PROFILE,
        int(state.dual_watch),
        0,
    )


def _k5_xor(data: bytes) -> bytes:
    return bytes(
        value ^ K5_OBFUSCATION[index % len(K5_OBFUSCATION)]
        for index, value in enumerate(data)
    )


def k5_packet(command_id: int, payload: bytes = b"") -> bytes:
    return struct.pack("<HH", command_id, len(payload)) + payload


def parse_k5_packet(raw: bytes) -> tuple[int, bytes]:
    if len(raw) < 4:
        raise GuardianProtocolError("K5 packet is shorter than its header")
    command, declared = struct.unpack_from("<HH", raw)
    payload = raw[4:]
    if declared != len(payload):
        raise GuardianProtocolError(
            f"K5 packet declares {declared} bytes, received {len(payload)}"
        )
    return command, payload


def encode_k5_envelope(raw_packet: bytes) -> bytes:
    if len(raw_packet) > K5_MAX_PAYLOAD:
        raise ValueError("packet is too large for the K5 UART envelope")
    protected = raw_packet + struct.pack("<H", crc16_xmodem(raw_packet))
    return (
        b"\xAB\xCD"
        + struct.pack("<H", len(raw_packet))
        + _k5_xor(protected)
        + b"\xDC\xBA"
    )


def decode_k5_envelope(frame: bytes) -> bytes:
    if len(frame) < 8 or frame[:2] != b"\xAB\xCD" or frame[-2:] != b"\xDC\xBA":
        raise GuardianProtocolError("invalid K5 UART envelope markers")
    size = struct.unpack_from("<H", frame, 2)[0]
    if size > K5_MAX_PAYLOAD or len(frame) != size + 8:
        raise GuardianProtocolError("invalid K5 UART envelope length")
    protected = frame[4:-2]
    raw_packet = _k5_xor(protected[:size])
    wire_crc = protected[size:]
    xor_crc = bytes(
        value ^ K5_OBFUSCATION[(size + index) % len(K5_OBFUSCATION)]
        for index, value in enumerate(wire_crc)
    )
    candidates = {
        struct.unpack("<H", candidate)[0] for candidate in (wire_crc, xor_crc)
    }
    if not candidates.intersection((0xFFFF, crc16_xmodem(raw_packet))):
        raise GuardianProtocolError("K5 UART envelope CRC mismatch")
    return raw_packet


def read_k5_envelope(port: SerialPort, timeout: float = 2.0) -> bytes:
    deadline = time.monotonic() + timeout
    previous = b""
    while time.monotonic() < deadline:
        current = port.read(1)
        if not current:
            continue
        if previous == b"\xAB" and current == b"\xCD":
            break
        previous = current
    else:
        raise GuardianProtocolError("radio did not send a K5 UART header")
    length = _read_exact(port, 2, deadline)
    size = struct.unpack("<H", length)[0]
    if size > K5_MAX_PAYLOAD:
        raise GuardianProtocolError(f"K5 UART envelope is too large ({size} bytes)")
    remainder = _read_exact(port, size + 4, deadline)
    return decode_k5_envelope(b"\xAB\xCD" + length + remainder)


def exchange_k5(
    port: SerialPort,
    command: int,
    payload: bytes,
    expected: int,
    *,
    timeout: float = 2.0,
) -> bytes:
    _write_all(port, encode_k5_envelope(k5_packet(command, payload)))
    reply_id, reply = parse_k5_packet(read_k5_envelope(port, timeout))
    if reply_id != expected:
        raise GuardianProtocolError(
            f"expected K5 reply 0x{expected:04X}, received 0x{reply_id:04X}"
        )
    return reply


def encode_k61_frame(operation: int, payload: bytes = b"") -> bytes:
    body = b"\x47" + operation.to_bytes(2, "big") + len(payload).to_bytes(2, "big") + payload
    return b"\xA6" + body + crc16_xmodem(body).to_bytes(2, "big")


def read_k61_frame(port: SerialPort, timeout: float = 2.0) -> tuple[int, bytes]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port.read(1) == b"\xA6":
            break
    else:
        raise GuardianProtocolError("radio did not send a K61 UART header")
    header = _read_exact(port, 5, deadline)
    command = header[0]
    operation = int.from_bytes(header[1:3], "big")
    length = int.from_bytes(header[3:5], "big")
    if length > K5_MAX_PAYLOAD:
        raise GuardianProtocolError(f"K61 UART frame is too large ({length} bytes)")
    payload = _read_exact(port, length, deadline)
    wire_crc = int.from_bytes(_read_exact(port, 2, deadline), "big")
    if wire_crc != crc16_xmodem(header + payload):
        raise GuardianProtocolError("K61 UART frame CRC mismatch")
    if command == 0xEE:
        code = payload[0] if payload else 0
        raise GuardianProtocolError(f"K61 UART protocol error {code}")
    if command != 0x47:
        raise GuardianProtocolError(f"unexpected K61 command 0x{command:02X}")
    return operation, payload


def exchange_k61(
    port: SerialPort,
    operation: int,
    payload: bytes,
    expected: int,
    *,
    timeout: float = 2.0,
) -> bytes:
    port.reset_input_buffer()
    _write_all(port, encode_k61_frame(operation, payload))
    reply_operation, reply = read_k61_frame(port, timeout)
    if reply_operation != expected:
        raise GuardianProtocolError(
            f"expected K61 reply 0x{expected:04X}, received 0x{reply_operation:04X}"
        )
    return reply


class K5RuntimeClient:
    def __init__(self, port: SerialPort):
        self.port = port
        self.token = 0
        self.firmware_version = ""

    def hello(self) -> str:
        self.token = secrets.randbits(32) or 1
        reply = exchange_k5(
            self.port,
            0x0514,
            struct.pack("<I", self.token),
            0x0515,
            timeout=2.5,
        )
        if len(reply) < 16:
            raise GuardianProtocolError("K5 firmware identity response is incomplete")
        version = reply[:16].split(b"\0", 1)[0].decode("ascii", "replace").strip()
        match = re.search(r"GK5\s+(\d+)(?:\.(\d+))?", version, re.IGNORECASE)
        if not match:
            raise GuardianCompatibilityError(
                f"{version or 'unknown firmware'} is not Guardian K5FW"
            )
        release = (int(match.group(1)), int(match.group(2) or 0))
        if release < (1, 5):
            raise GuardianCompatibilityError(
                f"Guardian K5FW {release[0]}.{release[1]} is too old; 1.5+ is required"
            )
        self.firmware_version = version
        return version

    def read_state(self) -> GuardianVfoState:
        if not self.token:
            raise GuardianProtocolError("K5 UART handshake has not completed")
        return _parse_state(
            exchange_k5(
                self.port,
                GET_VFOS,
                struct.pack("<I", self.token),
                GET_VFOS + 1,
            )
        )

    def read_eeprom(self, offset: int, size: int) -> bytes:
        """Read a bounded GK5 EEPROM region through the stock read-only command."""

        if not self.token:
            raise GuardianProtocolError("K5 UART handshake has not completed")
        address = int(offset)
        length = int(size)
        if not 0 <= address <= 0xFFFF:
            raise ValueError("K5 EEPROM offset must be in range 0..65535")
        if not 1 <= length <= 128 or address + length > 0x10000:
            raise ValueError("K5 EEPROM read must contain 1..128 in-range bytes")
        reply = exchange_k5(
            self.port,
            READ_EEPROM,
            struct.pack("<HBBI", address, length, 0, self.token),
            READ_EEPROM + 1,
        )
        if len(reply) != length + 4:
            raise GuardianProtocolError(
                f"K5 EEPROM reply has {len(reply)} bytes instead of {length + 4}"
            )
        reply_address, reply_length, padding = struct.unpack_from("<HBB", reply)
        if reply_address != address or reply_length != length or padding:
            raise GuardianProtocolError("K5 EEPROM reply does not match the request")
        return bytes(reply[4:])

    def set_profile(self, profile: int, *, persist: bool = False) -> tuple[int, int]:
        """Select a GK5 filter profile and return its confirmed register value."""

        if not self.token:
            raise GuardianProtocolError("K5 UART handshake has not completed")
        if profile not in range(5):
            raise ValueError("Guardian K5 profile must be in range 0..4")
        reply = exchange_k5(
            self.port,
            SET_PROFILE,
            struct.pack("<IBBxx", self.token, profile, int(persist)),
            SET_PROFILE + 1,
        )
        if len(reply) != 4:
            raise GuardianProtocolError(
                f"Guardian profile reply has {len(reply)} bytes instead of 4"
            )
        result, active_profile, register_43 = struct.unpack("<BBH", reply)
        if result:
            raise GuardianRadioRejected(result)
        if active_profile != profile:
            raise GuardianProtocolError(
                f"Guardian profile read-back mismatch: requested {profile}, "
                f"radio reports {active_profile}"
            )
        return active_profile, register_43

    def set_state(
        self,
        state: GuardianVfoState,
        *,
        active_vfo: int,
        vfo_b_frequency: int | None = None,
    ) -> GuardianVfoState:
        reply = exchange_k5(
            self.port,
            SET_BOTH_VFOS,
            _state_request(
                self.token,
                state,
                active_vfo=active_vfo,
                vfo_b_frequency=vfo_b_frequency,
            ),
            SET_BOTH_VFOS + 1,
        )
        return _parse_state(reply, set_reply=True)


class K61RuntimeClient:
    def __init__(self, port: SerialPort):
        self.port = port
        self.token = secrets.randbits(32) or 1
        self.firmware_version = ""

    def hello(self) -> str:
        reply = exchange_k61(
            self.port,
            GET_CAPABILITIES,
            struct.pack("<I", self.token),
            GET_CAPABILITIES + 1,
        )
        if len(reply) != 6:
            raise GuardianProtocolError(
                f"K61 capabilities reply has {len(reply)} bytes instead of 6"
            )
        version, profile_count, active_profile, reserved, register = struct.unpack(
            "<BBBBH", reply
        )
        if version != PROTOCOL_VERSION:
            raise GuardianCompatibilityError(
                f"Guardian K61 UART protocol v{version} is unsupported; v3 is required"
            )
        if profile_count < 2 or active_profile >= profile_count or reserved or register:
            raise GuardianProtocolError("K61 capabilities reply is inconsistent")
        self.firmware_version = "Guardian K61FW UART v3"
        return self.firmware_version

    def read_state(self) -> GuardianVfoState:
        return _parse_state(
            exchange_k61(
                self.port,
                GET_VFOS,
                struct.pack("<I", self.token),
                GET_VFOS + 1,
            )
        )

    def set_state(
        self,
        state: GuardianVfoState,
        *,
        active_vfo: int,
        vfo_b_frequency: int | None = None,
    ) -> GuardianVfoState:
        reply = exchange_k61(
            self.port,
            SET_BOTH_VFOS,
            _state_request(
                self.token,
                state,
                active_vfo=active_vfo,
                vfo_b_frequency=vfo_b_frequency,
            ),
            SET_BOTH_VFOS + 1,
        )
        return _parse_state(reply, set_reply=True)
