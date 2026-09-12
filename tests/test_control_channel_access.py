"""Half-duplex regression: receive the next advertisement before relaying one."""
from collections import deque
from types import SimpleNamespace
import threading
import time

import numpy as np
import pytest

from guardian.modem.afsk import AFSKModem
from guardian.modem.audio import AudioControlTransport, _AfskAcquisition
from guardian.protocol import ControlFrame, FrameType


@pytest.mark.parametrize("gain", [0.0001, 0.2, 1.0])
@pytest.mark.parametrize("clock_error", [-0.01, 0.0, 0.01])
def test_acquisition_uses_modulation_not_absolute_audio_level(gain, clock_error):
    modem = AFSKModem()
    frame = ControlFrame(FrameType.BEACON, source="OK2IPW")
    waveform = modem.modulate(frame.encode())
    waveform = np.interp(np.arange(0, len(waveform) - 1, 1 + clock_error),
                         np.arange(len(waveform)), waveform)
    rng = np.random.default_rng(116)
    # Arbitrary phase relative to both symbol and callback boundaries; fixed
    # signal/noise ratio across four orders of magnitude of receive gain.
    waveform = np.concatenate((np.zeros(137), waveform))
    waveform = gain * (waveform + rng.normal(0, 0.01, len(waveform)))
    detector = _AfskAcquisition(modem)
    detections = []
    for offset in range(0, len(waveform), 512):
        block = waveform[offset:offset + 512]
        acquired = detector.observe(block, (offset + len(block)) / modem.fs)
        if acquired is not None:
            detections.append(acquired)
    assert detections
    assert detections[0] < 0.1  # Before the short preamble/sync have finished.


@pytest.mark.parametrize("kind", ["noise", "mark", "space", "hum"])
def test_noise_and_unmodulated_tones_do_not_reserve_a_control_burst(kind):
    modem = AFSKModem()
    detector = _AfskAcquisition(modem)
    rng = np.random.default_rng(116)
    for block_index in range(200):
        axis = (np.arange(1024) + block_index * 1024) / modem.fs
        if kind == "noise":
            samples = rng.normal(0, 0.1, len(axis))
        else:
            frequency = {"mark": 1200, "space": 2200, "hum": 50}[kind]
            samples = 0.4 * np.sin(2 * np.pi * frequency * axis)
        assert detector.observe(samples, axis[-1] + 1 / modem.fs) is None


def test_rx_remains_enabled_while_a_reply_owns_only_the_queue_lock():
    transport = AudioControlTransport()
    samples = transport.modem.modulate(ControlFrame(FrameType.BEACON, source="OK2IPW").encode())
    block = samples[:4800].reshape(-1, 1)
    with transport._tx_lock:
        transport._rx_callback(block, len(block), None, None)
    assert len(transport._rx_buf) == len(block)
    assert transport._acquisition_ready_at > 0
    transport._transmitting.set()
    transport._rx_callback(block, len(block), None, None)
    assert len(transport._rx_buf) == len(block)  # Actual TX still masks self audio.


