"""Exercise real native audio, ARQ retransmission and independent modem state."""
import queue
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from guardian.ardop import Engine
from guardian.modem.ardop import ArdopControlModem
from guardian.payload.ardop import ArdopBackend, envelope, decode_envelope
from guardian.config import StationConfig
from guardian.session import LoopbackBus, Orchestrator, SessionState
from guardian.protocol import ControlFrame, FrameType
from guardian.modem.audio import AudioControlTransport


def carry(tx, rx, *, lose=False):
    wave = tx.waveform()
    tx.finish_transmit(len(wave))
    wave = np.concatenate((np.zeros_like(wave) if lose else wave,
                           np.zeros(24000, dtype=np.int16)))
    for i in range(0, len(wave), 960):
        rx.receive(wave[i:i+960])


@pytest.mark.parametrize('lose_first', [False, True])
def test_native_arq_500_retransmits_and_preserves_binary_payload(lose_first):
    payload = bytes(range(256)) * 16
    with Engine('N0AAA') as a, Engine('N0BBB') as b:
        a.command(3, data=payload)
        a.command(0, target='N0BBB')
        data = bytearray()
        modes = set()
        for turn in range(250):
            for tx, rx in ((a,b), (b,a)):
                if tx.transmitting:
                    if tx is a:
                        modes.add(a.status(7) & 0xfe)
                    carry(tx, rx, lose=lose_first and turn == 0 and tx is a)
            data.extend(b.read())
            if bytes(data) == payload and a.queued == 0:
                break
            if not a.transmitting and not b.transmitting:
                for _ in range(50):
                    a.receive(np.zeros(960, dtype=np.int16))
                    b.receive(np.zeros(960, dtype=np.int16))
        assert bytes(data) == payload
        assert a.queued == 0
        assert a.status(3) == b.status(3) == 500
        # Selection is quality driven; this resampled channel reaches 8PSK.
        # The top 16QAM PHY is exercised independently below.
        assert {0x48, 0x42, 0x40, 0x50, 0x52} <= modes
        a.command(1)
        for _ in range(30):
            for tx, rx in ((a,b), (b,a)):
                if tx.transmitting:
                    carry(tx, rx)
            if a.state == b.state == 0:
                break
        assert a.state == b.state == 0


def test_narrow_control_roundtrip_at_unknown_offset_and_with_noise():
    modem = ArdopControlModem()
    data = bytes(range(48))
    wave = modem.modulate(data)
    rng = np.random.default_rng(111)
    samples = np.concatenate((np.zeros(7132), wave, np.zeros(48000)))
    samples += rng.normal(0, 0.002, len(samples))
    assert modem.demodulate(samples, validator=lambda p: p == data) == [data]
    assert abs(len(wave) / 48000 - modem.airtime(48)) < 0.05


def test_control_waveform_stays_inside_500_hz_channel():
    modem = ArdopControlModem()
    wave = modem.modulate(bytes(range(48)))
    power = np.abs(np.fft.rfft(wave)) ** 2
    frequencies = np.fft.rfftfreq(len(wave), 1 / modem.fs)
    occupied = power[(frequencies >= 1250) & (frequencies <= 1750)].sum()
    assert occupied / power.sum() > 0.999


@pytest.mark.parametrize('offset', [7132, 48000 * 7 + 193])
def test_real_control_transport_receives_ardop_through_rolling_audio(offset):
    modem = ArdopControlModem()
    frame = ControlFrame(FrameType.START_VARA, source='N0AAA',
                         destination='N0BBB', message_id=42)
    transport = AudioControlTransport(modem=modem)
    received = []
    transport.on_frame = received.append
    samples = np.concatenate((np.zeros(offset), modem.modulate(frame.encode()),
                              np.zeros(48000 * 2))).astype(np.float32)

    class CaptureClock:
        """Feed genuine callback blocks between each receive-worker poll."""
        position = 0

        def wait(self, seconds):
            if self.position >= len(samples):
                return True
            end = min(len(samples), self.position + round(seconds * modem.fs))
            while self.position < end:
                stop = min(end, self.position + 960)
                block = samples[self.position:stop].reshape(-1, 1)
                transport._rx_callback(block, len(block), None, None)
                self.position = stop
            return False

        def is_set(self):
            return False

    assert transport.rx_window > modem.airtime(len(frame.encode())) + transport.poll_interval
    transport._rx_loop(CaptureClock())
    assert transport.pump() == 1
    assert received == [frame]


@pytest.mark.parametrize('frame_type,size', [(0x48,16), (0x42,16), (0x40,64),
                                           (0x50,128), (0x52,216), (0x54,256)])
