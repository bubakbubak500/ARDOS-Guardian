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

    def __init__(self, vara=None, on_log=None, on_qsy=None, on_unqsy=None,
                 on_acquire=None, on_release=None):
        self.vara = vara
        self.on_log = on_log or (lambda m: None)
        # Optional QSY hooks: on_qsy(callsign) tunes the radio to that station's
        # frequency before connecting; on_unqsy() restores the previous channel.
        self.on_qsy = on_qsy
        self.on_unqsy = on_unqsy
        # Soundcard handoff hooks: on_air with one codec (e.g. an IC-705), the
        # control modem and VARA share a single device. on_acquire() frees the
        # control channel so VARA can own the codec; on_release() reclaims it.
        self.on_acquire = on_acquire
        self.on_release = on_release
        self._transfer_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    def start_send(self, msg, done: DoneCb) -> None:
        threading.Thread(target=self._send, args=(msg, done), daemon=True).start()

    def _send(self, msg, done: DoneCb) -> None:
        if self.vara is None or not self.vara.connected:
            self.on_log("VARA P2P: command port not connected")
            done(False)
            return
        success = False
        acquired = False
        link_started = False
        with self._transfer_lock:
            if self.on_qsy:
                self._safe(lambda: self.on_qsy(msg.next_hop))
            try:
                if self.on_acquire:
                    self.on_acquire()
                    acquired = True
                self.vara.connect_to(msg.next_hop)
                link_started = True
                if not self.vara.wait_link("CONNECTED", CONNECT_TIMEOUT):
                    self.on_log(f"VARA P2P: link to {msg.next_hop} not established")
                    self._abort_link()
                else:
                    data = (
                        msg.payload_bytes
                        if msg.payload_bytes is not None
                        else msg.body.encode("utf-8")
                    )
                    self.vara.prepare_data_transfer()
                    self.vara.write_data(encode_envelope(msg.msg_id, data))
                    self.vara.disconnect_link()
                    self.on_log(
                        f"VARA P2P: payload #{msg.msg_id} queued for "
                        f"{msg.next_hop} ({len(data)} bytes)"
                    )
                    if self.vara.wait_link("DISCONNECTED", TRANSFER_TIMEOUT):
                        self.on_log(
                            f"VARA P2P: payload #{msg.msg_id} transmitted "
                            "and VARA link closed"
                        )
                        success = True
                    else:
                        self.on_log(
                            f"VARA P2P: transfer #{msg.msg_id} did not finish"
                        )
                        self._abort_link()
            except Exception as exc:  # noqa: BLE001
                self.on_log(f"VARA P2P send failed: {exc}")
                if link_started:
                    self._abort_link()
            finally:
                if acquired and self.on_release:
                    self._safe(self.on_release)
                if self.on_unqsy:
                    self._safe(self.on_unqsy)
        # done() may immediately send RECEIVED/CANCEL over AFSK, so it must run
        # only after the shared soundcard has been returned to that modem.
        done(success)

    @staticmethod
    def _safe(fn) -> None:
        try:
            fn()
        except Exception:
            pass

    def _abort_link(self) -> None:
        """Stop a failed/stale exchange and briefly await RF release."""
        try:
            self.vara.abort()
        except Exception:
            return
        try:
            self.vara.wait_link("DISCONNECTED", 5.0)
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
        success = False
        acquired = False
        with self._transfer_lock:
            try:
                if self.on_acquire:
                    self.on_acquire()
                    acquired = True
                self.vara.listen(True)
                if not self.vara.wait_link("CONNECTED", CONNECT_TIMEOUT):
                    self.on_log("VARA P2P: no incoming link")
                else:
                    head = self.vara.read_exactly(_HDR.size, TRANSFER_TIMEOUT)
                    magic, mid, length = _HDR.unpack(head)
                    if magic != _MAGIC:
                        raise ValueError("bad payload magic")
                    body = self.vara.read_exactly(length, TRANSFER_TIMEOUT)
                    crc_given = _CRC.unpack(
                        self.vara.read_exactly(_CRC.size, TRANSFER_TIMEOUT)
                    )[0]
                    if crc_given != crc16(head + body):
                        raise ValueError(f"CRC failed on #{mid}")
                    msg.payload_bytes = body
                    msg.body = ""
                    self.on_log(
                        f"VARA P2P: payload #{mid} received OK ({length} bytes)"
                    )
                    success = True
            except Exception as exc:  # noqa: BLE001
                self.on_log(f"VARA P2P receive failed: {exc}")
            finally:
                # Close VARA before the callback sends RECEIVED over AFSK.
                if success:
                    try:
                        self.vara.disconnect_link()
                        self.vara.wait_link("DISCONNECTED", 10.0)
                    except Exception:
                        self._abort_link()
                else:
                    self._abort_link()
                try:
                    self.vara.listen(False)
                except Exception:
                    pass
                if acquired and self.on_release:
                    self._safe(self.on_release)
        done(success)
