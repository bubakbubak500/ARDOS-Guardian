"""Short ARQ replies must survive the real half-duplex radio release time."""
import numpy as np

from guardian.ardop import Engine
from guardian.ardop.audio import AudioSession


def _carry(tx, rx):
    wave = tx.waveform()
    tx.finish_transmit(len(wave))
    pcm = np.concatenate((wave, np.zeros(24000, dtype=np.int16)))
    for offset in range(0, len(pcm), 960):
        rx.receive(pcm[offset:offset + 960])


def test_short_disconnect_reply_waits_for_peer_audio_drain(monkeypatch):
    with Engine('N0AAA') as a, Engine('N0BBB') as b:
        a.command(3, data=b'hello')
        a.command(0, target='N0BBB')
        received = bytearray()
        for _ in range(50):
            for tx, rx in ((a, b), (b, a)):
                if tx.transmitting:
                    _carry(tx, rx)
            received.extend(b.read())
            if received == b'hello' and a.queued == 0:
                break
        assert received == b'hello'
        assert b.state == 5
        a.command(1)

        # At t=0 the peer's waveform ends, but its radio remains deaf during
        # the 250 ms output guard and 50 ms PTT tail. With the old immediate
        # reply + 150 ms lead, it lost 150 ms of the short DISC leader.
        clock = [0.0]
        keyed = []
        monkeypatch.setattr('guardian.ardop.audio.time.sleep',
                            lambda seconds: clock.__setitem__(0, clock[0] + seconds))

        def transmit(sd, samples, **kwargs):
            kwargs['ptt'](True)
            lost = max(0, round((0.3 - clock[0] - kwargs['lead_seconds']) * 48000))
            pcm = np.rint(samples[lost:] * 32768).astype(np.int16)
            pcm = np.concatenate((pcm, np.zeros(24000, dtype=np.int16)))
            for offset in range(0, len(pcm), 960):
                b.receive(pcm[offset:offset + 960])
            kwargs['ptt'](False)

        monkeypatch.setattr('guardian.ardop.audio.transmit_waveform', transmit)
        audio = AudioSession(a, ptt=lambda value: keyed.append((value, clock[0])))
        audio.sd = object()
        audio.out_device = 0
        audio.step()
        assert b.state == 0  # Actual native DISC decoded, not just a timing check.
        assert b.transmitting  # END is ready to return.
        assert keyed[0][0] is True and keyed[0][1] >= 0.3
        assert keyed[-1][0] is False
