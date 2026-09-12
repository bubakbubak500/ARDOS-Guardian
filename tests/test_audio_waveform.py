from types import SimpleNamespace

import numpy as np
import pytest

from guardian.modem.audio import transmit_waveform


@pytest.mark.parametrize("fail_write", [False, True])
def test_output_is_opened_before_ptt_but_starts_after_lead(monkeypatch, fail_write):
    events = []
    clock = [0.0]

    class Stream:
        def __init__(self, **kwargs):
            events.append("open")
            self.started = None

        def start(self):
            events.append("start")
            self.started = clock[0]

        def write(self, samples):
            events.append("write")
            # Match PortAudio's reported underflow since the previous write:
            # an output stream started before the PTT delay is already starved.
            return fail_write or clock[0] - self.started > 0.01

        def stop(self, **kwargs):
            events.append("drain")

        def close(self):
            events.append("close")

    def sleep(seconds):
        clock[0] += seconds
        events.append("sleep")

    monkeypatch.setattr("guardian.modem.audio.time.sleep", sleep)

    def transmit():
        return transmit_waveform(
            SimpleNamespace(OutputStream=Stream), np.ones(480),
            device=1, sample_rate=48000,
            ptt=lambda on: events.append("key" if on else "release"),
            lead_seconds=0.15, tail_seconds=0.25, guard_seconds=0.4,
        )

    if fail_write:
        with pytest.raises(RuntimeError, match="underflow"):
            transmit()
    else:
        assert transmit() == 0.41
    assert events == ["open", "key", "sleep", "start", "write", "drain",
                      "sleep", "release", "close"]
