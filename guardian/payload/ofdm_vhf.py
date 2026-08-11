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

import threading
import time
from typing import Callable

import numpy as np

from ..modem.audio import (PTT_LEAD_SECONDS, PTT_TAIL_SECONDS,
                           _import_sounddevice, resolve_device,
                           transmit_waveform)
from ..ofdm import OfdmLink, OfdmStatus, PhyHeader, profile_or_default
from ..ofdm.link import OfdmBurstCodec
from ..ofdm.adaptation import AdaptationConfig, LinkAdaptationController
from ..ofdm.coding import FecProfile, fec_profile, fec_spec
from ..ofdm.framing import OfdmFrameType, burst_samples
from ..waveforms.config import PROFILES as EXPERIMENTAL_PROFILES
from ..waveforms.framing import ExperimentalBurstCodec
from .base import DoneCb, PayloadBackend

#: Extra silence appended after a burst, for the same reason the control modem
#: appends its own: stopping the output stream discards whatever the host API and
#: a USB radio still hold buffered, and on air that cost a measured ~130 ms off
#: the end of every control burst. Here the loss would be the tail of the data
#: section, so the guard is sized from the same measurement.
TX_GUARD_SECONDS = 0.4

#: How far above the tracked noise floor the receive level must rise before a
#: burst is believed to have started. The modem's own detector decides whether it
#: really was one; this only decides when to stop waiting and start decoding, so
#: it is deliberately generous -- a missed trigger costs a whole exchange, while a
#: false one costs a few milliseconds of Viterbi.
SQUELCH_ABOVE_FLOOR = 3.0

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
                 max_burst_bytes: int = 8192, arq_block_bytes: int = 512,
                 on_log: Callable[[str], None] | None = None,
                 codec=None) -> None:
        self.profile = profile
        self.input_device = input_device
        self.output_device = output_device
        self.ptt = ptt
        self.tx_lead = max(0.0, tx_lead_ms / 1000.0)
        self.tx_tail = max(0.0, tx_tail_ms / 1000.0)
        self.max_burst_bytes = max(1, int(max_burst_bytes))
        self.arq_block_bytes = max(1, int(arq_block_bytes))
        self.on_log = on_log or (lambda message: None)
        self.codec = codec or OfdmBurstCodec()

        self._sd = None
        self._stream = None
        # Guards the transmitter: half duplex means never keying while the
        # receive side is being read, and never two transmissions at once.
        self._tx_lock = threading.Lock()
        self._buffer_lock = threading.Lock()
        self._buffer: list[np.ndarray] = []
        self._floor = 0.0
        self._floor_seen = 0.0

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
        self.on_log(f"OFDM VHF: listening at {rate} Hz on device {self.input_device}")

    def stop(self) -> None:
        """Close the receive stream. Never raises — it runs in `finally` blocks."""
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

    def receive(self, timeout: float) -> np.ndarray | None:
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
        block = max(1, int(rate * ANALYSIS_BLOCK_SECONDS))
        longest = self.longest_burst_samples() + int(rate * (HANGOVER_SECONDS + 0.5))
        hangover = int(rate * HANGOVER_SECONDS)
        pretrigger = int(rate * PRETRIGGER_SECONDS)

        history = np.zeros(0)
        pending = np.zeros(0)
        collected = np.zeros(0)
        deadline = time.monotonic() + max(0.0, timeout)
        triggered = False
        quiet = 0
        last_audio = time.monotonic()

        while True:
            arrived = self._drain()
            if len(arrived):
                pending = np.concatenate([pending, arrived])
                last_audio = time.monotonic()
            while len(pending) >= block:
                chunk, pending = pending[:block], pending[block:]
                level = float(np.sqrt(np.mean(chunk ** 2)))
                if triggered:
                    collected = np.concatenate([collected, chunk])
                    quiet = quiet + block if level <= self._trigger_level() else 0
                    if quiet >= hangover or len(collected) >= longest:
                        return np.concatenate([history, collected])
                else:
                    # Track the floor first, and every block -- deciding whether
                    # this one is loud is meaningless until there is something to
                    # call loud relative to.
                    self._track_floor(level, block / rate)
                    if self._floor_ready() and level > self._trigger_level():
                        triggered = True
                        collected = chunk
                    else:
                        # Keep only enough history to cover a preamble.
                        history = np.concatenate([history, chunk])[-pretrigger:]
            if triggered:
                if time.monotonic() - last_audio >= STREAM_STALL_SECONDS:
                    return np.concatenate([history, collected])
            elif time.monotonic() >= deadline:
                return None
            time.sleep(0.005)

    def _trigger_level(self) -> float:
        return max(1e-5, self._floor * SQUELCH_ABOVE_FLOOR)

    def _floor_ready(self) -> bool:
        return self._floor_seen >= FLOOR_SETTLE_SECONDS

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

        return (f"squelch floor {dbfs(self._floor)}, "
                f"opens at {dbfs(self._trigger_level())}")

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
        if isinstance(self.codec, OfdmBurstCodec):
            return burst_samples(self.profile, header, lengths)
        return self.codec.burst_samples(self.profile, header, lengths)

    # -- transmit -----------------------------------------------------------

    def send(self, samples: np.ndarray) -> None:
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

        with self._tx_lock:
            transmit_waveform(
                self._sd, samples,
                device=self.output_device,
                sample_rate=self.profile.sample_rate,
                ptt=self.ptt,
                lead_seconds=max(self.tx_lead, PTT_LEAD_SECONDS),
                tail_seconds=max(self.tx_tail, PTT_TAIL_SECONDS),
                guard_seconds=TX_GUARD_SECONDS,
                before_play=clear_receive_buffer,
                after_release=clear_receive_buffer,
            )


