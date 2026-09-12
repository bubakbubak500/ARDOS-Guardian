"""Guardian OFDM VHF payload backend — experimental.

Moves a message payload with Guardian's own OFDM modem over the soundcard and
Guardian's own PTT, with no VARA involved. The DSP lives in `guardian.ofdm` and
knows nothing about radios; this module is the single bridge between the two, and
`RadioAudioPipe` below is the only class in the whole feature that touches
hardware.

The lifecycle deliberately mirrors `vara_p2p.py` hook for hook, including the
order things happen in and the fact that `done(ok)` runs *after* the codec has
gone back to the control modem. That is not tidiness: `done()` may immediately
key the radio to send a RECEIVED frame over AFSK, and if the payload transport
still owned the soundcard, that frame would never go out.

**Status of over-the-air ARQ.** The first two-radio exchange happened on
2026-08-09 (two IC-705s, OK7PS and OK2IPW) and messages moved. It also found two
faults that a simulated pipe structurally cannot: the receive squelch could be
deafened by the station's own transmission, because only a real radio produces
the AGC recovery and carrier drop that seeded its noise floor; and a lost final
acknowledgement turned a delivered message into a reported failure, because the
simulated pipe never lost one. Both are fixed here and in `guardian.ofdm.link`,
and both now have tests. `docs/OFDM_AIR_RESULTS_2026-08-09.md` has the analysis.
"""

from __future__ import annotations

import json
import threading
import time
import wave
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

import numpy as np

from ..config import G2_MAX_TX_SCALE
from ..modem.audio import (PTT_LEAD_SECONDS, PTT_TAIL_SECONDS,
                           _import_sounddevice, resolve_device,
                           transmit_waveform)
from ..ofdm import OfdmLink, OfdmStatus, PhyHeader
from ..ofdm.adaptation import AdaptationConfig, LinkAdaptationController
from ..ofdm.automatic import automatic_g2_policy
from ..ofdm.coding import FecProfile, fec_profile, fec_spec
from ..ofdm.framing import OfdmFrameType
from ..waveforms.config import profile_for as experimental_profile_for
from ..waveforms.framing import ExperimentalBurstCodec
from ..timing import RxTiming, TxTiming
from .base import DoneCb, PayloadBackend

#: Extra silence appended after a burst, for the same reason the control modem
#: appends its own: stopping the output stream discards whatever the host API and
#: a USB radio still hold buffered, and on air that cost a measured ~130 ms off
#: the end of every control burst. Here the loss would be the tail of the data
#: section, so the guard is sized from the same measurement.
# 130 ms was the measured PortAudio/USB truncation.  A quarter second keeps a
# useful safety margin without leaving the carrier keyed as silence for longer
# than the receiver's burst hangover.  The latter matters after a NACK: the far
# end must not mistake this silence for the end of our transmission and key its
# sparse retry while this radio is still changing back to receive.
TX_GUARD_SECONDS = 0.25

# Extra real-radio settling after the far end's carrier has actually dropped.
# This is deliberately small; the local PTT lead supplies another 150--300 ms
# before useful symbols can reach the air.
TURNAROUND_MARGIN_SECONDS = 0.05

# A local two-radio lab has two receivers in one process. Both can reject an
# audio capture at the same instant (DATA on one side, bitmap on the other), so
# writing the shared operator diagnostic must be serialized and atomic.
_REJECTED_AUDIO_WRITE_LOCK = threading.Lock()

#: How far above the tracked noise floor the receive level must rise before a
#: burst is believed to have started. The modem's own detector decides whether it
#: really was one; this only decides when to stop waiting and start decoding, so
#: it is deliberately generous -- a missed trigger costs a whole exchange, while a
#: false one costs a few milliseconds of Viterbi.
SQUELCH_ABOVE_FLOOR = 3.0

# FM receivers invert the usual audio-energy assumption.  Idle discriminator
# noise can be much louder than a correctly deviated digital signal, so keying
# the remote carrier first makes the USB audio level fall.  Treat that clean,
# large drop as a burst edge too.  The return threshold is deliberately nearer
# the original floor: once FM noise comes back the peer has physically unkeyed,
# and that noisy block must not be handed to the synchroniser as though it were
# part of the quieter burst.
# A 6 dB drop is already a decisive carrier-on edge when RMS is measured over
# 50 ms (thousands of samples).  Requiring the original 12 dB made detection
# depend on the exact RF level, squelch setting and radio direction: the weaker
# side of an otherwise clean link could decode audio perfectly once captured,
# yet never open the capture gate at all.
SQUELCH_BELOW_FLOOR = 0.50
SQUELCH_RETURN_TO_FLOOR = 0.75

#: How much quiet *audio* must follow a burst before it is considered finished.
HANGOVER_SECONDS = 0.35

#: How long the input stream may deliver nothing before a burst in progress is
#: treated as over.
#:
#: The hangover above is counted in samples, which is the right measure while
#: audio keeps arriving. If the stream stops instead -- a closed device, a finite
#: recording -- no further samples will ever arrive to satisfy it, and this is the
#: backstop. It is far longer than the hangover on purpose: a live stream that has
#: gone quiet for a whole second is broken, whereas one that merely lost the GIL to
#: a Viterbi decode for a few hundred milliseconds is ordinary, and cutting a burst
#: short there would throw away a transfer that was arriving perfectly well.
STREAM_STALL_SECONDS = 1.0

#: Seconds of audio kept before the trigger fires. A burst is detected a little
#: way into its own preamble, and the preamble is what synchronisation needs, so
#: the buffer has to reach back before the moment the level rose.
PRETRIGGER_SECONDS = 0.5

#: How much audio the noise floor must be measured over before the squelch is
#: allowed to fire.
#:
#: Without it the very first block always triggers: the floor starts at zero, so
#: anything at all is above it, and the receiver hands back a window of silence.
#: A payload receive is always preceded by this station transmitting, so there is
#: always at least a PTT turnaround of quiet before any answer can arrive -- three
#: blocks is comfortably inside that and still far short of the turnaround.
FLOOR_SETTLE_SECONDS = 0.15

#: The window the receive level is measured over.
#:
#: Fixed, rather than however much audio a poll happened to collect. A Viterbi
#: decode on another thread can hold the GIL long enough for several stream
#: callbacks to queue up, and averaging a short burst together with the seconds of
#: silence that followed it puts the level below the squelch -- so a quarter-second
#: acknowledgement would simply not be noticed. Sizing the analysis independently
#: of the polling makes detection behave the same however the scheduler behaves.
ANALYSIS_BLOCK_SECONDS = 0.05

