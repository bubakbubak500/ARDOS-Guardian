"""500 Hz ARDOP ARQ payload transport with bounded framing and end-to-end ACK."""
import hashlib
import struct
import threading
import time

from ..ardop import Engine
from ..ardop.audio import AudioSession
from .base import PayloadBackend

HEADER = struct.Struct('>4sII16s')
MAX_PAYLOAD = 16 * 1024 * 1024

def envelope(msg_id, body):
    if len(body) > MAX_PAYLOAD:
        raise ValueError('ARDOP payload exceeds 16 MiB')
    return HEADER.pack(b'GAR1', msg_id, len(body), hashlib.sha256(body).digest()[:16]) + body

def decode_envelope(data, msg_id):
    if len(data) < HEADER.size:
        return None
    magic, received_id, size, digest = HEADER.unpack_from(data)
    if magic != b'GAR1' or received_id != msg_id or size > MAX_PAYLOAD:
        raise ValueError('Invalid ARDOP payload header')
    if len(data) < HEADER.size + size:
        return None
    body = bytes(data[HEADER.size:])
    if len(body) != size or hashlib.sha256(body).digest()[:16] != digest:
        raise ValueError('ARDOP payload length or checksum mismatch')
    return body

class ArdopBackend(PayloadBackend):
    name = 'ardop'

    def __init__(self, *, callsign='N0CALL', on_log=None, on_qsy=None,
                 on_receive_qsy=None, on_unqsy=None, on_acquire=None,
                 on_release=None, audio_input=None, audio_output=None,
                 ptt=None, ptt_turnaround_ms=0, ardop_tx_scale=1.0, **unused):
        self.callsign = callsign
        self.on_log = on_log or (lambda text: None)
        self.on_qsy, self.on_receive_qsy = on_qsy, on_receive_qsy
        self.on_unqsy = on_unqsy
        self.on_acquire, self.on_release = on_acquire, on_release
        self.audio_args = dict(audio_input=audio_input, audio_output=audio_output,
                               ptt=ptt, turnaround_ms=ptt_turnaround_ms,
                               tx_scale=ardop_tx_scale)
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self._active_id = None
        self._worker = None

    def start_send(self, msg, done): self._start(msg, done, True)
    def start_receive(self, msg, done): self._start(msg, done, False)

    def set_ptt_turnaround_ms(self, delay_ms):
        self.audio_args['turnaround_ms'] = max(0, int(delay_ms))

    def _start(self, msg, done, sending):
        with self._lock:
            if self._active_id is not None:
                raise RuntimeError('ARDOP radio already has an active transfer')
            self._active_id = msg.msg_id
            self._cancelled.clear()
        self._worker = threading.Thread(target=self._run, args=(msg, done, sending), daemon=True)
        self._worker.start()

    def shutdown(self):
        self._cancelled.set()
        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(12)

    def cancel(self, msg):
        with self._lock:
            if self._active_id == msg.msg_id:
                self._cancelled.set()

    def _run(self, msg, done, sending):
        acquired = success = False
        try:
            if self._cancelled.is_set():
                raise RuntimeError('ARDOP cancelled')
            # Validate the native library and callsign before taking the radio.
            with Engine(self.callsign) as engine:
                if self.on_acquire:
                    self.on_acquire()
                    acquired = True
                qsy = self.on_qsy if sending else self.on_receive_qsy
                if qsy and qsy(msg) is False:
                    raise RuntimeError('ARDOP working-channel QSY failed')
                with AudioSession(engine, cancelled=self._cancelled, **self.audio_args) as audio:
                    self.on_log(f'ARDOP 500 Hz: {"send" if sending else "receive"} #{msg.msg_id}')
                    success = self._exchange(engine, audio, msg, sending)
        except Exception as exc:
            self.on_log(f'ARDOP transfer failed: {exc}')
        finally:
            for hook in (self.on_unqsy, self.on_release if acquired else None):
                if hook:
                    try:
                        hook()
                    except Exception as exc:
                        success = False
                        self.on_log(f'ARDOP radio handoff failed: {exc}')
            with self._lock:
                self._active_id = None
        done(success)

    def _exchange(self, engine, audio, msg, sending):
        peer = (msg.next_hop if sending else msg.source).upper()
        body = (msg.payload_bytes if msg.payload_bytes is not None else msg.body.encode('utf-8')) if sending else b''
        wire = envelope(msg.msg_id, body) if sending else b''
        ack = b'GACK' + struct.pack('>I', msg.msg_id)
        received = bytearray()
        offset = 0
        connected = False
        complete = False
        disconnecting = False
        last_mode = None
        start = time.monotonic()
        deadline = start + (max(240, len(wire) * 8 / 41 * 4 + 180) if sending else 240)
        if sending:
            engine.command(0, target=peer)
        while time.monotonic() < deadline:
            audio.step()
            mode = engine.status(7) & 0xfe
            if sending and mode != last_mode and mode in (0x48, 0x42, 0x40, 0x50, 0x52, 0x54):
                label = {0x48: '4FSK 200 Hz', 0x42: '4PSK 200 Hz short',
                         0x40: '4PSK 200 Hz', 0x50: '4PSK 500 Hz',
                         0x52: '8PSK 500 Hz', 0x54: '16QAM 500 Hz'}[mode]
                self.on_log(f'ARDOP: {label}, confirmed {max(0, offset - engine.queued)} bytes')
                last_mode = mode
            if engine.remote and engine.remote.removesuffix('-0') != peer.removesuffix('-0'):
                engine.command(2)
                raise RuntimeError('Unexpected ARDOP peer')
            connected |= engine.state in (2, 3, 5, 6, 7, 8)
            chunk = engine.read()
            if chunk:
                received.extend(chunk)
                if len(received) > MAX_PAYLOAD + HEADER.size:
                    raise ValueError('ARDOP receive limit exceeded')
            if sending:
                if offset < len(wire) and engine.queued < 8192:
                    block = wire[offset:offset + 4096]
                    engine.command(3, data=block)
                    offset += len(block)
                msg.payload_progress_bytes = max(0, offset - engine.queued - HEADER.size)
                if received:
                    if not ack.startswith(received):
                        raise ValueError('Invalid ARDOP completion acknowledgement')
                    complete = received == ack
                if complete and not disconnecting and not engine.transmitting:
                    engine.command(1)
                    disconnecting = True
                    deadline = time.monotonic() + 60
            elif not complete and chunk:
                if len(received) >= HEADER.size:
                    size = HEADER.unpack_from(received)[2]
                    msg.payload_wire_size = size + HEADER.size
                    deadline = start + max(240, size * 8 / 41 * 4 + 180)
                decoded = decode_envelope(received, msg.msg_id)
                msg.payload_progress_bytes = max(0, len(received) - HEADER.size)
                if decoded is not None:
                    msg.payload_bytes, msg.body = decoded, ''
                    engine.command(3, data=ack)
                    engine.command(7)  # IRS requests the link to return the ACK.
                    complete = True
                    deadline = time.monotonic() + 120
            if connected and engine.state == 0 and not engine.transmitting:
                return complete and (sending or engine.queued == 0)
        engine.command(2)
        return False
