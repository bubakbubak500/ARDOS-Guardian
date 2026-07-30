"""UI-independent operational controller for radio, VARA and ARDOS sessions."""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import StationConfig, config_dir
from .install.dependencies import find_vara_fm, find_vara_hf
from .i18n import dual
from .message import Folder, MessageStore, Status
from .modem import make_modem
from .modem.audio import AudioControlTransport, resolve_device
from .payload import make_backend
from .protocol import (
    MAX_CONTROL_FRAME_BYTES,
    Priority,
    alert_kind,
    decode_alert,
    max_note_length,
)
from .radio import make_driver
from .radio.rigctld_launcher import RigctldProcess
from .routing import HeardStations, RouteTable
from .services import (
    EventBus,
    LogLevel,
    NetworkSnapshot,
    RadioSnapshot,
    SnapshotStore,
    TaskResult,
    VaraSnapshot,
    WorkerPool,
)
from .session import NullTransport, Orchestrator, SessionState
from .vara import VaraClient


@dataclass(frozen=True)
class AlertRecord:
    """A net alert as the operator should see it."""

    code: int
    note: str
    source: str
    received: float          # wall clock, for the banner timestamp
    mine: bool               # this station originated it

    @property
    def priority(self) -> Priority:
        kind = alert_kind(self.code)
        return kind.priority if kind else Priority.ROUTINE


# The banner shows the newest; the rest stay for the log and for the operator
# scrolling back after a busy few minutes.
_ALERT_HISTORY = 20

# An alert only reaches the stations that are listening where we are tuned. If
# the route table names other working frequencies, the same alert is repeated
# on each of them and the radio is put back where it started. The cap is a
# safety rail, not an expectation: a real net has a handful of channels, and a
# route table with a hundred must not key the radio for half an hour.
ALERT_SWEEP_MAX_CHANNELS = 10
# Per channel: how many copies, how far apart, and how long to let the rig
# settle after a QSY before keying it.
ALERT_SWEEP_BURSTS = 2
ALERT_SWEEP_GAP = 3.0
ALERT_SWEEP_SETTLE = 0.6
# The home-frequency repeats are spaced by the orchestrator's tick loop. The
# sweep waits for them rather than tuning away mid-flood, but never forever --
# a stalled queue must not strand the alert on one channel.
ALERT_SWEEP_HOME_WAIT = 45.0
ALERT_SWEEP_TX_WAIT = 30.0