def test_relay_waits_for_second_neighbor_frame_and_receives_it(monkeypatch):
    modem = AFSKModem()
    receiver = AudioControlTransport(modem=modem)
    first = ControlFrame(FrameType.LINK_ADVERT, source="OK2IPW", destination="OK2IPW",
                         next_hop="OK7PS", message_id=1864630277, ttl=4)
    second = ControlFrame(FrameType.LINK_ADVERT, source="OK2IPW", destination="OK2IPW",
                          next_hop="ZZ0TST", message_id=1864630277, ttl=4)
    relay = ControlFrame(FrameType.LINK_ADVERT, source="OK7PS", destination="OK2IPW",
                         next_hop="OK7PS", message_id=1864630277, ttl=3)

    def burst(frame):
        return np.concatenate((np.zeros(7200), modem.modulate(frame.encode()), np.zeros(31200)))

    first_audio, second_audio = burst(first), burst(second)
    remote_end = (len(first_audio) + len(second_audio)) / modem.fs
    remote = np.concatenate((first_audio, second_audio, np.zeros(48000)))
    blocks = deque(((offset + len(remote[offset:offset + 4800])) / modem.fs,
                    remote[offset:offset + 4800].reshape(-1, 1))
                   for offset in range(0, len(remote), 4800))
    clock = [0.0]
    decoded = []
    keyed_at = []

    def advance(target):
        while blocks and blocks[0][0] <= target:
            clock[0], block = blocks.popleft()
            receiver._rx_callback(block, len(block), None, None)
            window = np.array(receiver._rx_buf, dtype=np.float32)
            if not len(window):
                continue
            for payload in modem.demodulate(window, validator=receiver._is_valid_control_payload):
                if payload not in decoded:
                    decoded.append(payload)
                    receiver._handle_payload(payload)
        clock[0] = target

    class AirCondition(threading.Condition):
        def wait(self, timeout=None):
            end = clock[0] + timeout
            advance(min(end, blocks[0][0]) if blocks else end)

    class Output:
        def __init__(self, **kwargs):
            advance(clock[0] + 0.04)  # RX must continue while the endpoint opens.

        def start(self):
            pass

        def write(self, samples):
            advance(clock[0] + len(samples) / modem.fs)
            return False

        def stop(self, **kwargs):
            pass

        def close(self):
            pass

    receiver._tx_condition = AirCondition()
    receiver._sd = SimpleNamespace(OutputStream=Output)
    receiver.ptt = lambda on: keyed_at.append(clock[0]) if on else None
    monkeypatch.setattr("guardian.modem.audio.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("guardian.modem.audio.time.sleep", lambda seconds: advance(clock[0] + seconds))
    advance(1.8)  # First frame decoded; B's second neighbor is just starting.
    assert first.encode() in decoded
    assert second.encode() not in decoded
    receiver._tx(relay)

    assert second.encode() in decoded  # The 1.1.5 lock-based RX mask loses it.
    assert len(keyed_at) == 1
    assert remote_end <= keyed_at[0] < remote_end + 0.7


def _waiting_transport(monkeypatch):
    transport = AudioControlTransport()
    transport._sd = object()
    transport._peer_ready_at = time.monotonic() + 60
    waiting = threading.Event()
    played = []
    original_wait = transport._tx_condition.wait

    def wait(timeout=None):
        waiting.set()
        return original_wait(timeout)

    def transmit(sd, samples, **kwargs):
        kwargs["before_play"]()
        played.append(len(samples))
        kwargs["after_release"]()

    monkeypatch.setattr(transport._tx_condition, "wait", wait)
    monkeypatch.setattr("guardian.modem.audio.transmit_waveform", transmit)
    return transport, waiting, played


def test_cancel_busy_channel_requests_keeps_receipts_and_other_messages(monkeypatch):
    transport, waiting, played = _waiting_transport(monkeypatch)
    request = ControlFrame(FrameType.START_VARA, source="OK7PS", message_id=901)
    transport.send(request)
    assert waiting.wait(1)
    transport.send(ControlFrame(FrameType.RECEIVED, source="OK7PS", message_id=901))
    transport.send(ControlFrame(FrameType.HAVE_MSG, source="OK7PS", message_id=902))
    transport.discard_deferred_message(901)
    with transport._tx_condition:
        transport._peer_ready_at = 0
        transport._tx_condition.notify_all()
    assert transport.wait_tx_idle(1)
    assert len(played) == 2
    assert not transport._pending_frames and not transport._cancelled_tx


def test_stop_then_immediate_restart_cancels_waiting_frames_and_morse(monkeypatch):
    transport, waiting, played = _waiting_transport(monkeypatch)
    transport.send(ControlFrame(FrameType.START_VARA, source="OK7PS", message_id=901))
    assert waiting.wait(1)
    transport.send_morse_after_pending("OK7PS")
    with transport._tx_condition:
        transport.stop()
        # start() resets this flag. Holding the condition here reproduces a
        # restart winning the race before either old worker observes stop.
        transport._stopped = False
        transport._peer_ready_at = 0
    assert transport.wait_tx_idle(1)
    assert played == []
    assert transport._post_tx_pending == 0
    assert not transport._pending_frames and not transport._cancelled_tx
