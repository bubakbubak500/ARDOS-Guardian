"""UI-independent operational controller for radio, VARA and ARDOS sessions."""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .config import StationConfig
from .install.dependencies import find_vara_fm, find_vara_hf
from .i18n import dual
from .message import Folder, MessageStore, Status
from .modem import make_modem
from .modem.audio import AudioControlTransport, resolve_device
from .payload import make_backend
from .protocol import Priority
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

WinlinkPrompt = Callable[[str, object, Callable[[bool], None]], None]


class Operations:
    """Own hardware objects while exposing only non-blocking UI commands."""

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
        self.vara.on_ptt = self._radio_ptt if config.vara_host_ptt else None
        self.audio_transport: AudioControlTransport | None = None
        self._radio_lock = threading.RLock()
        self._last_radio_poll = 0.0
        self._stored_inbound: set[int] = set()
        self._qsy_previous: int | None = None
        self._vara_process: subprocess.Popen | None = None
        self.winlink_prompt: WinlinkPrompt | None = None
        self.net = self._build_net(NullTransport())

    def _log(
        self,
        message: str,
        level: LogLevel = LogLevel.INFO,
        *,
        source: str = "operation",
    ) -> None:
        self.events.publish(message, level=level, source=source)

    def _build_net(self, transport) -> Orchestrator:
        return Orchestrator(
            self.config.callsign,
            transport,
            routes=self.routes,
            on_event=self._session_event,
            payload=self._make_payload_backend(),
            heard=self.heard,
            auto_route=self.config.auto_route,
            relay=self.config.auto_relay,
        )

    def _make_payload_backend(self):
        if self.config.payload_backend == "winlink_manual":
            acquire, release = self._winlink_acquire, self._winlink_release
        else:
            acquire, release = self._suspend_control, self._resume_control
        return make_backend(
            self.config.payload_backend,
            vara=self.vara,
            prompt=self._prompt_winlink,
            on_log=lambda value: self._log(value, source="payload"),
            on_qsy=self._qsy_to,
            # Keep the radio on the peer's channel until the control-layer
            # RECEIVED/DELIVERED confirmation arrives.
            on_unqsy=None,
            on_acquire=acquire,
            on_release=release,
        )

    def _prompt_winlink(self, role: str, message, done) -> None:
        if self.winlink_prompt is None:
            self._log(
                dual(
                    "Winlink hand-off needs operator confirmation.",
                    "Předání službě Winlink vyžaduje potvrzení operátora.",
                ),
                LogLevel.WARNING,
                source="winlink",
            )
            done(False)
            return
        self.winlink_prompt(role, message, done)

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
            if self.config.callsign != "NOCALL":
                self.vara.set_mycall(self.config.callsign)
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
        self.request_radio_poll(now=now)
        self._update_vara_snapshot()
        self._update_network_snapshot(now)

    def request_radio_poll(
        self,
        *,
        now: float | None = None,
        force: bool = False,
    ) -> bool:
        now = time.monotonic() if now is None else now
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
        self._log(f"[VARA] {text}", source="vara")

    def _radio_ptt(self, enabled: bool) -> None:
        try:
            self.radio.set_ptt(enabled)
        except Exception as exc:
            self._log(f"PTT error: {exc}", LogLevel.ERROR, source="radio")

    def _suspend_control(self) -> None:
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

    def _resume_control(self) -> None:
        if self.audio_transport is not None:
            try:
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

    def _winlink_acquire(self) -> None:
        self._suspend_control()
        if self.config.vara_handoff_com:
            self.radio.close()
            self.rigctld.stop()

    def _winlink_release(self) -> None:
        if self.config.vara_handoff_com:
            self.connect_radio()
        self._resume_control()

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