class Operations:
    """Own hardware objects while exposing only non-blocking UI commands."""

    # Auto-delivery sweeps this often; a heard peer is not going anywhere.
    _AUTO_DELIVER_INTERVAL = 10.0

    def __init__(
        self,
        config: StationConfig,
        events: EventBus,
        snapshots: SnapshotStore,
        workers: WorkerPool,
        mailstore: MessageStore,
        routes: RouteTable,
        heard: HeardStations,
    ) -> None:
        self.config = config
        self.events = events
        self.snapshots = snapshots
        self.workers = workers
        self.mailstore = mailstore
        self.routes = routes
        self.heard = heard
        self.radio = make_driver(config)
        self.rigctld = RigctldProcess(config.rigctld_path)
        self.vara = VaraClient(
            config.vara_host,
            config.vara_cmd_port,
            config.vara_data_port,
        )
        self.vara.on_notification = self._on_vara_notification
        self.configure_vara_host_ptt()
        self.audio_transport: AudioControlTransport | None = None
        self._radio_lock = threading.RLock()
        self._payload_active = threading.Event()
        self._last_radio_poll = 0.0
        self._stored_inbound: set[int] = set()
        self._qsy_previous: int | None = None
        self._last_beacon = 0.0
        self._last_auto_deliver = 0.0
        # Sent once per run so a peer that stays heard is not hammered.
        self._auto_delivered: set[int] = set()
        self._vara_process: subprocess.Popen | None = None
        self.alerts: list[AlertRecord] = []      # newest first
        self.net = self._build_net(NullTransport())

    def _log(
        self,
        message: str,
        level: LogLevel = LogLevel.INFO,
        *,
        source: str = "operation",
    ) -> None:
        self.events.publish(message, level=level, source=source)

    # A HAVE_MSG is 0.9 s on AFSK 1200 but 5.2 s on MFSK-16, and an exchange is
    # announce + ack back to back. Budget both legs plus PTT turnaround, or the
    # HF handshake times out before the peer can finish answering.
    _CONTROL_FRAME_BYTES = MAX_CONTROL_FRAME_BYTES
    _TURNAROUND = 3.0

    def _scale_session_timeouts(self, net: Orchestrator, transport) -> None:
        modem = getattr(transport, "modem", None)
        airtime = getattr(modem, "airtime", None)
        if airtime is None:
            return
        exchange = 2 * airtime(self._CONTROL_FRAME_BYTES) + self._TURNAROUND
        net.ack_timeout = max(net.ack_timeout, exchange)
        net.start_timeout = max(net.start_timeout, exchange)

    def _build_net(self, transport) -> Orchestrator:
        net = Orchestrator(
            self.config.callsign,
            transport,
            routes=self.routes,
            on_event=self._session_event,
            payload=self._make_payload_backend(),
            heard=self.heard,
            auto_route=self.config.auto_route,
            relay=self.config.auto_relay,
        )
        net.on_alert = self._on_alert
        net.channel_frequency = self.current_frequency
        self._scale_session_timeouts(net, transport)
        return net

    # ----- net-wide alerts ------------------------------------------------

    def max_alert_note(self) -> int:
        """Note characters that fit beside this station's callsign."""
        return max_note_length(self.config.callsign)

    def send_alert(self, code: int, note: str = "", *, sweep: bool = True) -> bool:
        """Broadcast an alert to everyone on the current frequency.

        With `sweep`, the same alert is then repeated on every other frequency
        the route table knows -- see `_alert_sweep`.
        """
        if self.audio_transport is None:
            self._log(
                dual(
                    "Alert not sent: the control channel is not running.",
                    "Výstraha neodeslána: řídicí kanál neběží.",
                ),
                level=LogLevel.WARNING,
            )
            return False
        frame = self.net.send_alert(code, note)
        if sweep:
            self._start_alert_sweep(frame)
        return True

    def current_frequency(self) -> int | None:
        """Where the radio is tuned according to the last CAT poll."""
        return self.snapshots.read().radio.frequency_hz

    def alert_sweep_channels(self) -> list[tuple[int, str]]:
        """Other frequencies from the route table an alert should also reach."""
        here = self.current_frequency()
        channels = [
            (freq, mode)
            for freq, mode in self.routes.frequencies()
            if freq != here
        ]
        return channels[:ALERT_SWEEP_MAX_CHANNELS]

    def _start_alert_sweep(self, frame) -> None:
        channels = self.alert_sweep_channels()
        if not channels:
            return
        net, transport = self.net, self.audio_transport
        submitted = self.workers.submit(
            "alert-sweep",
            lambda: self._alert_sweep(frame, channels, net, transport),
            self._alert_sweep_finished,
        )
        if not submitted:
            self._log(
                dual(
                    "A frequency sweep is already running; this alert stays on "
                    "the current frequency.",
                    "Přeladění už probíhá; tato výstraha zůstane na současném "
                    "kmitočtu.",
                ),
                LogLevel.WARNING,
                source="alert",
            )
            return
        self._log(
            dual(
                f"Alert will also be repeated on {len(channels)} other known "
                "frequencies.",
                f"Výstraha bude zopakována i na {len(channels)} dalších známých "
                "kmitočtech.",
            ),
            source="alert",
        )

    def _alert_sweep(self, frame, channels, net, transport) -> int:
        """Repeat one alert on every other frequency the route table knows.

        Runs on a worker thread, and every channel is attempted independently:
        a rig that will not tune, a QSY that times out or a channel that fails
        mid-burst costs that one channel and nothing more, because the point of
        the sweep is reach. The radio goes back to where it started afterwards,
        including when the sweep is cut short.
        """
        home = self._read_channel()
        reached = 0
        try:
            # Tuning away mid-flood would strand the queued home repeats.
            self._wait_for_queued_alerts(net, transport)
            for freq, mode in channels:
                if not self._sweep_may_continue(net, transport):
                    self._log(
                        dual(
                            "Alert sweep stopped: the control channel changed.",
                            "Přeladění zastaveno: řídicí kanál se změnil.",
                        ),
                        LogLevel.WARNING,
                        source="alert",
                    )
                    break
                if self._alert_on_channel(frame, freq, mode, net, transport):
                    reached += 1
        finally:
            self._return_to_channel(home)
        return reached

    def _alert_on_channel(self, frame, freq: int, mode: str, net, transport) -> bool:
        megahertz = freq / 1_000_000
        try:
            with self._radio_lock:
                self.radio.set_frequency(freq)
                if mode:
                    self.radio.set_mode(mode)
            self.request_radio_poll(force=True)
            time.sleep(ALERT_SWEEP_SETTLE)
            for burst in range(ALERT_SWEEP_BURSTS):
                net.retransmit_alert(frame)
                transport.wait_tx_idle(timeout=ALERT_SWEEP_TX_WAIT)
                if burst + 1 < ALERT_SWEEP_BURSTS:
                    time.sleep(ALERT_SWEEP_GAP)
        except Exception as exc:  # noqa: BLE001 - one channel must not end the sweep
            self._log(
                dual(
                    f"Alert not repeated on {megahertz:.4f} MHz: {exc}",
                    f"Výstraha nezopakována na {megahertz:.4f} MHz: {exc}",
                ),
                LogLevel.WARNING,
                source="alert",
            )
            return False
        self._log(
            dual(
                f"Alert repeated on {megahertz:.4f} MHz"
                + (f" {mode}." if mode else "."),
                f"Výstraha zopakována na {megahertz:.4f} MHz"
                + (f" {mode}." if mode else "."),
            ),
            source="alert",
        )
        return True

    def _sweep_may_continue(self, net, transport) -> bool:
        """Stop if the channel we started on is no longer the live one."""
        return (
            self.net is net
            and self.audio_transport is transport
            and not self._payload_active.is_set()
        )

    def _wait_for_queued_alerts(self, net, transport) -> None:
        deadline = time.monotonic() + ALERT_SWEEP_HOME_WAIT
        while time.monotonic() < deadline:
            if not net.alerts_pending() and transport.wait_tx_idle(timeout=1.0):
                return
            time.sleep(0.25)

    def _read_channel(self) -> tuple[int, str] | None:
        """Where the operator had the radio, so the sweep can hand it back."""
        try:
            with self._radio_lock:
                state = self.radio.get_state()
        except Exception:  # noqa: BLE001 - no CAT, nothing to restore
            return None
        if not state.frequency_hz:
            return None
        return int(state.frequency_hz), (state.mode or "")

    def _return_to_channel(self, home: tuple[int, str] | None) -> None:
        if home is None:
            return
        frequency, mode = home
        try:
            with self._radio_lock:
                self.radio.set_frequency(frequency)
                # The sweep may have crossed a band: a rig left on USB after an
                # HF hop would be deaf on the FM channel the operator was on.
                if mode:
                    self.radio.set_mode(mode)
        except Exception as exc:  # noqa: BLE001
            self._log(
                dual(
                    f"Radio was left off its original frequency: {exc}",
                    f"Rádio zůstalo mimo původní kmitočet: {exc}",
                ),
                LogLevel.ERROR,
                source="alert",
            )
            return
        self.request_radio_poll(force=True)

    def _alert_sweep_finished(self, result: TaskResult) -> None:
        if result.error:
            self._log(
                dual(
                    f"Alert frequency sweep failed: {result.error}",
                    f"Přeladění výstrahy selhalo: {result.error}",
                ),
                LogLevel.ERROR,
                source="alert",
            )
            return
        self._log(
            dual(
                f"Alert sweep finished on {result.value} extra frequencies.",
                f"Přeladění výstrahy dokončeno na {result.value} dalších kmitočtech.",
            ),
            source="alert",
        )

    def _on_alert(self, frame, mine: bool) -> None:
        code, note = decode_alert(frame.destination)
        self.alerts.insert(
            0,
            AlertRecord(
                code=code,
                note=note,
                source=frame.source,
                received=time.time(),
                mine=mine,
            ),
        )
        del self.alerts[_ALERT_HISTORY:]
        kind = alert_kind(code)
        label = kind.key if kind else f"0x{code:02X}"
        detail = f" \"{note}\"" if note else ""
        self._log(
            dual(
                f"Alert {label} from {frame.source}{detail}",
                f"Výstraha {label} od {frame.source}{detail}",
            ),
            level=(
                LogLevel.WARNING
                if not mine
                else LogLevel.INFO
            ),
            source="alert",
        )

    def apply_vara_session_settings(self) -> bool:
        """Push the per-session tuning commands VARA cannot infer.

        Sent when VARA connects *and* whenever the operator changes them --
        VARA has no way to learn a setting that so far only existed in
        Guardian's config, so before 0.6.33 changing the HF bandwidth left the
        modem on whatever it was given at connect time.

        `CHAT OFF` bounds VARA's idle loops. Compression stays on: Guardian
        pads every envelope to MIN_WIRE_SIZE and that padding is otherwise
        pure airtime. Bandwidth and `P2P SESSION` are HF/SAT only -- the
        reference is explicit that P2P "must be used for P2P connections, not
        for Gateways connections", and FM answers WRONG to a BW command.
        """
        if not self.vara.connected:
            return False
        if self.vara.state.link_state != "DISCONNECTED":
            # These are session-level commands; the reference warns that
            # changing session state mid-connection drops the link.
            self._log(
                dual(
                    "VARA settings will apply after the current link closes.",
                    "Nastavení VARA se projeví po ukončení stávajícího spojení.",
                ),
                LogLevel.WARNING,
                source="vara",
            )
            return False
        self.vara.send_command("PUBLIC ON")
        self.vara.send_command("COMPRESSION TEXT")
        self.vara.send_command("CHAT OFF")
        if self.config.vara_mode.upper() == "HF":
            self.vara.send_command(self.config.vara_hf_bandwidth)
            self.vara.send_command("P2P SESSION")
        return True

    def vara_endpoint(self) -> tuple:
        """Which VARA instance we talk to. A change here needs a reconnect."""
        return (
            self.config.vara_mode.upper(),
            self.config.vara_host,
            self.config.vara_cmd_port,
            self.config.vara_data_port,
        )

    def vara_tuning(self) -> tuple:
        """Settings VARA holds per session. A change here can be re-sent."""
        return (self.config.vara_hf_bandwidth,)

    def _make_payload_backend(self):
        return make_backend(
            self.config.payload_backend,
            vara=self.vara,
            on_log=lambda value: self._log(value, source="payload"),
            on_qsy=self._qsy_to,
            # Keep the radio on the peer's channel until the control-layer
            # RECEIVED/DELIVERED confirmation arrives.
            on_unqsy=None,
            on_acquire=self._suspend_control,
            on_release=self._resume_control,
        )

    def connect_radio(self) -> bool:
        radio = self.radio
        config = self.config

        def operation() -> list[str]:
            messages: list[str] = []
            with self._radio_lock:
                if config.radio_backend == "hamlib":
                    messages.append(
                        self.rigctld.ensure(
                            config.rig_model,
                            config.cat_port,
                            config.rigctld_port,
                            config.cat_baud,
                        )
                    )
                radio.open()
            return messages

        def completed(result: TaskResult) -> None:
            if result.error:
                self._log(
                    dual(
                        f"Radio connect failed: {result.error}",
                        f"Připojení rádia selhalo: {result.error}",
                    ),
                    LogLevel.ERROR,
                    source="radio",
                )
            else:
                for message in result.value:
                    self._log(message, source="radio")
                self._log(dual(
                    f"Radio connected via {radio.name}.",
                    f"Rádio připojeno přes {radio.name}.",
                ), source="radio")
            self.request_radio_poll(force=True)

        submitted = self.workers.submit("radio-control", operation, completed)
        if submitted:
            self._log(dual("Connecting radio…", "Připojuji rádio…"), source="radio")
        return submitted

    def disconnect_radio(self) -> bool:
        def operation() -> None:
            with self._radio_lock:
                self.radio.close()

        def completed(result: TaskResult) -> None:
            if result.error:
                self._log(
                    dual(
                        f"Radio disconnect failed: {result.error}",
                        f"Odpojení rádia selhalo: {result.error}",
                    ),
                    LogLevel.ERROR,
                    source="radio",
                )
            else:
                self._log(dual("Radio disconnected.", "Rádio odpojeno."), source="radio")
            self.request_radio_poll(force=True)

        return self.workers.submit("radio-control", operation, completed)

    def connect_vara(self) -> bool:
        def operation() -> str | None:
            self.vara.host = self.config.vara_host
            self.vara.cmd_port = self.config.vara_cmd_port
            self.vara.data_port = self.config.vara_data_port
            started = None
            try:
                self.vara.connect(timeout=0.5)
            except OSError as first_error:
                executable = self._selected_vara_executable()
                if executable is None:
                    mode = self.config.vara_mode.upper()
                    raise RuntimeError(
                        dual(
                            f"VARA {mode} is not running and its executable "
                            "is not available",
                            f"VARA {mode} neběží a její program není k dispozici",
                        )
                    ) from first_error
                if self._vara_process is None or self._vara_process.poll() is not None:
                    self._vara_process = subprocess.Popen(
                        [executable],
                        cwd=str(Path(executable).parent),
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    started = f"VARA {self.config.vara_mode.upper()}"
                deadline = time.monotonic() + 10.0
                last_error: OSError = first_error
                while time.monotonic() < deadline:
                    time.sleep(0.25)
                    try:
                        self.vara.connect(timeout=0.5)
                        break
                    except OSError as exc:
                        last_error = exc
                else:
                    raise TimeoutError(
                        f"{self.config.vara_mode.upper()} did not open "
                        f"{self.config.vara_host}:{self.config.vara_cmd_port}"
                    ) from last_error
            if self.vara.connected:
                # "VARA Protocol Native TNC Commands" (EA5HVK, 2025-10-10)
                # documents the initialization order as MYCALL, LISTEN ON,
                # CONNECT.
                self.apply_vara_session_settings()
                if self.config.callsign != "NOCALL":
                    self.vara.set_mycall(self.config.callsign)
                self.vara.listen(True)
            return started

        def completed(result: TaskResult) -> None:
            if result.error:
                self._log(
                    dual(
                        f"VARA connect failed: {result.error}",
                        f"Připojení VARA selhalo: {result.error}",
                    ),
                    LogLevel.ERROR,
                    source="vara",
                )
            else:
                if result.value:
                    self._log(
                        dual(
                            f"{result.value} was started by Guardian.",
                            f"{result.value} byla spuštěna Guardianem.",
                        ),
                        source="vara",
                    )
                self._log(
                    dual(
                        f"VARA connected at {self.config.vara_host}:"
                        f"{self.config.vara_cmd_port}.",
                        f"VARA připojena na {self.config.vara_host}:"
                        f"{self.config.vara_cmd_port}.",
                    ),
                    source="vara",
                )
            self._update_vara_snapshot()

        return self.workers.submit("vara-control", operation, completed)

    def _selected_vara_executable(self) -> str | None:
        host = self.config.vara_host.strip().lower()
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return None
        if self.config.vara_mode.upper() == "HF":
            return find_vara_hf(self.config.vara_hf_path)
        return find_vara_fm(self.config.vara_fm_path)

    def disconnect_vara(self) -> bool:
        def completed(result: TaskResult) -> None:
            if result.error:
                self._log(
                    dual(
                        f"VARA disconnect failed: {result.error}",
                        f"Odpojení VARA selhalo: {result.error}",
                    ),
                    LogLevel.ERROR,
                    source="vara",
                )
            else:
                self._log(dual("VARA disconnected.", "VARA odpojena."), source="vara")
            self._update_vara_snapshot()

        return self.workers.submit(
            "vara-control",
            self.vara.disconnect,
            completed,
        )

    def start_control_channel(self) -> bool:
        if self.audio_transport is not None:
            return True
        modem = make_modem(self.config.active_modem())
        input_device = (
            resolve_device(self.config.audio_input, "input")
            if self.config.audio_input
            else None
        )
        output_device = (
            resolve_device(self.config.audio_output, "output")
            if self.config.audio_output
            else None
        )
        if not isinstance(input_device, int):
            self._log(
                dual(
                    "Audio control channel failed: select an available RX input.",
                    "Zvukový řídicí kanál selhal: vyberte dostupný RX vstup.",
                ),
                LogLevel.ERROR,
                source="control",
            )
            return False
        if not isinstance(output_device, int):
            self._log(
                dual(
                    "Audio control channel failed: select an available TX output.",
                    "Zvukový řídicí kanál selhal: vyberte dostupný TX výstup.",
                ),
                LogLevel.ERROR,
                source="control",
            )
            return False
        transport = AudioControlTransport(
            modem=modem,
            ptt=self._radio_ptt,
            sample_rate=modem.fs if hasattr(modem, "fs") else 48_000,
            input_device=input_device,
            output_device=output_device,
            diagnostic_audio_path=config_dir() / "last-bad-control.wav",
            on_log=lambda value: self._log(value, source="control"),
        )
        try:
            transport.start()
        except Exception as exc:
            self._log(
                dual(
                    f"Audio control channel failed: {exc}",
                    f"Zvukový řídicí kanál selhal: {exc}",
                ),
                LogLevel.ERROR,
                source="control",
            )
            return False
        self.audio_transport = transport
        self.config.control_channel = "audio"
        self.config.save()
        self.net = self._build_net(transport)
        self._log(
            dual(
                f"Audio control channel active ({modem.name}).",
                f"Zvukový řídicí kanál je aktivní ({modem.name}).",
            ),
            source="control",
        )
        self._update_network_snapshot()
        return True

    def restart_control_channel(self) -> bool:
        """Reopen active control audio after an endpoint setting changes."""
        if self.audio_transport is None:
            return True
        self.audio_transport.stop()
        self.audio_transport = None
        self.net = self._build_net(NullTransport())
        self._log(
            dual(
                "Audio devices changed; reopening the control channel.",
                "Zvuková zařízení se změnila; znovu otevírám řídicí kanál.",
            ),
            source="control",
        )
        if self.start_control_channel():
            return True
        self.config.control_channel = "off"
        self.config.save()
        self._update_network_snapshot()
        return False

    def stop_control_channel(self) -> None:
        if self.audio_transport is not None:
            self.audio_transport.stop()
        self.audio_transport = None
        self.config.control_channel = "off"
        self.config.save()
        self.net = self._build_net(NullTransport())
        self._log(dual(
            "Audio control channel stopped.",
            "Zvukový řídicí kanál byl zastaven.",
        ), source="control")
        self._update_network_snapshot()

    def send_queued(self, message_id: int) -> bool:
        if self.audio_transport is None:
            self._log(
                dual(
                    "Start the audio control channel before sending.",
                    "Před odesláním spusťte zvukový řídicí kanál.",
                ),
                LogLevel.WARNING,
                source="mail",
            )
            return False
        mail = self.mailstore.get(message_id)
        if mail is None:
            return False
        route = self.routes.lookup(mail.final_dest)
        direct_route = route is not None and (
            not route.preferred or route.preferred == mail.final_dest.strip().upper()
        )
        if direct_route and route.freq_hz and self.config.auto_qsy:
            if not self._qsy_to(mail.final_dest):
                self._log(
                    dual(
                        f"Message #{mail.msg_id} was not sent because direct QSY failed.",
                        f"Zpráva #{mail.msg_id} nebyla odeslána, protože přímé QSY selhalo.",
                    ),
                    LogLevel.ERROR,
                    source="mail",
                )
                return False
        self.mailstore.set_status(message_id, status=Status.SENDING)
        self.net.send_message(
            final_dest=mail.final_dest,
            body=mail.subject,
            msg_id=mail.msg_id,
            priority=Priority(mail.priority),
            ttl=self.config.default_ttl,
            payload_bytes=mail.to_bundle(),
        )
        self._log(
            dual(
                f"Message #{mail.msg_id} to {mail.final_dest} announced "
                f"({mail.content_size()} B payload).",
                f"Zpráva #{mail.msg_id} pro {mail.final_dest} oznámena "
                f"(datový obsah {mail.content_size()} B).",
            ),
            source="mail",
        )
        return True

    def tick(self) -> None:
        now = time.monotonic()
        if self.audio_transport is not None:
            self.audio_transport.pump()
        self.net.tick(now)
        self._tick_beacon(now)
        self._tick_auto_deliver(now)
        self.request_radio_poll(now=now)
        self._update_vara_snapshot()
        self._update_network_snapshot(now)

    def _net_idle(self) -> bool:
        """True when it is safe for Guardian to start transmitting by itself.

        Both automatic behaviours below key the radio without an operator
        asking, so they only run with a live control channel, nothing already
        in flight, and no payload transfer holding the codec.
        """
        if self.audio_transport is None or self._payload_active.is_set():
            return False
        return not any(
            not message.state.terminal for message in self.net.sessions.values()
        )

    def _tick_beacon(self, now: float) -> None:
        """Announce presence so peers can hear this station and route to it."""
        if not self.config.beacon_enabled or not self._net_idle():
            return
        interval = max(15.0, float(self.config.beacon_interval))
        if now - self._last_beacon < interval:
            return
        self._last_beacon = now
        try:
            self.net.beacon()
        except Exception as exc:  # noqa: BLE001
            self._log(
                dual(f"Beacon failed: {exc}", f"Maják selhal: {exc}"),
                LogLevel.WARNING,
                source="network",
            )
            return
        self._log(
            dual("Presence beacon sent.", "Odeslán maják přítomnosti."),
            source="network",
        )

    def _tick_auto_deliver(self, now: float) -> None:
        """Send waiting mail as soon as its next hop is actually heard.

        Only one message per sweep, and only to a station heard right now --
        the point is to catch a peer coming on air, not to retry blindly.
        """
        if not self.config.auto_deliver or not self._net_idle():
            return
        if now - self._last_auto_deliver < self._AUTO_DELIVER_INTERVAL:
            return
        self._last_auto_deliver = now
        heard = {station.callsign for station in self.heard.active(now)}
        if not heard:
            return
        for folder in (Folder.OUTBOX, Folder.TRANSIT):
            for meta in self.mailstore.list(folder):
                if meta.get("status") == Status.FAILED:
                    continue          # a failure is the operator's to retry
                msg_id = meta["msg_id"]
                if msg_id in self._auto_delivered:
                    continue
                hop = meta.get("next_hop") or meta.get("final_dest") or ""
                if hop.upper() not in heard:
                    continue
                self._auto_delivered.add(msg_id)
                self._log(
                    dual(
                        f"{hop} is heard — sending waiting message #{msg_id}.",
                        f"{hop} je slyšet — odesílám čekající zprávu #{msg_id}.",
                    ),
                    source="network",
                )
                self.send_queued(msg_id)
                return

    def request_radio_poll(
        self,
        *,
        now: float | None = None,
        force: bool = False,
    ) -> bool:
        now = time.monotonic() if now is None else now
        # VARA host-PTT must be serviced within a very short timing window.
        # Four periodic CAT getters can otherwise delay a PTT edge and clip the
        # peer's short ARQ ACK. The final snapshot is refreshed after handoff.
        if self._payload_active.is_set():
            return False
        if not force and now - self._last_radio_poll < 1.0:
            return False
        self._last_radio_poll = now
        radio = self.radio

        def operation():
            with self._radio_lock:
                return radio.get_state()

        def completed(result: TaskResult) -> None:
            if result.error:
                self.snapshots.update(
                    radio=RadioSnapshot(
                        connected=False,
                        name=radio.name,
                        error=str(result.error),
                    )
                )
                return
            state = result.value
            self.snapshots.update(
                radio=RadioSnapshot(
                    connected=state.connected,
                    name=radio.name,
                    frequency_hz=state.frequency_hz,
                    mode=state.mode,
                    ptt=state.ptt,
                    signal=state.signal,
                    error=state.error,
                )
            )

        return self.workers.submit("radio-poll", operation, completed)

    def _update_vara_snapshot(self) -> None:
        state = self.vara.state
        self.snapshots.update(
            vara=VaraSnapshot(
                command_connected=state.cmd_connected,
                data_connected=state.data_connected,
                mycall=state.mycall,
                link_state=state.link_state,
                last_notification=state.last_notification,
                transport_lost=state.transport_lost,
                tx_buffer_bytes=state.tx_buffer_bytes,
                buffer_reports=state.buffer_reports,
                rejected_commands=state.rejected_commands,
                data_socket_reopens=state.data_socket_reopens,
                tx_bitrate_bps=state.tx_bitrate_bps,
                data_bytes_written=state.data_bytes_written,
                data_bytes_read=state.data_bytes_read,
                data_socket_generation=state.data_socket_generation,
                data_local_endpoint=state.data_local_endpoint,
                data_peer_endpoint=state.data_peer_endpoint,
                ptt=state.ptt,
                ptt_keyings=state.ptt_keyings,
                error=state.error,
            )
        )

    def _update_network_snapshot(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.snapshots.update(
            network=NetworkSnapshot(
                active_sessions=sum(
                    not message.state.terminal
                    for message in self.net.sessions.values()
                ),
                heard_stations=len(self.heard.active(now)),
                control_channel_active=self.audio_transport is not None,
            )
        )

    def _session_event(self, message, event: str) -> None:
        self._log(
            f"[{message.source}#{message.msg_id}] {event}",
            source="session",
        )
        if message.direction == "out" and self.mailstore.get(message.msg_id):
            if message.state in (SessionState.DELIVERED, SessionState.CONFIRMED):
                self.mailstore.set_status(
                    message.msg_id,
                    status=Status.DELIVERED,
                    folder=Folder.SENT,
                )
            elif message.state is SessionState.FAILED:
                self.mailstore.set_status(
                    message.msg_id,
                    status=Status.FAILED,
                )
            if (
                message.state
                in (
                    SessionState.CONFIRMED,
                    SessionState.DELIVERED,
                    SessionState.FAILED,
                    SessionState.CANCELLED,
                )
                and self._qsy_previous is not None
            ):
                self._qsy_restore()
        if (
            message.direction == "in"
            and message.payload_bytes
            and message.msg_id not in self._stored_inbound
            and message.state in (SessionState.RECEIVED_OK, SessionState.DELIVERED)
        ):
            self._stored_inbound.add(message.msg_id)
            try:
                self.mailstore.store_incoming(
                    message.payload_bytes,
                    self.config.callsign,
                    via=message.source,
                )
            except Exception as exc:
                self._log(
                    dual(
                        f"Could not store incoming message #{message.msg_id}: {exc}",
                        f"Příchozí zprávu #{message.msg_id} nelze uložit: {exc}",
                    ),
                    LogLevel.ERROR,
                    source="mail",
                )

    def _on_vara_notification(self, text: str) -> None:
        # BUFFER can change many times per second during a transfer. Its latest
        # value is exposed in diagnostics; rendering every update would flood
        # the activity panel and make the Qt UI sluggish.
        if text.upper().startswith("BUFFER"):
            return
        self._log(f"[VARA] {text}", source="vara")

    def _radio_ptt(self, enabled: bool) -> None:
        try:
            with self._radio_lock:
                self.radio.set_ptt(enabled)
        except Exception as exc:
            self._log(f"PTT error: {exc}", LogLevel.ERROR, source="radio")

    def configure_vara_host_ptt(self) -> None:
        """Apply the saved VARA host-PTT preference to the live client."""
        self.vara.on_ptt = (
            self._radio_ptt if self.config.vara_host_ptt else None
        )

    def _suspend_control(self) -> None:
        self._payload_active.set()
        try:
            # Wait for a radio poll already in progress before VARA begins.
            # Future polls remain suppressed until the codec is returned.
            with self._radio_lock:
                pass
            if self.audio_transport is not None:
                if not self.audio_transport.wait_tx_idle(timeout=5.0):
                    raise TimeoutError(
                        dual(
                            "The pending control burst did not finish before VARA handoff.",
                            "Čekající řídicí rámec nebyl dokončen před předáním VARA.",
                        )
                    )
                self.audio_transport.stop()
                self._log(dual(
                    "Control audio released for payload.",
                    "Řídicí zvuk uvolněn pro datový přenos.",
                ), source="control")
        except Exception:
            self._payload_active.clear()
            raise

    def _resume_control(self) -> None:
        try:
            if self.audio_transport is not None:
                self.audio_transport.start()
                self._log(dual(
                    "Control audio resumed.",
                    "Řídicí zvuk byl obnoven.",
                ), source="control")
        except Exception as exc:
            self._log(
                dual(
                    f"Control audio resume failed: {exc}",
                    f"Obnovení řídicího zvuku selhalo: {exc}",
                ),
                LogLevel.ERROR,
                source="control",
            )
        finally:
            self._payload_active.clear()
            self.request_radio_poll(force=True)

    def _qsy_to(self, callsign: str) -> bool:
        if not self.config.auto_qsy:
            return False
        target = self.routes.freq_for(callsign)
        if target is None:
            return False
        frequency, mode = target
        try:
            if self._qsy_previous is None:
                self._qsy_previous = self.radio.get_state().frequency_hz
            self.radio.set_frequency(frequency)
            if mode:
                self.radio.set_mode(mode)
            self._log(
                dual(
                    f"Direct QSY for {callsign}: {frequency / 1_000_000:.4f} MHz"
                    + (f" {mode}" if mode else ""),
                    f"Přímé QSY pro {callsign}: {frequency / 1_000_000:.4f} MHz"
                    + (f" {mode}" if mode else ""),
                ),
                source="radio",
            )
            return True
        except Exception as exc:
            self._qsy_restore()
            self._log(
                dual(f"QSY skipped: {exc}", f"QSY přeskočeno: {exc}"),
                LogLevel.WARNING,
                source="radio",
            )
            return False

    def _qsy_restore(self) -> None:
        if self.config.auto_qsy and self._qsy_previous is not None:
            try:
                self.radio.set_frequency(self._qsy_previous)
            except Exception:
                pass
        self._qsy_previous = None

    def close(self) -> None:
        try:
            self.stop_control_channel()
        except Exception:
            pass
        try:
            self.vara.disconnect()
        except Exception:
            pass
        try:
            self.radio.close()
        except Exception:
            pass
        try:
            self.rigctld.stop()
        except Exception:
            pass