# Acquisition is retried while a trigger stays loud, because the real frame can
# follow an AGC/carrier-drop transient. Keep the DSP input bounded so a long
# continuous interferer cannot turn those retries into quadratic work.
PROBE_WINDOW_SECONDS = 4.0

# Once the robust header/manifest gives an exact frame length, retain only this
# small amount of following audio.  It covers callback/block quantisation; radio
# direction turnaround is tracked separately as an absolute ready time.
POST_FRAME_SECONDS = ANALYSIS_BLOCK_SECONDS

# Some radios expose their USB codec ahead of a high-gain DATA modulation
# control.  Five percent full scale already hard-limits an IC-705 in that
# configuration, so the transport must preserve calibration values below the
# old 0.05 floor.  A tenth of one percent is the calibration floor, not a
# recommended operating point; keeping it available lets the lab actually find
# the linear region instead of pinning a sensitive radio to an artificial UI
# limit.
MIN_TX_SCALE = 0.001


def _swallow(action: Callable[[], None]) -> None:
    """Run a caller-supplied hook, ignoring anything it raises.

    Same contract as `VaraP2PBackend._safe`: these run in `finally` blocks whose
    job is to give the radio and the codec back. A hook that raises there would
    replace a recoverable transfer failure with a station that is still keyed.
    """
    try:
        action()
    except Exception:  # noqa: BLE001 - deliberately total
        pass