class OfdmVhfBackend(PayloadBackend):
    """PayloadBackend over the Guardian OFDM VHF modem."""

    name = "ofdm_vhf"

    def __init__(self, *, ofdm_profile: str = "BENCH", ofdm_mcs: int = 1,
                 ofdm_tx_lead_ms: int = 300, ofdm_tx_tail_ms: int = 100,
                 ofdm_max_retries: int = 4,
                 ofdm_adaptive_fec: bool = True, ofdm_fec: str = "1/2",
                 ofdm_adaptive_burst: bool = True, ofdm_burst_bytes: int = 4096,
                 ofdm_min_burst_bytes: int = 512,
                 ofdm_max_burst_bytes: int = 8192,
                 ofdm_arq_block_bytes: int = 512,
                 ofdm_timeout_multiplier: float = 1.0,
                 ofdm_legacy_mode: bool = False,
                 g2_waveform: str = "ofdm", g2_mcs: int = 2,
                 audio_input=None, audio_output=None,
                 ptt: Callable[[bool], None] | None = None,
                 ptt_turnaround_ms: int = 0,
                 on_log=None, on_qsy=None, on_receive_qsy=None, on_unqsy=None,
                 on_acquire=None, on_release=None, pipe_factory=None) -> None:
        self.waveform_family = str(g2_waveform or "ofdm").strip().lower()
        experimental = {
            "sc_hs": "SC_HS_2K7",
            "sc_ftn": "SC_FTN_2K7",
            "sefdm": "SEFDM_2K7",
        }
        if self.waveform_family in experimental:
            self.profile = EXPERIMENTAL_PROFILES[experimental[self.waveform_family]]
            self.codec = ExperimentalBurstCodec()
        else:
            self.waveform_family = "ofdm"
            self.profile = profile_or_default(ofdm_profile)
            self.codec = OfdmBurstCodec()
        self.requested_profile = ofdm_profile
        self.mcs_index = int(ofdm_mcs if self.waveform_family == "ofdm" else g2_mcs)
        self.tx_lead_ms = int(ofdm_tx_lead_ms)
        self.tx_tail_ms = int(ofdm_tx_tail_ms)
        self.max_retries = int(ofdm_max_retries)
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
        self.adaptation_config = AdaptationConfig(
            adaptive_fec=bool(ofdm_adaptive_fec),
            fixed_fec=fec_profile(ofdm_fec),
            adaptive_burst=bool(ofdm_adaptive_burst),
            fixed_burst_bytes=fixed_burst,
            min_burst_bytes=minimum,
            max_burst_bytes=maximum,
            arq_block_bytes=arq_bytes,
        )
        self.controller = LinkAdaptationController(
            self.adaptation_config, mcs_index=self.mcs_index
        )
        self.timeout_multiplier = max(0.5, min(4.0, float(ofdm_timeout_multiplier)))
        self.legacy_mode = bool(ofdm_legacy_mode)
        self.audio_input = audio_input
        self.audio_output = audio_output
        self.ptt = ptt or (lambda enabled: None)
        # PTT turnaround is what dominates how long an answer may take, so the
        # ARQ timeouts are built from it rather than from a round number. The
        # keying delay both stations negotiated is exactly that quantity.
        self.ptt_turnaround = max(0.25, ptt_turnaround_ms / 1000.0
                                  + PTT_LEAD_SECONDS + PTT_TAIL_SECONDS)
        self.on_log = on_log or (lambda message: None)
        self.on_qsy = on_qsy
        self.on_receive_qsy = on_receive_qsy
        self.on_unqsy = on_unqsy
        self.on_acquire = on_acquire
        self.on_release = on_release
        self._pipe_factory = pipe_factory
        self._transfer_lock = threading.Lock()
        #: Latest measurements, for the transfer panel to poll.
        selected = self.controller.profile
        self.status = OfdmStatus(
            profile=self.profile.name, mcs=self.mcs_index,
            fec=fec_spec(selected.fec).label,
            burst_bytes=selected.burst_bytes,
            arq_block_bytes=selected.arq_block_bytes,
        )

        if self.waveform_family == "ofdm" and self.profile.name != ofdm_profile:
            self.on_log(
                f"OFDM VHF: profile {ofdm_profile!r} is not known to this build; "
                f"using {self.profile.name} instead"
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
            max_burst_bytes=max(
                self.adaptation_config.max_burst_bytes,
                self.adaptation_config.fixed_burst_bytes,
            ),
            arq_block_bytes=self.adaptation_config.arq_block_bytes,
            on_log=self.on_log,
            codec=self.codec,
        )

    def _make_link(self, pipe) -> OfdmLink:
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
            controller=self.controller,
            legacy_mode=self.legacy_mode,
            codec=self.codec,
        )

    def _publish(self, status: OfdmStatus) -> None:
        self.status = status

    def _reset_status(self, direction: str) -> None:
        """Publish a clean transfer view before audio/QSY setup can block."""
        selected = self.controller.profile
        self.status = OfdmStatus(
            direction=direction,
            profile=self.profile.name,
            mcs=self.mcs_index,
            fec=fec_spec(selected.fec).label,
            burst_bytes=selected.burst_bytes,
            arq_block_bytes=selected.arq_block_bytes,
        )

    @staticmethod
    def _payload_of(msg) -> bytes:
        return (msg.payload_bytes if msg.payload_bytes is not None
                else msg.body.encode("utf-8"))

    # -- the PayloadBackend interface --------------------------------------

    def start_send(self, msg, done: DoneCb) -> None:
        threading.Thread(target=self._send, args=(msg, done), daemon=True).start()

    def start_receive(self, msg, done: DoneCb) -> None:
        threading.Thread(target=self._receive, args=(msg, done), daemon=True).start()

    def _send(self, msg, done: DoneCb) -> None:
        success = False
        acquired = False
        with self._transfer_lock:
            self._reset_status("send")
            pipe = None
            try:
                if self.on_acquire:
                    self.on_acquire()
                    acquired = True
                if self.on_qsy and self.on_qsy(msg) is False:
                    raise RuntimeError("working-channel QSY failed")
                data = self._payload_of(msg)
                pipe = self._make_pipe()
                pipe.start()
                self.on_log(
                    f"Guardian G2 {self.waveform_family.upper()}: sending "
                    f"#{msg.msg_id} ({len(data)} bytes) on "
                    f"profile {self.profile.name}, MCS{self.mcs_index}, "
                    f"{self.controller.summary()}, "
                    f"mode={'legacy v1' if self.legacy_mode else 'adaptive v2'}"
                )
                success = self._make_link(pipe).send_message(msg.msg_id, data)
            except Exception as exc:  # noqa: BLE001 - fail the transfer, not the app
                self.on_log(f"OFDM VHF send failed: {exc}")
            finally:
                if pipe is not None:
                    _swallow(pipe.stop)
                if self.on_unqsy:
                    _swallow(self.on_unqsy)
                if acquired and self.on_release:
                    _swallow(self.on_release)
        # done() may immediately send RECEIVED/CANCEL over AFSK, so it must run
        # only after the shared soundcard has been returned to that modem.
        done(success)

    def _receive(self, msg, done: DoneCb) -> None:
        success = False
        acquired = False
        with self._transfer_lock:
            self._reset_status("receive")
            pipe = None
            try:
                if self.on_acquire:
                    self.on_acquire()
                    acquired = True
                if self.on_receive_qsy and self.on_receive_qsy(msg) is False:
                    raise RuntimeError("working-channel QSY failed")
                pipe = self._make_pipe()
                pipe.start()
                self.on_log(f"OFDM VHF: waiting for payload #{msg.msg_id}")
                received = self._make_link(pipe).receive_message(msg_id=msg.msg_id)
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
        done(success)

    def cancel(self, msg) -> None:
        """No-op, as for VARA.

        The orchestrator's `cancel_message` sends a CANCEL control frame and
        never calls this; an in-flight transfer runs to its own conclusion. Wiring
        a real abort means a stop flag the ARQ loop checks between blocks, which
        is worth doing when the session layer grows a use for it.
        """