def test_every_500_hz_ladder_modulation_decodes(frame_type, size):
    data = bytes(range(size))
    with Engine(receive_only=True) as a, Engine(receive_only=True) as b:
        a.fec(data, frame_type=frame_type)
        carry(a, b)
        assert b.read() == data


def test_envelope_boundaries_and_corruption():
    wire = envelope(42, b'\x00\xffpayload')
    assert decode_envelope(wire[:-1], 42) is None
    assert decode_envelope(wire, 42) == b'\x00\xffpayload'
    with pytest.raises(ValueError): decode_envelope(wire, 43)
    with pytest.raises(ValueError): decode_envelope(wire[:-1] + b'X', 42)
    assert decode_envelope(envelope(42, b''), 42) == b''


def test_engine_rejects_invalid_buffers_and_closed_handles():
    with Engine() as engine:
        with pytest.raises(ValueError): engine.receive(np.zeros(961))
        with pytest.raises(ValueError): engine.command(3, data=b'x' * 16385)
        with pytest.raises(ValueError): engine.fec(b'x' * 65)
    with pytest.raises(RuntimeError): engine.status(0)


def test_dual_radio_preserves_independent_modem_choice():
    cfg = StationConfig(payload_backend='ardop', control_modem='afsk1200')
    cfg.second_radio = {'payload_backend': 'ofdm_vhf', 'control_modem': 'mfsk16'}
    assert cfg.active_modem() == 'ardop500'
    assert cfg.second_radio_config().active_modem() == 'mfsk16'


@pytest.mark.parametrize('remote_token', ['A500', 'G1T2', None])
def test_profile_agreement_requires_ardop_on_both_ends(remote_token):
    wire = []
    bus = LoopbackBus(monitor=lambda sender, frame: wire.append(frame))
    a = Orchestrator('N0AAA', bus.endpoint('a'), auto_route=False)
    b = Orchestrator('N0BBB', bus.endpoint('b'), auto_route=False, auto_complete=True)
    a.ofdm_payload_request = lambda: True
    a.g2_profile = lambda: 'A500'
    b.ofdm_payload_request = lambda: remote_token is not None
    b.g2_profile = lambda: remote_token
    msg = a.send_message('N0BBB', 'hello', 111, next_hop='N0BBB')
    for _ in range(20):
        delivered = bus.pump()
        a.tick(1.0); b.tick(1.0)
        if delivered == 0 and bus.idle:
            break
    if remote_token == 'A500':
        assert msg.state is SessionState.DELIVERED
        assert msg.payload_transport == b.sessions[111].payload_transport == 'ardop'
    else:
        assert msg.state.terminal
        assert not any(frame.type is FrameType.START_VARA for frame in wire)


class SimulatedAudio:
    def __init__(self, engine):
        self.engine = engine
        self.rx = queue.Queue()
        self.peer = None
        self.deadline = time.monotonic() + 10

    def step(self):
        if time.monotonic() > self.deadline:
            raise RuntimeError('Simulated audio deadline exceeded')
        if self.engine.transmitting:
            wave = self.engine.waveform()
            self.engine.finish_transmit(len(wave))
            for i in range(0, len(wave), 960):
                self.peer.rx.put(wave[i:i+960])
            for _ in range(25): self.peer.rx.put(np.zeros(960, dtype=np.int16))
        else:
            try:
                pcm = self.rx.get(timeout=0.001)
            except queue.Empty:
                pcm = np.zeros(960, dtype=np.int16)
            self.engine.receive(pcm)


def test_payload_ack_turnover_and_disconnect_between_two_native_instances():
    payload = bytes(range(256)) * 3
    tx_msg = SimpleNamespace(msg_id=42, next_hop='N0BBB', payload_bytes=payload, body='')
    rx_msg = SimpleNamespace(msg_id=42, source='N0AAA', payload_bytes=None, body='')
    results, errors = [], []
    with Engine('N0AAA') as a, Engine('N0BBB') as b:
        aa, bb = SimulatedAudio(a), SimulatedAudio(b)
        aa.peer, bb.peer = bb, aa
        def run(engine, audio, msg, sending):
            try:
                results.append(ArdopBackend()._exchange(engine, audio, msg, sending))
            except Exception as exc:
                errors.append(exc)
        workers = [threading.Thread(target=run, args=args, daemon=True) for args in
                   ((a, aa, tx_msg, True), (b, bb, rx_msg, False))]
        for worker in workers: worker.start()
        for worker in workers: worker.join(15)
        # Do not free native handles while a failed worker still owns one.
        if any(worker.is_alive() for worker in workers):
            pytest.fail('Native exchange did not terminate within 30 seconds')
    assert not errors
    assert results == [True, True]
    assert rx_msg.payload_bytes == payload
