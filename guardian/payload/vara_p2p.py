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
import time

from ..protocol import crc16
from ..vara import TransferResult
from .base import DoneCb, PayloadBackend

_MAGIC = b"GPLD"
_HDR = struct.Struct(">4sII")
_CRC = struct.Struct(">H")
# VARA FM can enter a BREAK/link-estimation loop when the application gives it
# less than one low-rate air frame.  A 1024-byte block is about 14 seconds at
# the unregistered 566 bps rate and is large enough to make the first frame
# actionable without making short Guardian messages excessively expensive.
MIN_WIRE_SIZE = 1024
CONNECT_TIMEOUT = 45.0
TRANSFER_TIMEOUT = 120.0
DISCONNECT_TIMEOUT = 30.0
_SLOW_LINK_BPS = 300.0
_TRANSFER_MARGIN = 3.0
# An unregistered VARA FM link is capped at 566 bps, and its ARQ spends about
# half the channel on the peer's acknowledgements.  A padded 1024-byte block
# therefore costs roughly 30 seconds of wall clock -- far more than the fixed
# 30 s disconnect budget that used to abort transfers mid-flight.
UNREGISTERED_FM_BPS = 566.0
_ARQ_DUTY_CYCLE = 0.5
_AIRTIME_MARGIN = 3.0
# VARA keys the transmitter about once a second while it drains its RF queue,
# so ten quiet seconds mean it has genuinely stopped sending.
PTT_QUIET_SECONDS = 10.0


def transfer_timeout_for(wire_bytes: int) -> float:
    """Allow three times the airtime of a conservative 300 bps VARA link."""
    return max(
        TRANSFER_TIMEOUT,
        wire_bytes * 8 / _SLOW_LINK_BPS * _TRANSFER_MARGIN,
    )


def airtime_for(wire_bytes: int, bitrate_bps: float | None = None) -> float:
    """Wall-clock seconds VARA needs to put wire_bytes on the air."""
    rate = bitrate_bps if bitrate_bps and bitrate_bps > 0 else UNREGISTERED_FM_BPS
    return wire_bytes * 8 / rate / _ARQ_DUTY_CYCLE


def disconnect_timeout_for(
    wire_bytes: int, bitrate_bps: float | None = None
) -> float:
    """A graceful DISCONNECT transmits the queue first, so budget for it."""
    return max(
        DISCONNECT_TIMEOUT,
        airtime_for(wire_bytes, bitrate_bps) * _AIRTIME_MARGIN,
    )


