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


@pytest.mark.parametrize("failure", ["waiting", "keying", "starting"])
def test_failure_before_playback_stops_and_closes_without_masking_error(failure):
    events = []

    def fail(stage):
        if failure == stage:
            raise OSError(f"failed while {stage}")

    class Stream:
        def start(self):
            fail("starting")

        def stop(self, *, ignore_errors):
            events.append("stop")
            # PortAudio reports paStreamIsStopped for an unopened playback
            # phase; that status must not hide the original cancellation/PTT
            # failure. Already-started streams are covered by the drain test.
            assert ignore_errors is True

        def close(self):
            events.append("close")

    def ptt(on):
        events.append("key" if on else "release")
        if on:
            fail("keying")

    with pytest.raises(OSError, match=f"failed while {failure}"):
        transmit_waveform(
            SimpleNamespace(OutputStream=lambda **kwargs: Stream()), np.ones(32),
            device=1, sample_rate=48000, ptt=ptt,
            before_play=lambda: fail("waiting"),
            lead_seconds=0, tail_seconds=0, guard_seconds=0,
        )
    assert events[-3:] == ["stop", "release", "close"]
