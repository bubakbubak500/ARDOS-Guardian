"""Direct VARA peer-to-peer payload backend.

Guardian owns VARA and moves the payload itself: the initiator connects to the
next hop and writes a small framed envelope; the responder (already LISTENing)
accepts the connection and reads it back. Self-contained — no Winlink, no
internet.

Payload envelope (big-endian):
    magic  4   b"GPLD"
    msg_id 4   uint32
    length 4   uint32
    body   ..  raw bytes
    crc16  2   CRC-16/CCITT-FALSE over magic..body

Requires a connected VaraClient and a real radio; runs its blocking work on a
worker thread and reports via the done() callback.
"""

from __future__ import annotations

import struct
import threading

from ..protocol import crc16
from .base import DoneCb, PayloadBackend

_MAGIC = b"GPLD"
_HDR = struct.Struct(">4sII")
_CRC = struct.Struct(">H")
CONNECT_TIMEOUT = 45.0
TRANSFER_TIMEOUT = 180.0


def encode_envelope(msg_id: int, body: bytes) -> bytes:
    head = _HDR.pack(_MAGIC, msg_id & 0xFFFFFFFF, len(body))
    return head + body + _CRC.pack(crc16(head + body))


class VaraP2PBackend(PayloadBackend):
    name = "vara_p2p"

    def __init__(self, vara=None, on_log=None, on_qsy=None, on_unqsy=None):
        self.vara = vara
        self.on_log = on_log or (lambda m: None)
        # Optional QSY hooks: on_qsy(callsign) tunes the radio to that station's
        # frequency before connecting; on_unqsy() restores the previous channel.
        self.on_qsy = on_qsy
        self.on_unqsy = on_unqsy

    # ------------------------------------------------------------------ #
    def start_send(self, msg, done: DoneCb) -> None:
        threading.Thread(target=self._send, args=(msg, done), daemon=True).start()

    def _send(self, msg, done: DoneCb) -> None:
        if self.vara is None or not self.vara.connected:
            self.on_log("VARA P2P: command port not connected")
            done(False)
            return
        if self.on_qsy:
            self._safe(lambda: self.on_qsy(msg.next_hop))
        try:
            self.vara.connect_to(msg.next_hop)
            if not self.vara.wait_link("CONNECTED", CONNECT_TIMEOUT):
                self.on_log(f"VARA P2P: link to {msg.next_hop} not established")
                done(False)
                return
            data = msg.payload_bytes if msg.payload_bytes is not None else msg.body.encode("utf-8")
            self.vara.write_data(encode_envelope(msg.msg_id, data))
            self.on_log(f"VARA P2P: payload #{msg.msg_id} sent to {msg.next_hop} ({len(data)} bytes)")
            done(True)
        except Exception as exc:  # noqa: BLE001
            self.on_log(f"VARA P2P send failed: {exc}")
            done(False)
        finally:
            if self.on_unqsy:
                self._safe(self.on_unqsy)

    @staticmethod
    def _safe(fn) -> None:
        try:
            fn()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    def start_receive(self, msg, done: DoneCb) -> None:
        threading.Thread(target=self._receive, args=(msg, done), daemon=True).start()

    def _receive(self, msg, done: DoneCb) -> None:
        if self.vara is None or not self.vara.connected:
            self.on_log("VARA P2P: command port not connected")
            done(False)
            return
        try:
            self.vara.listen(True)
            if not self.vara.wait_link("CONNECTED", CONNECT_TIMEOUT):
                self.on_log("VARA P2P: no incoming link")
                done(False)
                return
            head = self.vara.read_exactly(_HDR.size, TRANSFER_TIMEOUT)
            magic, mid, length = _HDR.unpack(head)
            if magic != _MAGIC:
                self.on_log("VARA P2P: bad payload magic")
                done(False)
                return
            body = self.vara.read_exactly(length, TRANSFER_TIMEOUT)
            crc_given = _CRC.unpack(self.vara.read_exactly(_CRC.size, TRANSFER_TIMEOUT))[0]
            if crc_given != crc16(head + body):
                self.on_log(f"VARA P2P: CRC failed on #{mid}")
                done(False)
                return
            msg.payload_bytes = body            # raw bundle for the mail store
            msg.body = ""                       # body lives inside the bundle now
            self.on_log(f"VARA P2P: payload #{mid} received OK ({length} bytes)")
            done(True)
        except Exception as exc:  # noqa: BLE001
            self.on_log(f"VARA P2P receive failed: {exc}")
            done(False)