def encode_envelope(msg_id: int, body: bytes) -> bytes:
    head = _HDR.pack(_MAGIC, msg_id & 0xFFFFFFFF, len(body))
    framed = head + body + _CRC.pack(crc16(head + body))
    return framed.ljust(MIN_WIRE_SIZE, b"\0")


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
                self.on_log(
                    "VARA P2P: using persistent TCP pair 8300/8301 "
                    f"(generation {self.vara.state.data_socket_generation})"
                )
                # Do NOT toggle LISTEN around a connection.  VARA's native
                # command reference documents the outbound flow as MYCALL,
                # LISTEN ON, CONNECT, and warns that either LISTEN ON or
                # LISTEN OFF "will cause a disconnection if it is received in
                # the middle of a VARA connection".
                self.vara.connect_to(msg.next_hop)
                link_started = True
                if not self.vara.wait_link("CONNECTED", CONNECT_TIMEOUT):
                    self.on_log(f"VARA P2P: link to {msg.next_hop} not established")
                    self._abort_link()
                else:
                    self.vara.wait_data_ready()
                    data = (
                        msg.payload_bytes
                        if msg.payload_bytes is not None
                        else msg.body.encode("utf-8")
                    )
                    self.vara.prepare_data_transfer()
                    envelope = encode_envelope(msg.msg_id, data)
                    self.vara.write_data(envelope)
                    queued = self.vara.state.tx_buffer_bytes
                    self.on_log(
                        f"VARA P2P: payload #{msg.msg_id} handed to VARA "
                        f"({len(data)} payload bytes / {len(envelope)} wire bytes, "
                        f"reported buffer {queued}, {self._socket_report()})"
                    )
                    transfer_timeout = transfer_timeout_for(len(envelope))
                    bitrate = getattr(
                        self.vara.state, "tx_bitrate_bps", None
                    )
                    airtime = airtime_for(len(envelope), bitrate)
                    closing = disconnect_timeout_for(len(envelope), bitrate)
                    result = self.vara.wait_transfer_complete(transfer_timeout)
                    if result is TransferResult.DRAINED:
                        self.on_log(
                            f"VARA P2P: payload #{msg.msg_id} RF queue drained; "
                            "disconnecting"
                        )
                        self.vara.disconnect_link()
                        if self._wait_closed(closing):
                            self.on_log(
                                f"VARA P2P: payload #{msg.msg_id} transmitted "
                                "and VARA link closed"
                            )
                            success = True
                        else:
                            self.on_log(
                                f"VARA P2P: transfer #{msg.msg_id} drained but "
                                "the link did not close"
                            )
                            self._abort_link()
                    elif result is TransferResult.NO_BUFFER_REPORTS:
                        self.on_log(
                            f"VARA P2P: no BUFFER telemetry for payload "
                            f"#{msg.msg_id}; holding the link until VARA stops "
                            f"keying (~{airtime:.0f}s of airtime at "
                            f"{bitrate or UNREGISTERED_FM_BPS:.0f} bps, "
                            f"budget {closing:.0f}s)"
                        )
                        self.vara.finish_data_write()
                        self.vara.disconnect_link()
                        if self._wait_closed(closing):
                            self.on_log(
                                f"VARA P2P: payload #{msg.msg_id} completed "
                                "without buffer-drain confirmation"
                            )
                            success = True
                        elif self._transport_lost():
                            self.on_log(
                                f"VARA P2P: lost the TCP session to VARA while "
                                f"sending #{msg.msg_id}; the payload was NOT "
                                f"confirmed on the air ({self._keyings()} PTT "
                                "keyings observed)"
                            )
                        else:
                            self.on_log(
                                f"VARA P2P: transfer #{msg.msg_id} did not "
                                f"finish on the degraded path "
                                f"({self._keyings()} PTT keyings observed, "
                                f"{self._socket_report()})"
                            )
                            self._abort_link()
                    else:
                        self.on_log(
                            f"VARA P2P: transfer #{msg.msg_id} failed before "
                            f"buffer drain ({result.value})"
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

    def _keyings(self) -> int:
        return int(getattr(self.vara.state, "ptt_keyings", 0) or 0)

    def _socket_report(self) -> str:
        """Describe the data socket VARA is supposed to be reading from."""
        state = self.vara.state
        probe = getattr(self.vara, "data_socket_alive", None)
        if probe is None:
            health = "unknown"
        else:
            try:
                health = "alive" if probe() else "CLOSED BY VARA"
            except Exception:  # noqa: BLE001
                health = "unknown"
        return (
            f"data socket {health}, generation "
            f"{getattr(state, 'data_socket_generation', '?')}, "
            f"{getattr(state, 'data_socket_reopens', 0)} reopens"
        )

    def _transport_lost(self) -> bool:
        return bool(getattr(self.vara.state, "transport_lost", False))

    def _wait_closed(self, timeout: float) -> bool:
        """Await DISCONNECTED, extending while VARA is still keying.

        VARA flushes its RF queue before honouring a graceful DISCONNECT, so a
        link that is still transmitting has not failed -- it is finishing the
        job.  Only a modem that has gone quiet is genuinely stuck.
        """
        try:
            return self.vara.wait_link(
                "DISCONNECTED",
                timeout,
                ptt_grace=PTT_QUIET_SECONDS,
                max_wait=timeout * 2,
            )
        except TypeError:
            # A VARA stand-in without the PTT-aware signature.
            return self.vara.wait_link("DISCONNECTED", timeout)

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
        read_before = int(getattr(self.vara.state, "data_bytes_read", 0) or 0)
        with self._transfer_lock:
            try:
                if self.on_acquire:
                    self.on_acquire()
                    acquired = True
                self.on_log(
                    "VARA P2P: using persistent TCP pair 8300/8301 "
                    f"(generation {self.vara.state.data_socket_generation})"
                )
                # LISTEN ON is established once when Guardian connects to
                # VARA. Reissuing it here can reach VARA while the inbound RF
                # handshake is already pending; the native protocol explicitly
                # says LISTEN ON/OFF during a connection causes disconnect.
                if not self.vara.wait_link("CONNECTED", CONNECT_TIMEOUT):
                    self.on_log("VARA P2P: no incoming link")
                else:
                    self.on_log("VARA P2P: link established, waiting for data header")
                    started = time.monotonic()
                    deadline = started + TRANSFER_TIMEOUT

                    def remaining() -> float:
                        return max(0.01, deadline - time.monotonic())

                    head = self.vara.read_exactly(_HDR.size, remaining())
                    magic, mid, length = _HDR.unpack(head)
                    if magic != _MAGIC:
                        raise ValueError("bad payload magic")
                    wire_size = max(
                        MIN_WIRE_SIZE, _HDR.size + length + _CRC.size
                    )
                    deadline = max(
                        deadline, started + transfer_timeout_for(wire_size)
                    )
                    body = self.vara.read_exactly(length, remaining())
                    crc_given = _CRC.unpack(
                        self.vara.read_exactly(_CRC.size, remaining())
                    )[0]
                    if crc_given != crc16(head + body):
                        raise ValueError(f"CRC failed on #{mid}")
                    padding_length = max(
                        0, MIN_WIRE_SIZE - (_HDR.size + length + _CRC.size)
                    )
                    if padding_length:
                        padding = self.vara.read_exactly(
                            padding_length, remaining()
                        )
                        if padding.strip(b"\0"):
                            raise ValueError(f"invalid payload padding on #{mid}")
                    msg.payload_bytes = body
                    msg.body = ""
                    self.on_log(
                        f"VARA P2P: payload #{mid} received OK ({length} bytes)"
                    )
                    success = True
            except Exception as exc:  # noqa: BLE001
                read_now = int(
                    getattr(self.vara.state, "data_bytes_read", 0) or 0
                )
                self.on_log(
                    f"VARA P2P receive failed: {exc} "
                    f"({read_now - read_before} payload bytes arrived, "
                    f"{self._keyings()} PTT keyings observed)"
                )
            finally:
                # The sender owns drain-before-disconnect ordering.  Do not
                # race its final BUFFER 0 by disconnecting from the responder.
                if success:
                    try:
                        if not self.vara.wait_link(
                            "DISCONNECTED", DISCONNECT_TIMEOUT
                        ):
                            self.on_log(
                                "VARA P2P: sender did not close the completed "
                                "link; aborting stale session"
                            )
                            self._abort_link()
                    except Exception:
                        self._abort_link()
                else:
                    self._abort_link()
                if acquired and self.on_release:
                    self._safe(self.on_release)
        done(success)