class RadioAudioPipe:
    """A half-duplex pipe over a real soundcard and a real transmitter.

    Satisfies `guardian.ofdm.link.HalfDuplexPipe`, so the ARQ state machine
    cannot tell it apart from the simulated one.

    Device resolution goes through `modem.audio.resolve_device`, and sounddevice
    is imported through `modem.audio._import_sounddevice`: a bare
    `import sounddevice` picks its bundled PortAudio DLL from the *machine's*
    architecture rather than the process's, which made every audio device vanish
    on Windows-on-ARM.
    """

    def __init__(self, profile, *, input_device, output_device,
                 ptt: Callable[[bool], None],
                 tx_lead_ms: int = 300, tx_tail_ms: int = 100,
                 tx_guard_ms: int = 250, exact_ptt_timing: bool = False,
                 tx_scale: float = 1.0,
                 tx_write_chunk_ms: int = 0,
                 train_bursts: int = 1, train_gap_ms: int = 30,
                 max_burst_bytes: int = 8192, arq_block_bytes: int = 512,
                 on_log: Callable[[str], None] | None = None,
                 codec=None) -> None:
        self.profile = profile
        self.input_device = input_device
        self.output_device = output_device
        self.ptt = ptt
        self.tx_lead = max(0.0, tx_lead_ms / 1000.0)
        self.tx_guard = max(0.0, tx_guard_ms / 1000.0)
        self.tx_tail = max(0.0, tx_tail_ms / 1000.0)
        self.exact_ptt_timing = bool(exact_ptt_timing)
        self.tx_scale = min(G2_MAX_TX_SCALE, max(MIN_TX_SCALE, float(tx_scale)))
        self.tx_write_chunk_frames = max(
            0, round(int(tx_write_chunk_ms) * self.profile.sample_rate / 1000)
        )
        self.train_bursts = max(1, min(8, int(train_bursts)))
        self.train_gap = max(0.01, min(0.20, int(train_gap_ms) / 1000.0))
        self.max_burst_bytes = max(1, int(max_burst_bytes))
        self.arq_block_bytes = max(1, int(arq_block_bytes))
        self.on_log = on_log or (lambda message: None)
        # This port has one native payload waveform.  Keeping the codec
        # explicit prevents a bare RadioAudioPipe from silently selecting the
        # retired OFDM implementation.
        self.codec = codec or ExperimentalBurstCodec()

        self._sd = None
        self._stream = None
        # Guards the transmitter: half duplex means never keying while the
        # receive side is being read, and never two transmissions at once.
        self._tx_lock = threading.Lock()
        self._buffer_lock = threading.Lock()
        self._buffer: list[np.ndarray] = []
        self._floor = 0.0
        self._floor_seen = 0.0
        self._floor_stable_seen = 0.0
        self._stopped = threading.Event()
        self._stopped.set()
        self._receive_interrupted = threading.Event()
        # ``receive`` returns after HANGOVER_SECONDS of quiet, while the peer
        # keeps PTT asserted for its anti-truncation guard and tail.  Publish the
        # remainder to the ARQ layer so *every* ACK/NACK (including a NACK before
        # a very short retry) waits until the peer is genuinely back in RX.
        keyed_quiet = self.tx_guard + self._tail_seconds()
        self._peer_keyed_quiet = keyed_quiet
        self.peer_turnaround_guard = max(
            TURNAROUND_MARGIN_SECONDS,
            keyed_quiet - HANGOVER_SECONDS + TURNAROUND_MARGIN_SECONDS,
        )
        self._peer_ready_at = 0.0
        self.last_rx_framed = False
        self.last_rx_timing: RxTiming | None = None
        self.last_capture_timing: RxTiming | None = None
        self.last_rejected_ofdm: dict | None = None
        self.last_rx_debug: dict | None = None
        self.rx_debug_history: list[dict] = []
        self._last_diagnostic_audio: float | None = None
        self._diagnostic_thread: threading.Thread | None = None
        self.input_status_events = 0
        self.last_input_status = ""

    def _lead_seconds(self) -> float:
        return (self.tx_lead if self.exact_ptt_timing
                else max(self.tx_lead, PTT_LEAD_SECONDS))

    def _tail_seconds(self) -> float:
        return (self.tx_tail if self.exact_ptt_timing
                else max(self.tx_tail, PTT_TAIL_SECONDS))

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Open the receive stream. Raises if the configured devices are not usable."""
        self._sd = _import_sounddevice()
        if not isinstance(self.input_device, int):
            raise RuntimeError("The configured RX audio input is not available")
        if not isinstance(self.output_device, int):
            raise RuntimeError("The configured TX audio output is not available")
        rate = self.profile.sample_rate
        self._sd.check_input_settings(device=self.input_device, samplerate=rate,
                                      channels=1)
        self._sd.check_output_settings(device=self.output_device, samplerate=rate,
                                       channels=1)
        self._stream = self._sd.InputStream(
            samplerate=rate, channels=1, device=self.input_device,
            blocksize=int(rate * 0.05), callback=self._on_audio,
        )
        self._stream.start()
        self._stopped.clear()
        self.on_log(f"OFDM VHF: listening at {rate} Hz on device {self.input_device}")

    def stop(self) -> None:
        """Close the receive stream. Never raises — it runs in `finally` blocks."""
        self._stopped.set()
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001 - closing is best-effort
                pass

    # -- receive ------------------------------------------------------------

    def _on_audio(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        """PortAudio callback. Does the minimum and returns."""
        if status:
            self.input_status_events += 1
            self.last_input_status = str(status)
        if self._tx_lock.locked():
            # Never splice audio from before and after our own transmission into
            # one buffer; the control modem drops its receive window for the
            # same reason.
            return
        with self._buffer_lock:
            self._buffer.append(np.asarray(indata[:, 0], dtype=np.float64).copy())

    def _drain(self) -> np.ndarray:
        with self._buffer_lock:
            chunks, self._buffer = self._buffer, []
        return np.concatenate(chunks) if chunks else np.zeros(0)

    def receive_limited(self, timeout: float,
                        max_capture_seconds: float) -> np.ndarray | None:
        """Receive a short control reply with a hard post-trigger bound."""
        return self.receive(
            timeout, max_capture_seconds=max_capture_seconds
        )

    def interrupt_receive(self) -> None:
        """Wake one pending receive without closing the reusable audio stream."""
        self._receive_interrupted.set()

    def receive(self, timeout: float, *,
                max_capture_seconds: float | None = None) -> np.ndarray | None:
        """Wait for a burst and return the audio around it, or None on timeout.

        Waits for the level to rise, then keeps collecting until it has been down
        for `HANGOVER_SECONDS` of audio or the longest burst the profile can
        produce has gone by. Deciding whether what arrived really was a burst is
        the modem's job, not this one's; all this has to get right is handing over
        a window that contains the whole thing.

        The hangover is counted in *samples*, not wall-clock. A live input stream
        never stops delivering, so a wall-clock check that only ran when the audio
        stalled would never fire on air, and every receive -- including a
        quarter-second acknowledgement -- would wait out the longest burst the
        profile allows. That is around five seconds on BENCH, which is longer than
        the ARQ gives an answer, so the link would have stalled on every block.
        """
        rate = self.profile.sample_rate
        wait_started = time.monotonic()
        debug = {
            "analysis_blocks": 0,
            "info_probe_attempts": 0,
            "info_probe_hits": 0,
            "start_probe_attempts": 0,
            "start_probe_hits": 0,
            "energy_high_hits": 0,
            "energy_low_hits": 0,
            "unvalidated_rearms": 0,
            "exit": "running",
        }
        self.last_rx_debug = debug

        def finish_debug(reason: str) -> None:
            debug["exit"] = reason
            debug["floor"] = self._floor
            debug["floor_seen_seconds"] = self._floor_seen
            debug["floor_stable_seconds"] = self._floor_stable_seen
            debug["floor_ready"] = self._floor_ready()
            debug["elapsed_seconds"] = time.monotonic() - wait_started
            self.rx_debug_history.append(dict(debug))
            self.rx_debug_history = self.rx_debug_history[-64:]
        block = max(1, int(rate * ANALYSIS_BLOCK_SECONDS))
        longest = self.longest_burst_samples() + int(rate * (HANGOVER_SECONDS + 0.5))
        if max_capture_seconds is not None:
            longest = min(
                longest,
                max(1, int(rate * max(0.1, float(max_capture_seconds)))),
            )
        hangover = int(rate * HANGOVER_SECONDS)
        pretrigger = int(rate * PRETRIGGER_SECONDS)

        history = np.zeros(0)
        pending = np.zeros(0)
        collected = np.zeros(0)
        deadline = time.monotonic() + max(0.0, timeout)
        triggered = False
        trigger_polarity = 0  # +1: louder burst, -1: FM carrier quieted noise
        quiet = 0
        last_audio = time.monotonic()
        frame_span: tuple[int, int, bool] | None = None
        info_probe = getattr(self.codec, "probe_burst_info", None)
        span_probe = getattr(self.codec, "probe_burst_span", None)
        start_probe = getattr(self.codec, "probe_burst_start", None)
        if info_probe is not None:
            probe = info_probe
        elif span_probe is not None:
            def probe(profile, samples):
                found = span_probe(profile, samples)
                return None if found is None else (*found, False)
        else:
            probe = None

        def finish(result: np.ndarray, wall: float, hangover_seconds: float,
                   framed_end: int | None = None) -> np.ndarray:
            # _drain can return several callback blocks after a host scheduling
            # pause. Ending one capture must not discard unread audio, which may
            # already contain the next real preamble after a startup transient.
            if len(pending):
                with self._buffer_lock:
                    self._buffer.insert(0, pending.copy())
            finish_debug("framed_capture" if framed_end is not None else "capture")
            post_frame = (
                hangover_seconds if framed_end is None
                else max(0.0, (len(result) - framed_end) / rate)
            )
            # Absolute deadline, not a sleep chained after decoding.  Header,
            # manifest and payload decoding consume this physical radio-turn
            # interval instead of making it longer.
            self._peer_ready_at = time.monotonic() + max(
                TURNAROUND_MARGIN_SECONDS,
                self._peer_keyed_quiet - post_frame
                + TURNAROUND_MARGIN_SECONDS,
            )
            self.last_rx_framed = framed_end is not None
            self.last_rx_timing = RxTiming(
                trigger_wait=max(0.0, wall - len(result) / rate),
                capture=len(result) / rate,
                hangover=hangover_seconds,
                ready_wall_clock=wall,
            )
            self.last_capture_timing = self.last_rx_timing
            return result

        while True:
            if self._receive_interrupted.is_set():
                self._receive_interrupted.clear()
                finish_debug("interrupted")
                return None
            if self._stopped.is_set():
                finish_debug("stopped")
                return None
            arrived = self._drain()
            if len(arrived):
                pending = np.concatenate([pending, arrived])
                last_audio = time.monotonic()
            while len(pending) >= block:
                chunk, pending = pending[:block], pending[block:]
                debug["analysis_blocks"] += 1
                level = float(np.sqrt(np.mean(chunk ** 2)))
                if triggered:
                    if (trigger_polarity < 0
                            and probe is None
                            and level >= self._lower_release_level()):
                        # This block is the loud discriminator noise after the
                        # remote carrier dropped.  A length-delimited codec gets
                        # to inspect the block first: on some radios the useful
                        # waveform itself rises above this level after a quiet
                        # carrier lead, and treating that as carrier drop cuts
                        # the frame off before its robust header can be read.
                        # Codecs without a framing probe still finish at the
                        # energy transition and leave the noise out of the DSP
                        # window.
                        result = np.concatenate([history, collected])
                        wall = time.monotonic() - wait_started
                        return finish(
                            result, wall, 0.0,
                            (frame_span[1] if frame_span is not None
                             and len(result) >= frame_span[1] else None),
                        )
                    collected = np.concatenate([collected, chunk])
                    if trigger_polarity > 0:
                        quiet = quiet + block if level <= self._trigger_level() else 0
                    else:
                        quiet = 0
                    result = np.concatenate([history, collected])
                    if (probe is not None
                            and (frame_span is None
                                 or (frame_span[2]
                                     and len(result) >= frame_span[1]))):
                        debug["info_probe_attempts"] += 1
                        probe_limit = int(rate * PROBE_WINDOW_SECONDS)
                        # A deferred-ACK DATA header says that another
                        # independently synchronised microburst follows under
                        # the same PTT. Start the next search at the exact end
                        # of this one so decoding cannot make every other burst
                        # pass by while the receive loop is closed.
                        probe_offset = 0 if frame_span is None else frame_span[1]
                        probe_audio = result[
                            probe_offset:probe_offset + probe_limit
                        ]
                        found = probe(self.profile, probe_audio)
                        if found is not None:
                            debug["info_probe_hits"] += 1
                            frame_span = (
                                probe_offset + found[0],
                                probe_offset + found[1],
                                found[2],
                            )
                    if (frame_span is None
                            and len(collected) >= int(
                                rate * PROBE_WINDOW_SECONDS
                            )):
                        # An RMS transition can be a USB/FM startup transient.
                        # Do not let an unvalidated low-edge trigger pin the
                        # probe forever to its first idle window while a real
                        # frame arrives later. Re-arm internally and retain just
                        # enough history to acquire a boundary-crossing preamble.
                        history = result[-pretrigger:]
                        collected = np.zeros(0)
                        triggered = False
                        trigger_polarity = 0
                        quiet = 0
                        frame_span = None
                        # The transition itself proved that the old floor no
                        # longer describes current idle audio. Seed the tracker
                        # from this block so the same persistent low state cannot
                        # immediately fire a second false edge. The CRC/preamble
                        # trigger remains active while the new floor settles.
                        self._floor = max(level, 1e-12)
                        self._floor_seen = 0.0
                        self._floor_stable_seen = 0.0
                        debug["unvalidated_rearms"] += 1
                        continue
                    if frame_span is not None:
                        _frame_start, frame_end, defer_ack = frame_span
                        target = frame_end + int(rate * POST_FRAME_SECONDS)
                        if not defer_ack and len(result) >= target:
                            pending = np.concatenate([result[target:], pending])
                            result = result[:target]
                            wall = time.monotonic() - wait_started
                            return finish(
                                result, wall,
                                min(HANGOVER_SECONDS, quiet / rate),
                                frame_end,
                            )
                    if (len(collected) >= longest
                            or (frame_span is None and quiet >= hangover)):
                        wall = time.monotonic() - wait_started
                        return finish(
                            result, wall,
                            min(HANGOVER_SECONDS, quiet / rate),
                        )
                else:
                    # Once the floor is stable, compare an edge with the floor
                    # from *before* that edge and do not teach the tracker that
                    # the burst itself is the new idle level.  During initial
                    # settling every block still belongs to the tracker.
                    ready = self._floor_ready()
                    high_edge = ready and level > self._trigger_level()
                    low_edge = ready and level < self._lower_trigger_level()
                    if high_edge:
                        debug["energy_high_hits"] += 1
                        triggered = True
                        trigger_polarity = 1
                        collected = chunk
                    elif low_edge:
                        debug["energy_low_hits"] += 1
                        triggered = True
                        trigger_polarity = -1
                        # The falling edge is the carrier onset and precedes the
                        # PTT lead, so the louder idle-noise history contains no
                        # useful preamble and would only confuse acquisition.
                        history = np.zeros(0)
                        collected = chunk
                    else:
                        self._track_floor(level, block / rate)
                        # Keep only enough history to cover a preamble.
                        history = np.concatenate([history, chunk])[-pretrigger:]
                        # A correctly deviated FM waveform can sit between both
                        # RMS gates: not three times louder than idle noise and
                        # not six dB quieter either.  Do not make a valid OFDM
                        # header depend on radio gain/squelch settings.  The
                        # framing probe requires the robust header CRC, so it is
                        # a substantially stronger trigger than raw energy and a
                        # false Schmidl-Cox peak cannot open an unbounded capture.
                        if probe is not None and ready:
                            debug["info_probe_attempts"] += 1
                            found = probe(self.profile, history)
                            if found is not None:
                                debug["info_probe_hits"] += 1
                                triggered = True
                                trigger_polarity = 0
                                collected = np.zeros(0)
                                frame_span = found
                        # The cheap FFT preamble detector is deliberately
                        # independent of RMS readiness.  Running the full
                        # equalizer+CRC probe on every idle 50-ms block can fall
                        # behind real time; correlation merely opens a bounded
                        # capture, after which the normal CRC probe validates it.
                        if (not triggered and start_probe is not None):
                            debug["start_probe_attempts"] += 1
                            start = start_probe(self.profile, history)
                            if start is not None:
                                debug["start_probe_hits"] += 1
                                triggered = True
                                trigger_polarity = 0
                                history = history[max(0, int(start)):]
                                collected = np.zeros(0)
            if triggered:
                if time.monotonic() - last_audio >= STREAM_STALL_SECONDS:
                    result = np.concatenate([history, collected])
                    wall = time.monotonic() - wait_started
                    return finish(
                        result, wall,
                        min(HANGOVER_SECONDS, quiet / rate),
                        (frame_span[1] if frame_span is not None
                         and len(result) >= frame_span[1] else None),
                    )
            elif time.monotonic() >= deadline:
                finish_debug("timeout")
                self.last_rx_timing = RxTiming(
                    trigger_wait=time.monotonic() - wait_started,
                    ready_wall_clock=time.monotonic() - wait_started,
                )
                return None
            time.sleep(0.005)

    def guard_peer_receiver(self) -> None:
        """Wait only until the far radio can physically have returned to RX."""
        remaining = self._peer_ready_at - time.monotonic()
        if remaining > 0.0:
            time.sleep(remaining)

    def save_rejected_capture(self, samples, reason: str) -> None:
        """Keep one bounded, automatically refreshed field-failure capture."""
        now = time.monotonic()
        if ((self._last_diagnostic_audio is not None
             and now - self._last_diagnostic_audio < 8.0)
                or (self._diagnostic_thread is not None
                    and self._diagnostic_thread.is_alive())):
            return
        audio = np.asarray(samples, dtype=np.float64).reshape(-1).copy()
        if not len(audio):
            return
        self._last_diagnostic_audio = now
        timing = self.last_rx_timing or self.last_capture_timing
        timing_metadata = ({
                "trigger_wait": timing.trigger_wait,
                "capture": timing.capture,
                "hangover": timing.hangover,
                "ready_wall_clock": timing.ready_wall_clock,
            } if timing is not None else None)
        framed = self.last_rx_framed

        def write_diagnostic() -> None:
            metadata = {
                "generated_utc": datetime.now(timezone.utc).isoformat(),
                "profile": self.profile.name,
                "sample_rate": self.profile.sample_rate,
                "sample_count": len(audio),
                "seconds": len(audio) / self.profile.sample_rate,
                "reason": str(reason),
                "length_delimited": framed,
                "audio_peak": float(np.max(np.abs(audio))),
                "audio_rms": float(np.sqrt(np.mean(audio ** 2))),
                "rx_timing": timing_metadata,
                "max_burst_bytes": self.max_burst_bytes,
                "arq_block_bytes": self.arq_block_bytes,
            }
            try:
                from ..config import config_dir

                path = config_dir() / "last-bad-ofdm.wav"
                path.parent.mkdir(parents=True, exist_ok=True)
                pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
                suffix = f"{id(self):x}-{threading.get_ident():x}"
                temp_audio = path.with_name(f".{path.stem}-{suffix}.tmp.wav")
                json_path = path.with_suffix(".json")
                temp_json = json_path.with_name(
                    f".{json_path.stem}-{suffix}.tmp.json"
                )
                with _REJECTED_AUDIO_WRITE_LOCK:
                    try:
                        # Open the filesystem object before constructing a
                        # Wave_write.  wave.open(path, "wb") constructs the
                        # Wave_write first and, if the underlying open is denied,
                        # leaves an incomplete object whose __del__ emits a second
                        # misleading exception.  A file object cannot take that
                        # broken constructor path.
                        with open(temp_audio, "wb") as raw_audio:
                            with wave.open(raw_audio, "wb") as recording:
                                recording.setnchannels(1)
                                recording.setsampwidth(2)
                                recording.setframerate(self.profile.sample_rate)
                                recording.writeframes(pcm.tobytes())
                        temp_json.write_text(
                            json.dumps(metadata, indent=2, ensure_ascii=False),
                            encoding="utf-8",
                        )
                        temp_audio.replace(path)
                        temp_json.replace(json_path)
                    finally:
                        temp_audio.unlink(missing_ok=True)
                        temp_json.unlink(missing_ok=True)
                self.last_rejected_ofdm = metadata
                self.on_log(f"Rejected OFDM audio saved: {path}")
            except Exception as exc:  # noqa: BLE001 - diagnostics cannot break ARQ
                self.on_log(f"Rejected OFDM audio could not be saved: {exc}")

        self._diagnostic_thread = threading.Thread(
            target=write_diagnostic,
            name="ofdm-diagnostic-wav",
            daemon=True,
        )
        self._diagnostic_thread.start()

    def _trigger_level(self) -> float:
        return max(1e-5, self._floor * SQUELCH_ABOVE_FLOOR)

    def _lower_trigger_level(self) -> float:
        return self._floor * SQUELCH_BELOW_FLOOR

    def _lower_release_level(self) -> float:
        return self._floor * SQUELCH_RETURN_TO_FLOOR

    def _floor_ready(self) -> bool:
        return (self._floor_seen >= FLOOR_SETTLE_SECONDS
                and self._floor_stable_seen >= FLOOR_SETTLE_SECONDS)

    def _track_floor(self, level: float, seconds: float) -> None:
        """Follow the idle noise level: quick to come down, slow to go up.

        Slow to go up on purpose: track a rise fast and a burst drags the floor
        to its own level, after which nothing triggers again.

        Quick to come down for the opposite reason, and it is the one that bit us
        on air. Every payload transmission is followed immediately by listening
        for the answer, and the first audio after the transmitter unkeys is the
        receiver's AGC recovering and whatever the radio makes of the carrier
        dropping. Seeding the floor from that -- which taking the first block, or
        a symmetric filter, both do -- sets it tens of dB above the real noise,
        and since the trigger is three times the floor, the acknowledgement that
        follows never rises above it. The station then retransmits into a channel
        it has made itself deaf to. A floor that falls with a 100 ms time constant
        and rises with a 10 s one converges on the quiet part regardless of what
        it was seeded with.
        """
        previous = self._floor
        if previous > 0.0 and 0.5 <= level / previous <= 2.0:
            self._floor_stable_seen += seconds
        else:
            self._floor_stable_seen = 0.0
        if self._floor_seen <= 0.0:
            self._floor = level
        elif level < self._floor:
            self._floor += (level - self._floor) * min(1.0, seconds / 0.1)
        else:
            self._floor += (level - self._floor) * min(0.1, seconds)
        self._floor_seen += seconds

    def describe_squelch(self) -> str:
        """The squelch state in dBFS, for the log when a reply window came up empty."""
        def dbfs(value: float) -> str:
            return f"{20.0 * np.log10(value):.0f} dBFS" if value > 0 else "silent"

        return (f"squelch floor {dbfs(self._floor)}, opens above "
                f"{dbfs(self._trigger_level())} or below "
                f"{dbfs(self._lower_trigger_level())}")

    def longest_burst_samples(self) -> int:
        """The longest burst this profile can produce, at the most robust MCS."""
        count = min(32, max(1, (self.max_burst_bytes + self.arq_block_bytes - 1)
                              // self.arq_block_bytes))
        lengths = [self.arq_block_bytes] * count
        header = PhyHeader(
            OfdmFrameType.DATA, 0, block_count=count,
            payload_len=sum(lengths), subblock_count=count,
            fec=FecProfile.FEC_1_2,
        )
        one = self.codec.burst_samples(self.profile, header, lengths)
        return (one * self.train_bursts
                + int(self.profile.sample_rate * self.train_gap)
                * max(0, self.train_bursts - 1))

    # -- transmit -----------------------------------------------------------

    def send(self, samples: np.ndarray) -> TxTiming:
        """Key the radio, play the burst, unkey. PTT is released on every path.

        The keying itself is `modem.audio.transmit_waveform`, which is also what
        the Modem test workspace transmits through -- one implementation of
        "always release the transmitter" rather than one per caller.
        """
        if self._sd is None:
            raise RuntimeError("OFDM VHF: audio pipe was not started")

        def clear_receive_buffer() -> None:
            # Never splice audio from before and after our own transmission into
            # one receive window.
            with self._buffer_lock:
                self._buffer = []

        lead = self._lead_seconds()
        tail = self._tail_seconds()
        started = time.monotonic()
        with self._tx_lock:
            transmit_waveform(
                self._sd, np.asarray(samples, dtype=np.float64) * self.tx_scale,
                device=self.output_device,
                sample_rate=self.profile.sample_rate,
                ptt=self.ptt,
                lead_seconds=lead,
                tail_seconds=tail,
                guard_seconds=self.tx_guard,
                write_chunk_frames=self.tx_write_chunk_frames,
                before_play=clear_receive_buffer,
                after_release=clear_receive_buffer,
            )
        return TxTiming(
            lead=lead,
            waveform=len(samples) / self.profile.sample_rate,
            guard=self.tx_guard,
            tail=tail,
            keyed_total=lead + len(samples) / self.profile.sample_rate
                        + self.tx_guard + tail,
            wall_clock=time.monotonic() - started,
        )


class OfdmVhfBackend(PayloadBackend):
    """PayloadBackend over the Guardian OFDM VHF modem."""

    name = "ofdm_vhf"

    def __init__(self, *, ofdm_profile: str = "BENCH", ofdm_mcs: int = 1,
                 ofdm_tx_lead_ms: int = 60, ofdm_tx_tail_ms: int = 60,
                 ofdm_max_retries: int = 4,
                  ofdm_adaptive_fec: bool = True,
                  ofdm_modern_ldpc: bool = False, ofdm_fec: str = "1/2",
                 ofdm_adaptive_burst: bool = True, ofdm_burst_bytes: int = 4096,
                 ofdm_min_burst_bytes: int = 512,
                 ofdm_max_burst_bytes: int = 8192,
                 ofdm_arq_block_bytes: int = 512,
                 ofdm_timeout_multiplier: float = 1.0,
                 ofdm_legacy_mode: bool = False,
                 ofdm_train_bursts: int = 1, ofdm_adaptive_train: bool = False,
                 ofdm_superframe: bool = False,
                 ofdm_train_gap_ms: int = 30,
                 ofdm_max_train_seconds: float = 20.0,
                   g2_waveform: str = "sc_ftn", g2_bandwidth: str = "2K7",
                  g2_mcs: int = 2, g2_adaptive_mcs: bool = False,
                  g2_automatic: bool = True,
                  radio_backend: str = "",
                  radio_model: str = "",
                  g2_tx_scale: float = 1.0,
                  audio_input=None, audio_output=None,
                  ptt: Callable[[bool], None] | None = None,
                  ptt_turnaround_ms: int = 0,
                  on_log=None, on_qsy=None, on_receive_qsy=None, on_unqsy=None,
                  on_acquire=None, on_release=None, on_handoff_failed=None,
                  pipe_factory=None) -> None:
        self.automatic = bool(g2_automatic)
        # This port exposes SC-FTN beside VARA. Reject every other G2 family
        # before policy lookup so an unsupported remote/config token cannot be
        # silently normalized into a different waveform.
        requested_family = str(g2_waveform or "sc_ftn").strip().lower()
        if requested_family != "sc_ftn":
            raise ValueError(
                f"unsupported Guardian waveform {requested_family!r}; "
                "this build supports SC-FTN only"
            )
        requested_bandwidth = str(g2_bandwidth or "2K7").strip().upper()
        if requested_bandwidth not in {"1K2", "2K7", "4K5", "5K", "10K", "20K"}:
            raise ValueError(
                f"unsupported SC-FTN bandwidth {requested_bandwidth!r}"
            )
        self.radio_backend = str(radio_backend or "").strip().lower()
        policy = automatic_g2_policy(
            requested_family, requested_bandwidth, radio_backend=self.radio_backend,
            radio_model=radio_model,
        )
        self.policy = policy if self.automatic else None
        self.waveform_family = requested_family
        self.g2_bandwidth = requested_bandwidth
        self.profile = experimental_profile_for(
            self.waveform_family, self.g2_bandwidth
        )
        if self.automatic:
            geometry = {
                name: value for name, value in (
                    ("center_hz", policy.center_hz),
                    ("nyquist_symbol_rate", policy.nyquist_symbol_rate),
                    ("symbol_rate", policy.symbol_rate),
                ) if value is not None
            }
            self.profile = replace(
                self.profile,
                bootstrap_modulation=policy.bootstrap_modulation,
                data_acquisition_lead_seconds=(
                    policy.acquisition_lead_seconds
                ),
                reference_metric_blocks=policy.reference_metric_blocks,
                **geometry,
            )
        self.codec = ExperimentalBurstCodec()
        self.requested_profile = ofdm_profile
        self.mcs_index = (policy.maximum_mcs if self.automatic else int(g2_mcs))
        self.adaptive_mcs = True if self.automatic else bool(g2_adaptive_mcs)
        self.tx_scale = min(G2_MAX_TX_SCALE, max(MIN_TX_SCALE, float(g2_tx_scale)))
        self.tx_lead_ms = int(ofdm_tx_lead_ms)
        self.tx_tail_ms = int(ofdm_tx_tail_ms)
        self.max_retries = ((policy.maximum_retries + policy.rescue_retries)
                            if self.automatic
                            else int(ofdm_max_retries))
        self.rescue_after_attempt = (
            policy.maximum_retries + 1 if self.automatic else None
        )
        self.rescue_mcs_index = policy.rescue_mcs
        arq_bytes = int(ofdm_arq_block_bytes)
        minimum = max(
            arq_bytes,
            min(int(ofdm_min_burst_bytes), int(ofdm_max_burst_bytes)),
        )
        maximum = max(
            minimum,
            int(ofdm_min_burst_bytes),
            int(ofdm_max_burst_bytes),
        )
        fixed_burst = max(arq_bytes, int(ofdm_burst_bytes))
        if self.automatic:
            self.adaptation_config = AdaptationConfig(
                adaptive_fec=True,
                modern_ldpc=True,
                fixed_fec=policy.initial_fec,
                initial_fec=policy.initial_fec,
                adaptive_burst=True,
                fixed_burst_bytes=policy.initial_burst_bytes,
                initial_burst_bytes=policy.initial_burst_bytes,
                min_burst_bytes=policy.minimum_burst_bytes,
                max_burst_bytes=policy.maximum_burst_bytes,
                arq_block_bytes=policy.arq_block_bytes,
                clean_bursts_to_upgrade=policy.clean_bursts_to_upgrade,
                rapid_acquisition=policy.rapid_acquisition,
            )
        else:
            self.adaptation_config = AdaptationConfig(
                adaptive_fec=bool(ofdm_adaptive_fec),
                modern_ldpc=bool(ofdm_modern_ldpc),
                fixed_fec=fec_profile(ofdm_fec),
                adaptive_burst=bool(ofdm_adaptive_burst),
                fixed_burst_bytes=fixed_burst,
                min_burst_bytes=minimum,
                max_burst_bytes=maximum,
                arq_block_bytes=arq_bytes,
            )
        self.controller = LinkAdaptationController(
            self.adaptation_config,
            mcs_index=(policy.initial_mcs if self.automatic else
                       (1 if self.adaptive_mcs else self.mcs_index)),
        )
        self._initial_controller_mcs = self.controller.mcs_index
        # Link history belongs to a remote path, not to the sound-card backend
        # as a whole.  The transfer lock makes this small in-memory cache
        # single-owner; returning stations resume their own learned state.
        self._peer_controllers: dict[str, LinkAdaptationController] = {}
        self._peer_controller_seen: dict[str, float] = {}
        self._peer_controller_path: dict[str, tuple] = {}
        self.timeout_multiplier = (1.0 if self.automatic else
            max(0.5, min(4.0, float(ofdm_timeout_multiplier))))
        self.legacy_mode = False if self.automatic else bool(ofdm_legacy_mode)
        self.train_bursts = (1 if self.automatic else
                             max(1, min(8, int(ofdm_train_bursts))))
        self.adaptive_train = False if self.automatic else bool(ofdm_adaptive_train)
        self.superframe = True if self.automatic else bool(ofdm_superframe)
        self.train_gap_ms = max(10, min(200, int(ofdm_train_gap_ms)))
        self.max_train_seconds = (policy.maximum_train_seconds if self.automatic else max(
            1.0, min(60.0, float(ofdm_max_train_seconds))
        ))
        self.tx_guard_ms = policy.tx_guard_ms if self.automatic else 250
        self.exact_ptt_timing = self.automatic
        self.chase_first_retry = self.automatic
        self.audio_input = audio_input
        self.audio_output = audio_output
        self.ptt = ptt or (lambda enabled: None)
        # PTT turnaround is what dominates how long an answer may take, so the
        # ARQ timeouts are built from it rather than from a round number. The
        # keying delay both stations negotiated is exactly that quantity.
        self.ptt_turnaround = max(
            0.25,
            ptt_turnaround_ms / 1000.0
            + self.tx_lead_ms / 1000.0 + self.tx_tail_ms / 1000.0,
        )
        self.on_log = on_log or (lambda message: None)
        self.on_qsy = on_qsy
        self.on_receive_qsy = on_receive_qsy
        self.on_unqsy = on_unqsy
        self.on_acquire = on_acquire
        self.on_release = on_release
        self._pipe_factory = pipe_factory
        self._transfer_lock = threading.Lock()
        self._active_lock = threading.Lock()
        self._cancelled = threading.Event()
        self._active_msg_id: int | None = None
        self._active_message = None
        self._active_pipe = None
        #: Latest measurements, for the transfer panel to poll.
        selected = self.controller.profile
        self.status = OfdmStatus(
            profile=self.profile.name, mcs=self.controller.mcs_index,
            fec=fec_spec(selected.fec).label,
            burst_bytes=selected.burst_bytes,
            arq_block_bytes=selected.arq_block_bytes,
        )

    def set_ptt_turnaround_ms(self, delay_ms: int | float) -> None:
        """Update the ARQ turnaround budget after session negotiation.

        The control handshake can negotiate slow keying after this backend was
        constructed.  Keep the lead and tail guards in the budget, while
        allowing a later transfer to use the agreed radio release delay.
        Existing links read this value when they are created; Operations calls
        this before the selected payload worker starts its link.
        """
        delay_seconds = max(0.0, float(delay_ms)) / 1000.0
        self.ptt_turnaround = max(
            0.25,
            delay_seconds + self.tx_lead_ms / 1000.0
            + self.tx_tail_ms / 1000.0,
        )


    # -- plumbing -----------------------------------------------------------

    def _make_pipe(self):
        if self._pipe_factory is not None:
            return self._pipe_factory(self.profile)
        return RadioAudioPipe(
            self.profile,
            input_device=resolve_device(self.audio_input, "input"),
            output_device=resolve_device(self.audio_output, "output"),
            ptt=self.ptt,
            tx_lead_ms=self.tx_lead_ms,
            tx_tail_ms=self.tx_tail_ms,
            tx_guard_ms=self.tx_guard_ms,
            exact_ptt_timing=self.exact_ptt_timing,
            tx_scale=self.tx_scale,
            train_bursts=self.train_bursts,
            train_gap_ms=self.train_gap_ms,
            max_burst_bytes=max(
                self.adaptation_config.max_burst_bytes,
                self.adaptation_config.fixed_burst_bytes,
            ),
            arq_block_bytes=self.adaptation_config.arq_block_bytes,
            on_log=self.on_log,
            codec=self.codec,
        )

    def _controller_for_peer(self, peer: str, *, direction: str = "send",
                             frequency_hz: int = 0) -> LinkAdaptationController:
        key = f"{direction}:{str(peer or '').strip().upper() or '?'}"
        now = time.monotonic()
        path = (self.waveform_family, self.g2_bandwidth, int(frequency_hz),
                str(self.audio_input), str(self.audio_output), self.tx_scale)
        existing = self._peer_controllers.get(key)
        valid = (existing is not None
                 and self._peer_controller_path.get(key) == path
                 and now - self._peer_controller_seen.get(key, 0.0) < 120.0)
        self._peer_controller_seen[key] = now
        self._peer_controller_path[key] = path
        if valid:
            self.controller = existing
            return existing
        controller = LinkAdaptationController(
            self.adaptation_config, mcs_index=self._initial_controller_mcs
        )
        self._peer_controllers[key] = controller
        self.controller = controller
        return controller

    def _make_link(self, pipe, controller: LinkAdaptationController | None = None) -> OfdmLink:
        selected_controller = controller or self.controller
        return OfdmLink(
            self.profile, pipe,
            mcs_index=self.mcs_index,
            max_retries=self.max_retries,
            ptt_turnaround=self.ptt_turnaround,
            # Both ends spend a hangover deciding a burst has finished before they
            # can start decoding it, so an answer is always that much later than
            # its airtime and turnaround alone suggest. Counting it here rather
            # than padding the turnaround keeps each number meaning one thing.
            timeout_margin=2.0 * HANGOVER_SECONDS + 1.0,
            timeout_multiplier=self.timeout_multiplier,
            on_log=self.on_log,
            on_status=self._publish,
            cancelled=self._cancelled.is_set,
            controller=selected_controller,
            legacy_mode=self.legacy_mode,
            train_bursts=self.train_bursts,
            adaptive_train=self.adaptive_train,
            adaptive_mcs=self.adaptive_mcs,
            superframe=self.superframe,
            train_gap_seconds=self.train_gap_ms / 1000.0,
            max_train_seconds=self.max_train_seconds,
            chase_first_retry=self.chase_first_retry,
            rescue_after_attempt=self.rescue_after_attempt,
            rescue_mcs_index=self.rescue_mcs_index,
            codec=self.codec,
        )

    def _publish(self, status: OfdmStatus) -> None:
        self.status = status
        with self._active_lock:
            msg = self._active_message
            if msg is not None:
                moved = max(int(status.tx_bytes), int(status.rx_bytes))
                msg.payload_progress_bytes = max(
                    int(getattr(msg, "payload_progress_bytes", 0)), moved
                )

    def _reset_status(self, direction: str) -> None:
        """Publish a clean transfer view before audio/QSY setup can block."""
        selected = self.controller.profile
        self.status = OfdmStatus(
            direction=direction,
            profile=self.profile.name,
            mcs=self.controller.mcs_index,
            fec=fec_spec(selected.fec).label,
            burst_bytes=selected.burst_bytes,
            arq_block_bytes=selected.arq_block_bytes,
        )

    def _begin_transfer(self, msg) -> None:
        with self._active_lock:
            self._cancelled.clear()
            self._active_msg_id = int(msg.msg_id)
            self._active_message = msg
            self._active_pipe = None

    def _set_active_pipe(self, pipe) -> None:
        with self._active_lock:
            self._active_pipe = pipe

    def _end_transfer(self) -> None:
        with self._active_lock:
            self._active_msg_id = None
            self._active_message = None
            self._active_pipe = None

    def _check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise RuntimeError("OFDM transfer cancelled")

    @staticmethod
    def _payload_of(msg) -> bytes:
        return (msg.payload_bytes if msg.payload_bytes is not None
                else msg.body.encode("utf-8"))

    # -- the PayloadBackend interface --------------------------------------

    def start_send(self, msg, done: DoneCb) -> None:
        self._begin_transfer(msg)
        threading.Thread(target=self._send, args=(msg, done), daemon=True).start()

    def start_receive(self, msg, done: DoneCb) -> None:
        self._begin_transfer(msg)
        threading.Thread(target=self._receive, args=(msg, done), daemon=True).start()

    def _send(self, msg, done: DoneCb) -> None:
        success = False
        acquired = False
        with self._transfer_lock:
            controller = self._controller_for_peer(
                msg.next_hop, frequency_hz=getattr(msg, "working_frequency_hz", 0))
            self._reset_status("send")
            pipe = None
            try:
                self._check_cancelled()
                if self.on_acquire:
                    self.on_acquire()
                    acquired = True
                # START has just drained locally. The peer still has to decode
                # it, service its protocol timer and reopen the shared input.
                # Allow that handoff once, with PTT off; do not pad every DATA
                # preamble or every subsequent DATA/ACK turnaround.
                handoff_ready = time.monotonic() + (
                    0.35 if acquired and self.waveform_family == "sc_ftn" else 0.0)
                self._check_cancelled()
                if self.on_qsy and self.on_qsy(msg) is False:
                    raise RuntimeError("working-channel QSY failed")
                data = self._payload_of(msg)
                pipe = self._make_pipe()
                self._set_active_pipe(pipe)
                self._check_cancelled()
                pipe.start()
                self._check_cancelled()
                if self._cancelled.wait(max(0.0, handoff_ready - time.monotonic())):
                    self._check_cancelled()
                self.on_log(
                    f"Guardian G2 {self.waveform_family.upper()}: sending "
                    f"#{msg.msg_id} ({len(data)} bytes) on "
                    f"profile {self.profile.name}, MCS{self.controller.mcs_index}, "
                    f"{self.controller.summary()}, "
                    f"mode={'legacy v1' if self.legacy_mode else 'adaptive v2'}"
                )
                success = self._make_link(pipe, controller).send_message(msg.msg_id, data)
            except Exception as exc:  # noqa: BLE001 - fail the transfer, not the app
                self.on_log(f"OFDM VHF send failed: {exc}")
            finally:
                key = f"send:{str(msg.next_hop or '').strip().upper() or '?'}"
                # A failed transfer is not a valid warm-start reference. Keep
                # application delivery state, but reacquire the physical path.
                if not success:
                    self._peer_controllers.pop(key, None)
                self._peer_controller_seen[key] = time.monotonic()
                if pipe is not None:
                    _swallow(pipe.stop)
                if self.on_unqsy:
                    _swallow(self.on_unqsy)
                if acquired and self.on_release:
                    _swallow(self.on_release)
                self._end_transfer()
        # done() may immediately send RECEIVED/CANCEL over AFSK, so it must run
        # only after the shared soundcard has been returned to that modem.
        done(success)

    def _receive(self, msg, done: DoneCb) -> None:
        success = False
        acquired = False
        with self._transfer_lock:
            controller = self._controller_for_peer(
                msg.source, direction="receive",
                frequency_hz=getattr(msg, "working_frequency_hz", 0))
            self._reset_status("receive")
            pipe = None
            try:
                self._check_cancelled()
                if self.on_acquire:
                    self.on_acquire()
                    acquired = True
                self._check_cancelled()
                if self.on_receive_qsy and self.on_receive_qsy(msg) is False:
                    raise RuntimeError("working-channel QSY failed")
                pipe = self._make_pipe()
                self._set_active_pipe(pipe)
                self._check_cancelled()
                pipe.start()
                self._check_cancelled()
                self.on_log(f"OFDM VHF: waiting for payload #{msg.msg_id}")
                received = self._make_link(pipe, controller).receive_message(
                    msg_id=msg.msg_id
                )
                if received is not None:
                    msg.payload_bytes = received
                    msg.body = ""
                    success = True
                    self.on_log(
                        f"OFDM VHF: payload #{msg.msg_id} received OK "
                        f"({len(received)} bytes)"
                    )
                else:
                    self.on_log(f"OFDM VHF: payload #{msg.msg_id} did not arrive")
            except Exception as exc:  # noqa: BLE001 - fail the transfer, not the app
                self.on_log(f"OFDM VHF receive failed: {exc}")
            finally:
                if pipe is not None:
                    _swallow(pipe.stop)
                if self.on_unqsy:
                    _swallow(self.on_unqsy)
                if acquired and self.on_release:
                    _swallow(self.on_release)
                self._end_transfer()
        done(success)

    def cancel(self, msg) -> None:
        """Abort the active ARQ loop and unblock a pending soundcard receive."""
        with self._active_lock:
            if self._active_msg_id != int(msg.msg_id):
                return
            self._cancelled.set()
            pipe = self._active_pipe
        if pipe is not None:
            _swallow(pipe.stop)
        self.on_log(f"OFDM VHF: payload #{msg.msg_id} cancelled")
