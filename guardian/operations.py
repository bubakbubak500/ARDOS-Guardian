"""UI-independent operational controller for radio, VARA and ARDOS sessions."""

from __future__ import annotations

import secrets
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import StationConfig, config_dir
from .install.dependencies import find_vara_fm, find_vara_hf
from .i18n import dual
from .message import Folder, MessageStore, Status
from .modem import make_modem
from .modem.audio import (DEFAULT_SAMPLE_RATE, PTT_LEAD_SECONDS,
                          PTT_TAIL_SECONDS, AudioControlTransport,
                          _import_sounddevice, resolve_device,
                          transmit_waveform)
from .modem.recorder import AudioCapture, WavRecorder, capture_path
from .ofdm import profile_or_default
from .payload import make_backend
from .payload.negotiated import NegotiatedPayload
from .protocol import (
    MAX_CONTROL_FRAME_BYTES,
    MAX_PTT_DELAY_MS,
    ControlFrame,
    Flags,
    FrameType,
    Priority,
    alert_kind,
    decode_alert,
    max_note_length,
)
from .station_lab import (
    CalibrationMode,
    CalibrationReport,
    CalibrationState,
    GainJournal,
    ProbeCommand,
    ProbeResult,
    WindowsGainController,
    full_plan,
    quick_plan,
)
from .waveforms.config import profile_for as experimental_profile_for
from .radio import Channel, ChannelPlan, ChannelScanner, make_driver
from .radio.bands import band_for, same_band
from .radio.presets import DUMMY_MODEL, find_executable
from .radio.rigctld_launcher import RigctldProcess
from .routing import HeardStations, RouteTable
from .services import (
    EventBus,
    LogLevel,
    MailboxSnapshot,
    NetworkSnapshot,
    RadioSnapshot,
    SnapshotStore,
    TaskResult,
    VaraSnapshot,
    WorkerPool,
)
from .session import NullTransport, Orchestrator, SessionState
from .session.orchestrator import parse_working_channel_token, working_channel_token
from .vara import VaraClient
from .vara.settings import configure_encryption, read_encryption_status


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


@dataclass
class StationLabStatus:
    """Plain state snapshot polled by the station-lab workspace."""

    state: str = CalibrationState.IDLE.value
    peer: str = ""
    session_id: int = 0
    mode: str = CalibrationMode.QUICK.value
    progress: int = 0
    total: int = 0
    current: str = ""
    message: str = ""
    pending_offer: bool = False
    report: CalibrationReport | None = None
    report_json: str = ""
    report_csv: str = ""
    error: str = ""


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

# The PTT test is a deliberate carrier: long enough for the operator to see the
# rig switch and the SWR needle move, short enough that a forgotten click on a
# live antenna is harmless. The ceiling is a guard, not a setting.
PTT_TEST_SECONDS = 2.0
PTT_TEST_MAX_SECONDS = 5.0

# Give a complete control-frame round trip time to finish before retrying, and
# bound the attempts so an unanswered call cannot stay in "offering" forever.
# Retries carry Q2/Q3 or F2/F3 tokens, avoiding the audio transport's duplicate
# suppression while remaining compatible with the existing prefix-based mode.
CALIBRATION_OFFER_MIN_INTERVAL = 9.0
CALIBRATION_OFFER_ATTEMPTS = 3
# A CAL_PROBE is decoded before the final AFSK samples have necessarily left
# the peer's radio. Give the receiving worker time to discard that tail and
# arm the payload recorder before the measuring waveform starts.
CALIBRATION_RX_SETTLE_SECONDS = 0.35
CALIBRATION_PROBE_GUARD_SECONDS = 0.85
# A valid report returns in a few seconds; keep bounded DSP/AFSK headroom.
CALIBRATION_REPORT_TIMEOUT = 8.0


def control_mode_compatible(modem: str, mode: str) -> bool:
    """Whether one live control modem can be used on ``mode``.

    Retuning does not replace the audio modem.  Silently sending AFSK on an HF
    USB route (or MFSK on FM) would make both scanning and an alert sweep appear
    successful locally while putting unusable audio on air.
    """
    value = (mode or "").strip().upper().replace("-", "")
    active = (modem or "").strip().lower()
    if active == "mfsk16":
        return value in {"USB", "LSB", "PKTUSB", "PKTLSB", "DATAUSB", "DATALSB"}
    return value in {"FM", "NFM", "PKTFM", "DATAFM"}


class Operations:
    """Own hardware objects while exposing only non-blocking UI commands."""

    # Auto-delivery sweeps this often; a heard peer is not going anywhere.
    _AUTO_DELIVER_INTERVAL = 10.0
    # A failed automatic handoff stays queued and may be retried, but never in
    # a tight keying loop against a peer that remains visible in Heard.
    _AUTO_DELIVER_RETRY = 300.0

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
        # Set while the operator is capturing received audio to a file.
        self._recorder: WavRecorder | None = None
        self._recorder_capture: AudioCapture | None = None
        self._recording_suspended_control = False
        self._last_radio_poll = 0.0
        self._stored_inbound: set[int] = set()
        self._qsy_previous: int | None = None
        self._qsy_previous_mode: str = ""
        self._last_beacon = 0.0
        self._last_auto_deliver = 0.0
        self._auto_delivery_attempted: dict[int, float] = {}
        self._vara_process: subprocess.Popen | None = None
        # Negotiated slow-keying hold-off for the payload session in flight.
        self._payload_ptt_delay_ms = 0
        # Set by the Qt shell. Operations stays UI-independent, while every
        # no-CAT QSY still has a synchronous operator safety gate.
        self.confirm_manual_qsy: Callable[[str, int, str], bool] | None = None
        self.alerts: list[AlertRecord] = []      # newest first
        self.net = self._build_net(NullTransport())
        self.scanner: ChannelScanner | None = None
        self._scanner_home: Channel | None = None
        self._scanner_paused = False
        self._scanner_last_heard = 0.0
        self._scanner_generation = 0
        self.station_lab = StationLabStatus()
        self._calibration_lock = threading.RLock()
        self._calibration_cancel = threading.Event()
        self._calibration_report_ready = threading.Event()
        self._calibration_commands: dict[int, ProbeCommand] = {}
        self._calibration_results: dict[int, ProbeResult] = {}
        self._calibration_last_rx_command: ProbeCommand | None = None
        self._calibration_initiator = False
        self._calibration_offer_sent_at = 0.0
        self._calibration_offer_attempts = 0
        self._restore_calibration_gain_after_crash()

    def _restore_calibration_gain_after_crash(self) -> None:
        """Recover a mixer snapshot left armed by an interrupted AutoTune."""
        journal = GainJournal()
        snapshot = journal.load()
        if snapshot is None:
            return
        gain = WindowsGainController(self.config.audio_output)
        current = gain.snapshot()
        if not current.supported:
            self.station_lab.error = dual(
                "AutoTune found an unrestored Windows-volume snapshot, but the "
                "saved radio output is not currently available.",
                "AutoTune našel neobnovený stav hlasitosti Windows, ale uložený "
                "výstup rádia nyní není dostupný.",
            )
            return
        try:
            gain.restore(snapshot)
        except Exception as exc:  # noqa: BLE001 - preserve journal for next start
            self.station_lab.error = str(exc)
            return
        journal.clear()
        self._log(
            dual(
                "Restored Windows audio gain after an interrupted AutoTune session.",
                "Po přerušené relaci AutoTune byla obnovena hlasitost Windows.",
            ),
            LogLevel.WARNING,
            source="station-lab",
        )

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
        # A worst-case RREQ walks outward and its RREP walks back. Give every
        # hop one full frame plus radio turnaround; on MFSK this is much longer
        # than the legacy fixed eight-second one-hop offer window.
        discovery = getattr(net, "discovery", None)
        if discovery is None:
            return
        frame_time = airtime(self._CONTROL_FRAME_BYTES)
        legs = 2 * max(2, int(self.config.discovery_ttl))
        discovery.query_timeout = max(
            discovery.query_timeout,
            legs * (frame_time + self._TURNAROUND),
        )
        discovery.jitter_min = max(discovery.jitter_min, frame_time * 0.2)
        discovery.jitter_max = max(discovery.jitter_max, frame_time * 0.6)

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
            discovery_mode=self.config.discovery_mode,
            discovery_forward=self.config.discovery_forward,
            discovery_ttl=self.config.discovery_ttl,
            discovery_route_lifetime=self.config.discovery_route_lifetime,
            discovery_frame_budget=self.config.discovery_frame_budget,
            discovery_allowlist=set(self.config.discovery_allowlist),
            discovery_denylist=set(self.config.discovery_denylist),
            discovery_auto_use=self.config.discovery_auto_use,
            link_advert_enabled=self.config.link_advert_enabled,
            link_advert_interval=self.config.link_advert_interval,
            discovery_channel_active=self.audio_transport is not None,
        )
        net.on_alert = self._on_alert
        net.on_discovery_event = self._on_discovery_event
        net.channel_frequency = self.current_frequency
        net.ptt_delay_request = self._vara_keying_delay_request
        net.ofdm_payload_request = self._ofdm_payload_configured
        net.on_final_ack_sent = self._on_final_ack_sent
        net.position = self.beacon_position
        net.working_channel_offer = self._working_channel_offer
        net.working_channel_accept = self._working_channel_accept
        net.delivery_receipt_route = self._delivery_receipt_route
        net.on_calibration_frame = self._on_calibration_frame
        self._scale_session_timeouts(net, transport)
        return net

    def _delivery_receipt_route(self, message_id: int, final_dest: str) -> str:
        """Recover a receipt's reverse hop from persistent mail after restart."""
        mail = self.mailstore.get(message_id)
        if mail is None or mail.final_dest.strip().upper() != final_dest.strip().upper():
            return ""
        if mail.folder not in (Folder.OUTBOX, Folder.TRANSIT, Folder.SENT):
            return ""
        self.mailstore.set_status(
            message_id,
            status=Status.DELIVERED,
            folder=Folder.SENT,
        )
        self._log(
            dual(
                f"Final delivery of message #{message_id} confirmed.",
                f"Potvrzeno koncové doručení zprávy #{message_id}.",
            ),
            source="mail",
        )
        return mail.hops[-1] if mail.hops else ""

    def apply_network_settings(self) -> None:
        """Apply saved routing/channel behaviour without restarting control RX."""
        self.net.callsign = self.config.callsign.strip().upper()
        self.net.auto_route = self.config.auto_route
        self.net.relay = self.config.auto_relay
        self.net.configure_discovery(
            mode=self.config.discovery_mode,
            forward=self.config.discovery_forward,
            relay_enabled=self.config.auto_relay,
            max_ttl=self.config.discovery_ttl,
            route_lifetime=self.config.discovery_route_lifetime,
            frame_budget=self.config.discovery_frame_budget,
            allowlist=set(self.config.discovery_allowlist),
            denylist=set(self.config.discovery_denylist),
            auto_use=self.config.discovery_auto_use,
            link_advert_enabled=self.config.link_advert_enabled,
            link_advert_interval=self.config.link_advert_interval,
        )
        self.net.working_channel_offer = self._working_channel_offer
        self.net.working_channel_accept = self._working_channel_accept
        # A backend already executing owns its own reference. Replacing this
        # one therefore affects the next session without disrupting RF now.
        self.net.payload = self._make_payload_backend()

    def _on_discovery_event(self, event) -> None:
        self._log(
            dual(
                f"Discovery {event.kind}: {event.source} → "
                f"{event.destination or 'network'} ({event.detail}).",
                f"Discovery {event.kind}: {event.source} → "
                f"{event.destination or 'síť'} ({event.detail}).",
            ),
            source="discovery",
        )

    def discover_route(self, destination: str):
        """Start an operator-requested query only on a running control net."""
        if self.audio_transport is None:
            self._log(
                dual(
                    "Route discovery not started: the control channel is off.",
                    "Hledání trasy nebylo spuštěno: řídicí kanál je vypnutý.",
                ),
                level=LogLevel.WARNING,
                source="discovery",
            )
            return None
        return self.net.discover_route(destination)

    def approve_discovered_route(self, destination: str):
        return self.net.approve_discovered_route(destination)

    def clear_discovered_routes(self) -> None:
        self.net.discovery.clear_routes()

    def advertise_live_links(self) -> int:
        if self.audio_transport is None:
            self._log(
                dual(
                    "Neighbour advertisement not sent: the control channel is off.",
                    "Oznámení sousedů nebylo odesláno: řídicí kanál je vypnutý.",
                ),
                level=LogLevel.WARNING,
                source="discovery",
            )
            return 0
        now = getattr(self.net, "_now", time.monotonic())
        return self.net.discovery.advertise_neighbors(
            [
                (station.callsign, station.last_snr)
                for station in self.heard.active(now)
            ],
            force=True,
        )

    def clear_live_topology(self) -> None:
        self.net.discovery.clear_live_topology()

    def beacon_position(self) -> str:
        """The locator our beacons carry, or "" to keep it off the air."""
        if not self.config.beacon_position:
            return ""
        return (self.config.station_grid or "").strip().upper()

    def _vara_keying_delay_request(self) -> int:
        """Slow-keying hold-off this station asks its peer for, in ms.

        Zero unless the operator configured one *and* the band is FM: HF
        stations run better radios and the extra dead air would only slow the
        net down, so the request never leaves an HF configuration.
        """
        if self.config.vara_mode.upper() != "FM":
            return 0
        return max(0, min(int(self.config.vara_ptt_delay_ms or 0), MAX_PTT_DELAY_MS))

    def _ofdm_payload_configured(self) -> bool:
        """Whether this station announces the experimental OFDM transport.

        Read at call time rather than captured, so switching transports in
        Settings takes effect on the next announcement without rebuilding the net.
        """
        return self.config.payload_backend == "ofdm_vhf"

    def _on_final_ack_sent(self, message) -> None:
        """Append one 40 WPM CW ID after the final destination's ACK frames."""
        if not self.config.morse_id_after_ack:
            return
        transport = self.audio_transport
        sender = getattr(message, "source", "").strip().upper()
        mine = self.config.callsign.strip().upper()
        if transport is None or not sender or not mine or mine == "NOCALL":
            self._log(
                dual(
                    "Post-transfer Morse ID skipped: the live control audio "
                    "channel and both callsigns are required.",
                    "Morse identifikace po přenosu byla přeskočena: je nutný "
                    "živý zvukový řídicí kanál a obě volací značky.",
                ),
                LogLevel.WARNING,
                source="session",
            )
            return
        text = f"{sender} DE {mine}"
        if transport.send_morse_after_pending(text, wpm=40.0):
            self._log(
                dual(
                    f"Queued final Morse ID: {text} (40 WPM).",
                    f"Zařazena závěrečná Morse identifikace: {text} (40 WPM).",
                ),
                source="session",
            )

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
        if self.scanner is not None:
            self._log(
                dual(
                    "Alert not sent: stop the channel scanner first.",
                    "Výstraha neodeslána: nejprve zastavte scanner kanálů.",
                ),
                LogLevel.WARNING,
                source="alert",
            )
            return False
        frame = self.net.send_alert(code, note)
        if sweep:
            self._start_alert_sweep(frame)
        return True

    def current_frequency(self) -> int | None:
        """Where the radio is tuned according to CAT or the no-CAT operator."""
        if self.is_no_cat_radio():
            return int(self.config.manual_frequency_hz or 0) or None
        return self.snapshots.read().radio.frequency_hz

    def scanner_channels(self) -> list[Channel]:
        """Freeze the current channel and compatible route frequencies."""
        snapshot = self.snapshots.read().radio
        home_frequency = self.current_frequency()
        if not home_frequency:
            return []
        fallback_mode = "USB" if self.config.active_modem() == "mfsk16" else "FM"
        home_mode = (snapshot.mode or fallback_mode).strip().upper()
        if not control_mode_compatible(self.config.active_modem(), home_mode):
            return []
        channels = [Channel("Home", int(home_frequency), home_mode)]
        seen = {int(home_frequency)}
        for frequency, mode in self.routes.frequencies():
            frequency = int(frequency)
            normal_mode = (mode or fallback_mode).strip().upper()
            if frequency in seen or not control_mode_compatible(
                self.config.active_modem(), normal_mode
            ):
                continue
            seen.add(frequency)
            channels.append(
                Channel(f"{frequency / 1_000_000:.4f} MHz", frequency, normal_mode)
            )
        return channels

    def start_scanner(self) -> bool:
        """Start receive-only scanning after an explicit operator action."""
        if self.scanner is not None and self.scanner.enabled:
            return True
        snapshot = self.snapshots.read()
        reason = ""
        if self.audio_transport is None:
            reason = dual(
                "start the live control channel first",
                "nejprve spusťte živý řídicí kanál",
            )
        elif self.config.radio_backend != "hamlib" or self.is_no_cat_radio():
            reason = dual(
                "a real CAT-controlled Hamlib radio is required",
                "je potřeba skutečné rádio ovládané přes Hamlib CAT",
            )
        elif not snapshot.radio.connected:
            reason = dual("the radio is not connected", "rádio není připojené")
        elif self._payload_active.is_set() or self._active_session_count():
            reason = dual("a session is active", "probíhá relace")
        elif self.workers.is_active("alert-sweep"):
            reason = dual("an alert sweep is active", "probíhá přeladění výstrahy")
        channels = self.scanner_channels() if not reason else []
        if not reason and len(channels) < 2:
            reason = dual(
                "add at least one compatible route frequency",
                "přidejte alespoň jednu kompatibilní frekvenci trasy",
            )
        if reason:
            self._log(
                dual("Scanner not started: ", "Scanner nebyl spuštěn: ") + reason + ".",
                LogLevel.WARNING,
                source="scanner",
            )
            self._update_network_snapshot()
            return False
        self._scanner_generation += 1
        self.scanner = ChannelScanner(
            ChannelPlan(channels),
            dwell=self.config.scan_dwell,
            signal_threshold=self.config.scan_signal_threshold,
        )
        self._scanner_home = channels[0]
        self.scanner.start(time.monotonic())
        active = self.heard.active(time.monotonic())
        self._scanner_last_heard = active[0].last_heard if active else 0.0
        self._scanner_paused = False
        self._log(
            dual(
                f"Channel scanner started on {len(channels)} channels.",
                f"Scanner kanálů spuštěn pro {len(channels)} kanálů.",
            ),
            source="scanner",
        )
        self._update_network_snapshot()
        return True

    def stop_scanner(self, *, restore: bool = True, log: bool = True) -> bool:
        scanner = self.scanner
        if scanner is None:
            return False
        scanner.stop()
        home = self._scanner_home
        self.scanner = None
        self._scanner_home = None
        self._scanner_paused = False
        self._scanner_generation += 1
        if restore and home is not None:
            self._schedule_scanner_tune(home, task_name="scanner-home", restoring=True)
        if log:
            self._log(
                dual("Channel scanner stopped.", "Scanner kanálů zastaven."),
                source="scanner",
            )
        self._update_network_snapshot()
        return True

    def _active_session_count(self) -> int:
        return sum(
            not message.state.terminal for message in self.net.sessions.values()
        )

    def _tick_scanner(self, now: float) -> None:
        scanner = self.scanner
        if scanner is None or not scanner.enabled:
            self._scanner_paused = False
            return
        paused = (
            self._payload_active.is_set()
            or bool(self._active_session_count())
            or self.workers.is_active("alert-sweep")
            or self.workers.is_active("radio-control")
            or self.workers.is_active("scanner-home")
        )
        self._scanner_paused = paused
        if paused or self.workers.is_active("scanner-tune"):
            return
        active = self.heard.active(now)
        newest = active[0].last_heard if active else 0.0
        received = newest > self._scanner_last_heard
        if received:
            self._scanner_last_heard = newest
        signal = self.snapshots.read().radio.signal
        was_holding = scanner.holding
        channel = scanner.tick(now, signal=signal, activity=received)
        if scanner.holding and not was_holding:
            self._log(
                dual(
                    f"Scanner holding on {scanner.current.name} due to activity.",
                    f"Scanner drží na {scanner.current.name} kvůli aktivitě.",
                ),
                source="scanner",
            )
        if channel is not None:
            self._schedule_scanner_tune(channel)

    def _schedule_scanner_tune(
        self,
        channel: Channel,
        *,
        task_name: str = "scanner-tune",
        restoring: bool = False,
    ) -> bool:
        generation = self._scanner_generation

        def operation() -> Channel:
            with self._radio_lock:
                self.radio.set_frequency(channel.freq_hz)
                if channel.mode:
                    self.radio.set_mode(channel.mode)
            return channel

        def completed(result: TaskResult) -> None:
            if result.error:
                self._log(
                    dual(
                        f"Scanner could not tune {channel.name}: {result.error}",
                        f"Scanner nemohl přeladit na {channel.name}: {result.error}",
                    ),
                    LogLevel.ERROR if restoring else LogLevel.WARNING,
                    source="scanner",
                )
            elif restoring or generation == self._scanner_generation:
                action = dual("returned to", "vrácen na") if restoring else dual("tuned to", "naladěn na")
                self._log(
                    dual("Scanner ", "Scanner ")
                    + action
                    + f" {channel.freq_hz / 1_000_000:.4f} MHz {channel.mode}.",
                    source="scanner",
                )
            self.request_radio_poll(force=True)
            self._update_network_snapshot()

        return self.workers.submit(task_name, operation, completed)

    def clear_mailstore(self) -> int:
        """Delete every stored message; refused while a transfer is running.

        A session mid-flight still reads its message by id, so wiping under it
        would fail the transfer in a confusing way. Returns the number of
        messages removed, or -1 for a refusal.
        """
        if self._payload_active.is_set() or self._active_session_count():
            self._log(
                dual(
                    "Mail database not cleared: a transfer is in progress.",
                    "Databáze zpráv nebyla smazána: probíhá přenos.",
                ),
                LogLevel.WARNING,
                source="mail",
            )
            return -1
        removed = self.mailstore.clear()
        self.snapshots.update(mailbox=MailboxSnapshot())
        self._log(
            dual(
                f"Mail database cleared: {removed} messages deleted.",
                f"Databáze zpráv smazána: odstraněno {removed} zpráv.",
            ),
            source="mail",
        )
        return removed

    def payload_active(self) -> bool:
        """True while a negotiated payload modem owns the shared audio."""
        return self._payload_active.is_set()

    def ofdm_status(self):
        """Return live OFDM state only when this hop actually negotiated OFDM.

        An OFDM-configured station keeps VARA as a per-peer fallback.  The OFDM
        backend therefore exists even during a VARA transfer, and its retained
        status must not replace VARA's BUFFER-driven progress in the shell.
        """
        if not self._payload_active.is_set():
            return None
        payload_states = {
            SessionState.STARTING_VARA,
            SessionState.TRANSFERRING,
            SessionState.RECEIVING,
        }
        if not any(
            message.state in payload_states
            and getattr(message, "payload_transport", "vara_p2p") == "ofdm_vhf"
            for message in tuple(self.net.sessions.values())
        ):
            return None
        payload = getattr(self.net, "payload", None)
        backends = getattr(payload, "backends", None)
        if isinstance(backends, dict):
            payload = backends.get("ofdm_vhf")
        if getattr(payload, "name", None) != "ofdm_vhf":
            return None
        return getattr(payload, "status", None)

    # ----- recording received audio ---------------------------------------

    def recording_active(self) -> bool:
        return self._recorder is not None and self._recorder.active

    def recording_seconds(self) -> float:
        return self._recorder.seconds if self._recorder is not None else 0.0

    def recording_level(self) -> float:
        """Loudest sample of the capture so far, in full-scale units."""
        return self._recorder.level() if self._recorder is not None else 0.0

    def recording_path(self) -> Path | None:
        return self._recorder.path if self._recorder is not None else None

    def start_recording(self, *, sample_rate: int | None = None,
                        exclusive: bool = False) -> Path | None:
        """Start writing received audio to a WAV file. Returns the path, or None.

        Two ways in, and the choice is not arbitrary. When the control channel is
        open its receive stream is tapped, so there is only ever one handle on the
        device and the capture is exactly the audio the modem is working from.
        When it is not, a stream of its own is opened -- which is the normal case
        for the first step of an on-air test, where a station is brought up purely
        to record what the other end transmits.
        """
        if self.recording_active():
            return self._recorder.path
        if self.payload_active():
            self._log(
                dual(
                    "Cannot record while a payload transfer owns the audio device.",
                    "Nelze nahrávat, když zvukové zařízení používá přenos zprávy.",
                ),
                LogLevel.WARNING,
                source="audio",
            )
            return None

        # The rate has to be the one the *payload* waveform uses, not the control
        # modem's. A profile above 24 kHz of audio samples at 96 kHz, and a capture
        # of it taken at 48 kHz would be aliased -- silently useless, since the
        # bench would then reject it on the rate check and the session would have
        # to be repeated. When the control channel is being tapped its own rate
        # wins, because that stream is what is being copied.
        suspended_control = False
        if exclusive and self.audio_transport is not None:
            try:
                self._suspend_control()
                suspended_control = True
            except Exception as exc:  # noqa: BLE001 - refusal belongs in the log
                self._log(f"Recording could not take the audio device: {exc}",
                          LogLevel.ERROR, source="audio")
                return None

        waveform = profile_or_default(self.config.ofdm_profile)
        requested_rate = (waveform.sample_rate if sample_rate is None
                          else max(1, int(sample_rate)))
        tap_control = self.audio_transport is not None and not exclusive
        sample_rate = (self.audio_transport.fs if tap_control
                       else requested_rate)
        recorder = WavRecorder(
            capture_path(config_dir() / "captures"),
            sample_rate=sample_rate,
            on_log=lambda value: self._log(value, source="audio"),
        )
        capture = None
        try:
            recorder.start()
            if tap_control:
                self.audio_transport.on_audio = recorder.write
                source = self.audio_transport.actual_input_device_name or "control RX"
            else:
                device = (resolve_device(self.config.audio_input, "input")
                          if self.config.audio_input else None)
                if not isinstance(device, int):
                    raise RuntimeError(
                        dual(
                            "Select an available RX input in Station settings first.",
                            "Nejprve vyberte dostupný vstup RX v nastavení stanice.",
                        )
                    )
                capture = AudioCapture(recorder, device=device,
                                       sample_rate=sample_rate)
                capture.start()
                source = str(self.config.audio_input)
        except Exception as exc:  # noqa: BLE001 - report it, do not crash the UI
            if capture is not None:
                capture.stop()
            recorder.stop()
            try:
                recorder.path.unlink(missing_ok=True)
            except OSError:
                pass
            if suspended_control:
                self._resume_control()
            self._log(f"Recording could not start: {exc}", LogLevel.ERROR,
                      source="audio")
            return None

        self._recorder = recorder
        self._recorder_capture = capture
        self._recording_suspended_control = suspended_control
        self._log(
            dual(
                f"Recording received audio from {source} to {recorder.path.name} "
                f"({sample_rate} Hz mono).",
                f"Nahrávám přijímaný zvuk z {source} do {recorder.path.name} "
                f"({sample_rate} Hz mono).",
            ),
            source="audio",
        )
        return recorder.path

    # ----- transmitting a test burst ---------------------------------------

    def transmit_test_burst(self, *, profile_name: str | None = None,
                            mcs_index: int | None = None,
                            waveform_family: str = "ofdm",
                            bandwidth: str | None = None,
                            fec=None,
                            tx_scale: float | None = None,
                            seed: int = 0xA5,
                            payload_bytes: int = 512, repeats: int = 3,
                            on_log=None):
        """Put a generated OFDM test burst on the air. Returns seconds aired, or None.

        This exists because the alternative was telling an operator to "play the
        WAV into the radio", which means finding a media player, pointing it at
        the right output device, and keying by hand at the right moment. Guardian
        already owns the transmit device and the PTT line, so it does it itself.

        The transmission carries no message: it is the modem's own waveform with
        no channel applied, several bursts with gaps, which is exactly what a
        receiving station needs in order to measure what its radio passed. Nothing
        listens for a reply -- the far end records instead.

        The caller is responsible for having asked the operator first. This keys a
        transmitter.
        """
        from .ofdm import bench
        from .waveforms import bench as experimental_bench
        from .waveforms.config import PROFILES as experimental_profiles

        report = on_log or (lambda value: self._log(value, source="payload"))
        if self.payload_active():
            report(dual(
                "Cannot transmit a test burst while a payload transfer owns the "
                "audio device.",
                "Nelze vyslat testovací dávku, když zvukové zařízení používá "
                "přenos zprávy.",
            ))
            return None

        family = str(waveform_family or "ofdm").strip().lower()
        if family == "ofdm":
            waveform_profile = profile_or_default(profile_name or self.config.ofdm_profile)
            selected_bench = bench
            default_mcs = self.config.ofdm_mcs
        else:
            waveform_profile = (
                experimental_profiles[profile_name]
                if profile_name in experimental_profiles
                else experimental_profile_for(
                    family, bandwidth or self.config.g2_bandwidth
                )
            )
            selected_bench = experimental_bench
            default_mcs = self.config.g2_mcs
        index = default_mcs if mcs_index is None else int(mcs_index)
        samples, _ = selected_bench.make_test_burst(
            waveform_profile, index, payload_bytes=payload_bytes,
            repeats=repeats,
            seed=int(seed) & 0xFFFFFFFF,
            fec=self.config.ofdm_fec if fec is None else fec,
        )
        scale = (self.config.g2_tx_scales.get(family, 1.0)
                 if tx_scale is None else float(tx_scale))
        scale = min(1.0, max(0.05, scale))
        samples = np.asarray(samples, dtype=np.float64) * scale

        output = resolve_device(self.config.audio_output, "output")
        if not isinstance(output, int):
            report(dual(
                "Select an available TX output in Station settings first.",
                "Nejprve vyberte dostupný výstup TX v nastavení stanice.",
            ))
            return None

        # The control modem shares this sound card, and a test burst is a
        # transmission like any other -- so it goes through the same handoff a
        # payload transfer uses rather than keying underneath it.
        acquired = False
        aired = None
        try:
            self._suspend_control()
            acquired = True
            sd = _import_sounddevice()
            sd.check_output_settings(device=output,
                                     samplerate=waveform_profile.sample_rate,
                                     channels=1)
            report(dual(
                f"Transmitting {repeats} test burst(s) on profile "
                f"{waveform_profile.name}, MCS{index}: "
                f"{len(samples) / waveform_profile.sample_rate:.1f} s of audio, "
                f"digital drive {scale * 100:.0f}%.",
                f"Vysílám {repeats} testovacích dávek na profilu "
                f"{waveform_profile.name}, MCS{index}: "
                f"{len(samples) / waveform_profile.sample_rate:.1f} s zvuku, "
                f"digitální úroveň {scale * 100:.0f}%.",
            ))
            aired = transmit_waveform(
                sd, samples,
                device=output,
                sample_rate=waveform_profile.sample_rate,
                ptt=self._payload_ptt,
                lead_seconds=max(self.config.ofdm_tx_lead_ms / 1000.0,
                                 PTT_LEAD_SECONDS),
                tail_seconds=max(self.config.ofdm_tx_tail_ms / 1000.0,
                                 PTT_TAIL_SECONDS),
            )
            report(dual(
                f"Test burst finished: {aired:.1f} s aired.",
                f"Testovací dávka odvysílána: {aired:.1f} s.",
            ))
        except Exception as exc:  # noqa: BLE001 - report it, never crash the UI
            report(dual(
                f"Test burst failed: {exc}",
                f"Testovací dávka selhala: {exc}",
            ))
            aired = None
        finally:
            if acquired:
                self._resume_control()
        return aired

    # ----- paired station calibration ------------------------------------

    def start_station_calibration(
        self, peer: str, mode: str = CalibrationMode.QUICK.value,
    ) -> bool:
        """Offer a bounded, opt-in AutoTune session to one directly heard peer."""
        target = str(peer or "").strip().upper()
        selected_mode = (CalibrationMode.FULL if str(mode).lower() == "full"
                         else CalibrationMode.QUICK)
        if not target or target == self.config.callsign.strip().upper():
            self.station_lab.error = dual(
                "Enter the other station's callsign.",
                "Zadejte volací značku protistanice.",
            )
            return False
        if self.audio_transport is None:
            self.station_lab.error = dual(
                "Start the control channel before calling the other station.",
                "Před voláním protistanice spusťte řídicí kanál.",
            )
            return False
        if self.payload_active() or self.station_lab.state not in {
            CalibrationState.IDLE.value, CalibrationState.COMPLETE.value,
            CalibrationState.CANCELLED.value, CalibrationState.FAILED.value,
        }:
            self.station_lab.error = dual(
                "The station is busy.", "Stanice je zaneprázdněná."
            )
            return False
        session_id = secrets.randbits(32) or 1
        self._calibration_cancel.clear()
        self._calibration_initiator = True
        self._calibration_results.clear()
        self._calibration_commands.clear()
        self._calibration_offer_attempts = 0
        self.station_lab = StationLabStatus(
            state=CalibrationState.OFFERING.value, peer=target,
            session_id=session_id, mode=selected_mode.value,
            message=dual(
                f"Calling {target} for station calibration…",
                f"Volám {target} pro kalibraci stanice…",
            ),
        )
        self._send_calibration_offer()
        return True

    def _send_calibration_offer(self) -> None:
        status = self.station_lab
        attempt = self._calibration_offer_attempts + 1
        self.net.send_calibration_frame(
            FrameType.CAL_OFFER, status.peer, status.session_id,
            f"{'F' if status.mode == CalibrationMode.FULL.value else 'Q'}{attempt}",
        )
        self._calibration_offer_attempts = attempt
        self._calibration_offer_sent_at = time.monotonic()

    def _tick_station_calibration(self, now: float) -> None:
        """Retry the consent handshake and fail it cleanly when unanswered."""
        status = self.station_lab
        if status.state != CalibrationState.OFFERING.value:
            return
        interval = max(CALIBRATION_OFFER_MIN_INTERVAL, self.net.ack_timeout)
        if now - self._calibration_offer_sent_at < interval:
            return
        if self._calibration_offer_attempts < CALIBRATION_OFFER_ATTEMPTS:
            status.message = dual(
                f"Calling {status.peer} again for station calibration…",
                f"Znovu volám {status.peer} pro kalibraci stanice…",
            )
            self._send_calibration_offer()
            return
        self.net.send_calibration_frame(
            FrameType.CAL_CANCEL, status.peer, status.session_id, "TIMEOUT"
        )
        status.state = CalibrationState.FAILED.value
        status.error = dual(
            f"{status.peer} did not answer the calibration request.",
            f"{status.peer} neodpověděl na výzvu ke kalibraci.",
        )

    def accept_station_calibration(self) -> bool:
        """Accept the pending offer locally; never called automatically by UI."""
        status = self.station_lab
        if not status.pending_offer or not status.peer or not status.session_id:
            return False
        status.pending_offer = False
        status.state = CalibrationState.PREPARING.value
        status.message = dual(
            f"Calibration with {status.peer} accepted; waiting for a probe.",
            f"Kalibrace s {status.peer} přijata; čekám na měřicí dávku.",
        )
        self.net.send_calibration_frame(
            FrameType.CAL_ACCEPT, status.peer, status.session_id,
            "F1" if status.mode == CalibrationMode.FULL.value else "Q1",
        )
        return True

    def reject_station_calibration(self) -> bool:
        status = self.station_lab
        if not status.pending_offer:
            return False
        self.net.send_calibration_frame(
            FrameType.CAL_BUSY, status.peer, status.session_id, "REFUSE"
        )
        self.station_lab = StationLabStatus(
            state=CalibrationState.IDLE.value,
            message=dual("Calibration refused.", "Kalibrace odmítnuta."),
        )
        return True

    def cancel_station_calibration(self) -> bool:
        status = self.station_lab
        if status.state in {CalibrationState.IDLE.value,
                            CalibrationState.COMPLETE.value}:
            return False
        self._calibration_cancel.set()
        if status.peer and status.session_id:
            self.net.send_calibration_frame(
                FrameType.CAL_CANCEL, status.peer, status.session_id, "CANCEL"
            )
        status.state = CalibrationState.CANCELLED.value
        status.message = dual("Calibration cancelled.", "Kalibrace zrušena.")
        return True

    def apply_station_calibration(self) -> bool:
        """Apply the final recommendation only after the operator clicks Apply."""
        report = self.station_lab.report
        recommendation = report.recommendation if report is not None else None
        if recommendation is None:
            return False
        self.config.g2_tx_scales[recommendation.waveform] = recommendation.tx_scale
        if recommendation.endpoint_volume is not None:
            gain = WindowsGainController(self.config.audio_output)
            snapshot = gain.snapshot()
            if not snapshot.supported:
                self.station_lab.error = dual(
                    "The saved Windows endpoint is not available; digital drive was applied only.",
                    "Uložený výstup Windows není dostupný; použila se jen digitální úroveň.",
                )
            else:
                gain.set_endpoint_volume(recommendation.endpoint_volume)
        self.config.save()
        self.apply_network_settings()
        report.applied = True
        self.station_lab.message = dual(
            "The calibrated profile was saved and applied.",
            "Kalibrovaný profil byl uložen a použit.",
        )
        return True

    def _on_calibration_frame(self, frame: ControlFrame) -> None:
        """Handle a directed station-lab control frame on the UI/tick thread."""
        if frame.type is FrameType.CAL_OFFER:
            self._calibration_offer(frame)
            return
        status = self.station_lab
        if (frame.message_id != status.session_id
                or frame.source.strip().upper() != status.peer):
            return
        if frame.type is FrameType.CAL_ACCEPT:
            if status.state != CalibrationState.OFFERING.value:
                return
            self._calibration_offer_attempts = 0
            status.state = CalibrationState.PREPARING.value
            status.message = dual(
                f"{status.peer} accepted; preparing the measurement.",
                f"{status.peer} přijal požadavek; připravuji měření.",
            )
            queued = self.workers.submit(
                "station-calibration", self._run_station_calibration,
                self._station_calibration_finished,
            )
            if not queued:
                status.state = CalibrationState.FAILED.value
                status.error = dual(
                    "Calibration worker is already active.",
                    "Kalibrační úloha již běží.",
                )
        elif frame.type is FrameType.CAL_PROBE:
            try:
                command = ProbeCommand.decode(frame.next_hop)
            except ValueError as exc:
                self._log(f"Calibration probe rejected: {exc}", LogLevel.WARNING,
                          source="station-lab")
                return
            if status.state not in {CalibrationState.PREPARING.value,
                                    CalibrationState.MEASURING.value}:
                return
            status.state = CalibrationState.MEASURING.value
            status.current = dual(
                f"Receiving {command.waveform} MCS{command.mcs}",
                f"Přijímám {command.waveform} MCS{command.mcs}",
            )
            self._calibration_last_rx_command = command
            self.workers.submit(
                "station-calibration-rx", lambda: self._receive_calibration_probe(command),
                self._station_calibration_rx_finished,
            )
        elif frame.type is FrameType.CAL_REPORT:
            # Report decoding needs the original probe; sequence is the first
            # encoded byte, so try the bounded outstanding set without trusting
            # malformed wire text.
            for candidate in list(self._calibration_commands.values()):
                try:
                    result = ProbeResult.decode_report(frame.next_hop, candidate)
                except ValueError:
                    continue
                self._calibration_results[result.sequence] = result
                self._calibration_report_ready.set()
                break
        elif frame.type in {FrameType.CAL_BUSY, FrameType.CAL_CANCEL}:
            self._calibration_cancel.set()
            status.state = (CalibrationState.FAILED.value
                            if frame.type is FrameType.CAL_BUSY
                            else CalibrationState.CANCELLED.value)
            status.error = dual(
                f"{status.peer} refused or cancelled calibration.",
                f"{status.peer} kalibraci odmítl nebo zrušil.",
            )
        elif frame.type is FrameType.CAL_DONE:
            if frame.next_hop == "TURN" and not self._calibration_initiator:
                status.state = CalibrationState.PREPARING.value
                status.message = dual(
                    "The first direction is complete; reversing the link.",
                    "První směr je hotový; obracím směr linky.",
                )
                self.workers.submit(
                    "station-calibration", self._run_station_calibration,
                    self._station_calibration_finished,
                )
            else:
                status.state = CalibrationState.COMPLETE.value
                status.message = dual(
                    "Both directions completed calibration.",
                    "Kalibrace v obou směrech byla dokončena.",
                )

    def _calibration_offer(self, frame: ControlFrame) -> None:
        source = frame.source.strip().upper()
        status = self.station_lab
        same_offer = (
            source
            and source == status.peer
            and frame.message_id == status.session_id
        )
        if same_offer:
            # A repeated offer means either the first offer overlapped noise or
            # our CAL_ACCEPT was lost. Preserve a pending operator decision; once
            # accepted, re-ACK it instead of incorrectly telling the caller BUSY.
            if status.pending_offer:
                return
            if (
                not self._calibration_initiator
                and status.state in {
                    CalibrationState.PREPARING.value,
                    CalibrationState.MEASURING.value,
                    CalibrationState.WAITING_REPORT.value,
                }
            ):
                self.net.send_calibration_frame(
                    FrameType.CAL_ACCEPT, status.peer, status.session_id,
                    "F1" if status.mode == CalibrationMode.FULL.value else "Q1",
                )
                return
        if not source or self.payload_active() or self.station_lab.state not in {
            CalibrationState.IDLE.value, CalibrationState.COMPLETE.value,
            CalibrationState.CANCELLED.value, CalibrationState.FAILED.value,
        }:
            self.net.send_calibration_frame(
                FrameType.CAL_BUSY, source, frame.message_id, "BUSY"
            )
            return
        mode = (CalibrationMode.FULL.value if frame.next_hop.startswith("F")
                else CalibrationMode.QUICK.value)
        self.station_lab = StationLabStatus(
            state=CalibrationState.WAITING_APPROVAL.value,
            peer=source, session_id=frame.message_id, mode=mode,
            pending_offer=True,
            message=dual(
                f"{source} requests a {'full' if mode == 'full' else 'quick'} station test.",
                f"{source} žádá {'úplný' if mode == 'full' else 'rychlý'} test stanice.",
            ),
        )
        self._calibration_initiator = False
        allowed = {value.strip().upper() for value in self.config.calibration_allowlist}
        if self.config.calibration_auto_accept and source in allowed:
            self.accept_station_calibration()

    def _run_station_calibration(self) -> CalibrationReport:
        status = self.station_lab
        started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        report = CalibrationReport(
            status.session_id, status.peer, status.mode, "outbound", started,
            radio=self.config.radio, audio_input=self.config.audio_input,
            audio_output=self.config.audio_output,
            frequency_hz=self.current_frequency(),
        )
        try:
            output_index = resolve_device(self.config.audio_output, "output")
            sd = _import_sounddevice()
            device = sd.query_devices(output_index)
            host = sd.query_hostapis(int(device["hostapi"]))
            report.host_api = str(host["name"])
        except Exception:  # noqa: BLE001 - diagnostic metadata is optional
            report.host_api = "unknown"
        if status.mode == CalibrationMode.FULL.value:
            plan = full_plan()
        else:
            from .ofdm.coding import fec_profile
            fec = int(fec_profile(self.config.ofdm_fec))
            base = quick_plan(
                self.config.g2_waveform,
                self.config.ofdm_mcs if self.config.g2_waveform == "ofdm"
                else self.config.g2_mcs,
                fec,
                self.config.g2_bandwidth,
            )
            plan = [ProbeCommand(index, item.waveform, item.mcs, item.fec,
                                 item.tx_scale, item.bandwidth)
                    for index, item in enumerate(
                        candidate for item in base for candidate in (item, item)
                    )]
        status.total = len(plan)
        gain = WindowsGainController(self.config.audio_output)
        snapshot = gain.snapshot()
        journal = GainJournal()
        windows_sweep = bool(self.config.calibration_windows_gain and snapshot.supported)
        if windows_sweep:
            journal.arm(snapshot)
        try:
            deadline = time.monotonic() + min(
                900, max(30, int(self.config.calibration_max_seconds))
            )
            for index, command in enumerate(plan, 1):
                if self._calibration_cancel.is_set() or time.monotonic() >= deadline:
                    break
                # The endpoint ladder is deliberately not a Cartesian product.
                # It samples five levels while tx_scale stays on the planned
                # low-to-high frontier; the raw pair is retained in every row.
                endpoint = None
                if windows_sweep:
                    if status.mode == CalibrationMode.QUICK.value:
                        # First repeated point is an A/B probe. If an 8.5 dB
                        # mixer change moves remote peak by less than 2 dB, the
                        # selected PortAudio path bypasses that mixer and the
                        # rest of the run varies digital drive only.
                        if index == 1:
                            endpoint = 0.35
                        elif index == 2:
                            endpoint = 0.85
                        elif len(report.results) >= 2:
                            low, high = report.results[0], report.results[1]
                            if (low.audio_peak and high.audio_peak
                                    and low.audio_peak > 0 and high.audio_peak > 0
                                    and abs(20.0 * np.log10(
                                        high.audio_peak / low.audio_peak
                                    )) >= 2.0):
                                endpoint = min(0.85, max(0.35,
                                    snapshot.endpoint_volume or 0.65))
                            else:
                                endpoint = None
                    else:
                        ladder = (0.35, 0.50, 0.65, 0.80, 0.95)
                        endpoint = ladder[min(
                            len(ladder) - 1,
                            (index - 1) * len(ladder) // len(plan),
                        )]
                self._calibration_report_ready.clear()
                self._calibration_commands[command.sequence] = command
                status.state = CalibrationState.MEASURING.value
                status.progress = index - 1
                status.current = dual(
                    f"{command.waveform} MCS{command.mcs}, drive {command.tx_scale * 100:.0f}%",
                    f"{command.waveform} MCS{command.mcs}, úroveň {command.tx_scale * 100:.0f}%",
                )
                self.net.send_calibration_frame(
                    FrameType.CAL_PROBE, status.peer, status.session_id,
                    command.encode(),
                )
                control_flushed = True
                if self.audio_transport is not None:
                    control_flushed = self.audio_transport.wait_tx_idle(timeout=8.0)
                if endpoint is not None:
                    gain.set_endpoint_volume(endpoint)
                # Give the peer's worker a short deterministic interval to swap
                # from the AFSK control decoder to its payload recorder.
                if control_flushed:
                    time.sleep(CALIBRATION_PROBE_GUARD_SECONDS)
                point_started = time.monotonic()
                aired = (
                    self.transmit_test_burst(
                        waveform_family=command.waveform,
                        bandwidth=command.bandwidth,
                        mcs_index=command.mcs, fec=command.fec,
                        tx_scale=command.tx_scale, payload_bytes=512, repeats=1,
                        seed=status.session_id ^ command.sequence,
                    )
                    if control_flushed else None
                )
                if windows_sweep:
                    gain.restore(snapshot)
                status.state = CalibrationState.WAITING_REPORT.value
                got = (
                    self._calibration_report_ready.wait(
                        timeout=CALIBRATION_REPORT_TIMEOUT
                    )
                    if control_flushed else False
                )
                result = self._calibration_results.pop(command.sequence, None) if got else None
                if result is None:
                    result = ProbeResult(
                        command.sequence, command.tx_scale, command.waveform,
                        command.mcs, command.fec, False,
                        wall_seconds=max(0.001, time.monotonic() - point_started),
                        error=("no calibration report" if control_flushed
                               else "control probe did not leave the radio"),
                        bandwidth=command.bandwidth,
                    )
                else:
                    result.payload_bytes = 512
                    result.wall_seconds = max(
                        0.001, time.monotonic() - point_started
                    )
                    result.keyed_seconds = float(aired or 0.0)
                result.endpoint_volume = endpoint
                report.results.append(result)
                status.progress = index
                # Two consecutive unsafe/undecodable points while ascending are
                # the clipping/channel cliff. Quick Tune stops; Full moves to the
                # next family instead of keying hopeless higher orders.
                if status.mode == CalibrationMode.QUICK.value and len(report.results) >= 2:
                    if all(not item.safe or not item.frame_ok
                           for item in report.results[-2:]):
                        stop_reason = "signal/clipping cliff"
                        break
            else:
                stop_reason = "complete"
            reason = ("cancelled" if self._calibration_cancel.is_set()
                      else "time limit" if time.monotonic() >= deadline
                      else stop_reason)
            # A deliberately pruned or time-bounded plan is still finished.
            # Do not leave the UI showing e.g. 2/12 after CAL_DONE succeeded.
            status.total = status.progress
            report.finish(reason)
            paths = report.save()
            status.report_json, status.report_csv = map(str, paths)
            self.net.send_calibration_frame(
                FrameType.CAL_DONE, status.peer, status.session_id,
                "TURN" if self._calibration_initiator else "DONE",
            )
            return report
        finally:
            if windows_sweep:
                try:
                    gain.restore(snapshot)
                finally:
                    journal.clear()

    def _receive_calibration_probe(self, command: ProbeCommand) -> ProbeResult:
        from .ofdm.link import OfdmBurstCodec
        from .payload.ofdm_vhf import RadioAudioPipe
        from .waveforms.framing import ExperimentalBurstCodec

        if command.waveform == "ofdm":
            profile = profile_or_default(self.config.ofdm_profile)
            codec = OfdmBurstCodec()
        else:
            profile = experimental_profile_for(
                command.waveform, command.bandwidth
            )
            codec = ExperimentalBurstCodec()
        pipe = RadioAudioPipe(
            profile,
            input_device=resolve_device(self.config.audio_input, "input"),
            output_device=resolve_device(self.config.audio_output, "output"),
            ptt=self._payload_ptt, codec=codec,
            tx_lead_ms=self.config.ofdm_tx_lead_ms,
            tx_tail_ms=self.config.ofdm_tx_tail_ms,
        )
        acquired = False
        started = time.monotonic()
        try:
            self._suspend_control()
            acquired = True
            # CAL_PROBE can be decoded while the AFSK tail is still present.
            # Opening the payload detector on that tail makes it return before
            # the actual measuring burst. Let the control waveform clear first.
            time.sleep(CALIBRATION_RX_SETTLE_SECONDS)
            pipe.start()
            samples = pipe.receive(timeout=12.0)
            if samples is None:
                return ProbeResult(
                    command.sequence, command.tx_scale, command.waveform,
                    command.mcs, command.fec, False,
                    wall_seconds=time.monotonic() - started,
                    error="no measuring burst heard",
                    bandwidth=command.bandwidth,
                )
            decoded = codec.decode_burst(profile, samples)
            from .ofdm.bench import write_wav
            capture_root = config_dir() / "station-lab" / "captures"
            capture_root.mkdir(parents=True, exist_ok=True)
            raw_path = write_wav(
                capture_root / (
                    f"cal-{self.station_lab.session_id:08x}-{command.sequence:03d}-"
                    f"{command.waveform}-{command.bandwidth}-mcs{command.mcs}.wav"
                ),
                samples,
                profile.sample_rate,
            )
            metrics = decoded.metrics
            peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
            rms = float(np.sqrt(np.mean(np.asarray(samples) ** 2))) if len(samples) else 0.0
            clipped = int(np.count_nonzero(np.abs(samples) >= 0.999))
            header = decoded.header
            return ProbeResult(
                command.sequence, command.tx_scale, command.waveform,
                command.mcs, command.fec, bool(decoded.ok),
                header_ok=header is not None,
                snr_db=metrics.residual_snr_db or metrics.snr_db,
                evm_rms=metrics.evm_rms,
                audio_peak=metrics.audio_rms * 10 ** ((metrics.crest_factor_db or 0) / 20)
                if metrics.audio_rms is not None else peak,
                audio_rms=metrics.audio_rms if metrics.audio_rms is not None else rms,
                clipped_samples=clipped, flat_top=clipped >= 3,
                sync_confidence=metrics.sync_confidence,
                cfo_hz=metrics.cfo_hz,
                payload_bytes=(header.payload_len if header is not None else 0),
                wall_seconds=time.monotonic() - started,
                error=metrics.error or "",
                bandwidth=command.bandwidth,
                capture_path=str(raw_path),
            )
        finally:
            pipe.stop()
            if acquired:
                self._resume_control()

    def _station_calibration_rx_finished(self, task: TaskResult) -> None:
        status = self.station_lab
        if task.error is not None:
            command = self._calibration_last_rx_command or ProbeCommand(
                0, self.config.g2_waveform, 0, 0, 0.05
            )
            result = ProbeResult(
                command.sequence, command.tx_scale, command.waveform,
                command.mcs, command.fec, False,
                error=str(task.error),
                bandwidth=command.bandwidth,
            )
        else:
            result = task.value
        if not isinstance(result, ProbeResult):
            return
        self.net.send_calibration_frame(
            FrameType.CAL_REPORT, status.peer, status.session_id,
            result.encode_report(),
        )
        status.progress += 1
        status.state = CalibrationState.PREPARING.value

    def _station_calibration_finished(self, task: TaskResult) -> None:
        status = self.station_lab
        if task.error is not None:
            status.state = CalibrationState.FAILED.value
            status.error = str(task.error)
            status.message = dual(
                "Station calibration failed.", "Kalibrace stanice selhala."
            )
            return
        status.report = task.value if isinstance(task.value, CalibrationReport) else None
        if self._calibration_cancel.is_set():
            status.state = CalibrationState.CANCELLED.value
        elif self._calibration_initiator:
            status.state = CalibrationState.PREPARING.value
            status.message = dual(
                "This direction is complete; the peer is measuring the reverse direction.",
                "Tento směr je hotový; protistanice měří opačný směr.",
            )
            return
        else:
            status.state = CalibrationState.COMPLETE.value
        status.message = dual(
            "Measurement complete. Review the report before applying it.",
            "Měření dokončeno. Před použitím zkontrolujte report.",
        )

    def _close_recording_losing_its_source(self, reason: str) -> None:
        """End a tapped recording whose audio source is about to disappear.

        A capture that taps the control stream stops receiving the moment that
        stream closes, but nothing about it would look wrong: the file stays open,
        the UI still says "recording", and the elapsed time simply stops advancing.
        Closing it here gives the operator a complete, valid file and a line saying
        why it ended. A capture on a stream of its own is unaffected, so it is
        left alone.
        """
        if self._recorder is None or self._recorder_capture is not None:
            return
        self._log(
            dual(f"Recording stopped: {reason}.",
                 f"Nahrávání ukončeno: {reason}."),
            source="audio",
        )
        try:
            self.stop_recording()
        except Exception as exc:  # noqa: BLE001 - never block the codec handoff
            self._log(f"Recording could not be closed cleanly: {exc}",
                      LogLevel.ERROR, source="audio")

    def stop_recording(self):
        """Close the capture and return its `RecordingSummary`, or None."""
        recorder, self._recorder = self._recorder, None
        capture, self._recorder_capture = self._recorder_capture, None
        suspended_control, self._recording_suspended_control = (
            self._recording_suspended_control, False
        )
        if recorder is None:
            if suspended_control:
                self._resume_control()
            return None
        if self.audio_transport is not None and self.audio_transport.on_audio is not None:
            self.audio_transport.on_audio = None
        try:
            if capture is not None:
                capture.stop()
            summary = recorder.stop()
        finally:
            if suspended_control:
                self._resume_control()
        self._log(
            dual(
                f"Recording stopped: {summary.verdict()}",
                f"Nahrávání ukončeno: {summary.verdict()}",
            ),
            LogLevel.WARNING if not summary.usable else LogLevel.INFO,
            source="audio",
        )
        return summary

    def is_no_cat_radio(self) -> bool:
        return (
            self.config.radio_backend == "hamlib"
            and int(self.config.rig_model or 0) == DUMMY_MODEL
        )

    def set_manual_frequency(self, frequency_hz: int) -> None:
        """Record the physical dial position for a radio without CAT."""
        value = max(0, int(frequency_hz or 0))
        self.config.manual_frequency_hz = value
        self.config.save()
        if getattr(self.radio, "no_cat", False):
            self.radio.manual_frequency_hz = value
        self.request_radio_poll(force=True)

    def alert_sweep_channels(self) -> list[tuple[int, str]]:
        """Other frequencies from the route table an alert should also reach."""
        # A dummy backend can key PTT but cannot move the physical dial. A
        # background sweep through simulated Hamlib frequencies would silently
        # transmit every copy on the original channel.
        if self.is_no_cat_radio():
            return []
        here = self.current_frequency()
        fallback_mode = "USB" if self.config.active_modem() == "mfsk16" else "FM"
        channels = [
            (freq, mode or fallback_mode)
            for freq, mode in self.routes.frequencies()
            if freq != here
            and control_mode_compatible(
                self.config.active_modem(), mode or fallback_mode
            )
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

        `CHAT OFF` bounds VARA's idle loops. TEXT compression remains the
        baseline unless the operator selects VARA FILES. Guardian's BZIP2 layer
        is the mutually exclusive transport-independent alternative. Bandwidth
        and `P2P SESSION` are HF/SAT only -- the
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
        self.vara.send_command(
            "COMPRESSION FILES"
            if self.config.vara_file_compression
            else "COMPRESSION TEXT"
        )
        self.vara.send_command("CHAT OFF")
        if self.config.vara_mode.upper() == "HF":
            self.vara.send_command(self.config.vara_hf_bandwidth)
            self.vara.send_command("P2P SESSION")
        return True

    def radio_settings(self) -> tuple:
        """Everything the radio driver and rigctld are built from. A change
        here needs `reconfigure_radio()` — the driver is constructed once."""
        c = self.config
        return (
            c.radio_backend,
            c.rig_model,
            c.cat_port,
            c.cat_baud,
            c.rigctld_host,
            c.rigctld_port,
            c.rigctld_path,
            c.ptt_line,
            c.ptt_type,
        )

    def reconfigure_radio(self) -> None:
        """Rebuild the driver after the operator changes radio settings.

        Before this existed a backend or PTT change only took effect after an
        application restart: `make_driver` ran once in `__init__` and the old
        driver (with the old port, host, and `reports_ptt`) lived on. The
        rigctld child is kept — `ensure()` already restarts it when its
        command line no longer matches.
        """
        self.stop_scanner(restore=False)
        with self._radio_lock:
            try:
                self.radio.close()
            except Exception:  # noqa: BLE001 - a dead driver must not block the new one
                pass
            self.radio = make_driver(self.config)
        self.rigctld.exe = find_executable("rigctld", self.config.rigctld_path)
        self._log(
            dual(
                f"Radio control reconfigured ({self.radio.name}).",
                f"Řízení rádia překonfigurováno ({self.radio.name}).",
            ),
            source="radio",
        )
        self.request_radio_poll(force=True)

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
        return (
            self.config.vara_hf_bandwidth,
            self.config.vara_file_compression,
            self.config.vara_encryption,
            self.config.vara_encryption_password,
        )

    def _configure_vara_encryption(self) -> bool:
        """Persist Guardian's AES choice into the selected local VARA INI.

        VARA does not expose encryption on its TCP command channel.  A remote
        modem therefore remains the remote operator's responsibility; a local
        modem is edited narrowly before Guardian starts/connects to it.
        """
        executable = self._selected_vara_executable()
        if executable is None:
            if self.config.vara_encryption:
                self._log(
                    dual(
                        "VARA encryption is enabled in Guardian, but a remote "
                        "VARA instance must be configured in its own Encryption dialog.",
                        "Šifrování VARA je v Guardianu zapnuté, ale vzdálenou "
                        "instanci VARA je nutné nastavit v jejím dialogu Encryption.",
                    ),
                    LogLevel.WARNING,
                    source="vara",
                )
            return False
        encryption = read_encryption_status(executable)
        if (
            encryption.enabled != bool(self.config.vara_encryption)
            or self.config.vara_encryption_password.strip()
        ):
            configure_encryption(
                executable,
                enabled=self.config.vara_encryption,
                password=self.config.vara_encryption_password,
            )
        return True

    def apply_vara_encryption_setting(self) -> bool:
        """Apply a Settings-dialog AES edit and report restart requirements."""
        try:
            local = self._configure_vara_encryption()
        except (OSError, ValueError) as exc:
            self._log(
                dual(
                    f"VARA encryption setting failed: {exc}",
                    f"Nastavení šifrování VARA selhalo: {exc}",
                ),
                LogLevel.ERROR,
                source="vara",
            )
            return False
        if local and self.vara.connected:
            self._log(
                dual(
                    "VARA encryption was saved. Restart VARA before the next "
                    "encrypted connection so the modem reloads its INI.",
                    "Šifrování VARA bylo uloženo. Před dalším šifrovaným spojením "
                    "restartujte VARA, aby modem znovu načetl svůj INI soubor.",
                ),
                LogLevel.WARNING,
                source="vara",
            )
        return local

    def _make_payload_backend(self):
        """Build the payload transport for the sessions that come next.

        A VARA station gets exactly what it always got. A station that selected an
        experimental transport gets it wrapped in `NegotiatedPayload`, because the
        peer may not have it: the handshake settles that per hop, and each transfer
        then goes to whichever backend that hop agreed on.
        """
        deps = self._payload_dependencies()
        primary = make_backend(self.config.payload_backend, **deps)
        if self.config.payload_backend == "vara_p2p":
            return primary
        return NegotiatedPayload(
            default=make_backend("vara_p2p", **deps),
            backends={primary.name: primary},
            on_log=lambda value: self._log(value, source="payload"),
        )

    def _payload_dependencies(self) -> dict:
        """Everything any transport might need. Each one takes what it uses."""
        return dict(
            vara=self.vara,
            on_log=lambda value: self._log(value, source="payload"),
            on_qsy=self._payload_send_qsy,
            on_receive_qsy=(
                self._payload_receive_qsy
                if self.config.separate_working_channels
                else None
            ),
            # The legacy single-channel path stays tuned until its control
            # confirmation. Opt-in working sessions restore both peers before
            # control audio resumes and RECEIVED/DELIVERED is transmitted.
            on_unqsy=(
                self._payload_restore_calling
                if self.config.separate_working_channels
                else None
            ),
            on_acquire=self._suspend_control,
            on_release=self._resume_control,
            # What a soundcard transport needs and VARA does not: the codec
            # endpoints, a way to key the radio, and its waveform settings. The
            # VARA path ignores them, so make_backend("vara_p2p") behaves exactly
            # as it did before this existed.
            audio_input=self.config.audio_input,
            audio_output=self.config.audio_output,
            ptt=self._payload_ptt,
            ptt_turnaround_ms=self._payload_ptt_delay_ms,
            ofdm_profile=self.config.ofdm_profile,
            ofdm_mcs=self.config.ofdm_mcs,
            ofdm_tx_lead_ms=self.config.ofdm_tx_lead_ms,
            ofdm_tx_tail_ms=self.config.ofdm_tx_tail_ms,
            ofdm_max_retries=self.config.ofdm_max_retries,
            ofdm_adaptive_fec=self.config.ofdm_adaptive_fec,
            ofdm_modern_ldpc=self.config.ofdm_modern_ldpc,
            ofdm_fec=self.config.ofdm_fec,
            ofdm_adaptive_burst=self.config.ofdm_adaptive_burst,
            ofdm_burst_bytes=self.config.ofdm_burst_bytes,
            ofdm_min_burst_bytes=self.config.ofdm_min_burst_bytes,
            ofdm_max_burst_bytes=self.config.ofdm_max_burst_bytes,
            ofdm_arq_block_bytes=self.config.ofdm_arq_block_bytes,
            ofdm_timeout_multiplier=self.config.ofdm_timeout_multiplier,
            ofdm_legacy_mode=self.config.ofdm_legacy_mode,
            ofdm_train_bursts=self.config.ofdm_train_bursts,
            ofdm_adaptive_train=self.config.ofdm_adaptive_train,
            ofdm_superframe=self.config.ofdm_superframe,
            ofdm_train_gap_ms=self.config.ofdm_train_gap_ms,
            ofdm_max_train_seconds=self.config.ofdm_max_train_seconds,
            g2_waveform=self.config.g2_waveform,
            g2_bandwidth=self.config.g2_bandwidth,
            g2_mcs=self.config.g2_mcs,
            g2_adaptive_mcs=self.config.g2_adaptive_mcs,
            g2_tx_scale=self.config.g2_tx_scales.get(
                self.config.g2_waveform, 1.0
            ),
        )

    def _open_radio(self) -> list[str]:
        """Bring the configured radio up, starting rigctld when Hamlib needs it.

        Returns whatever the launcher had to say. Callers hold no lock; this
        takes the radio lock itself and is safe to call on a worker.
        """
        messages: list[str] = []
        config = self.config
        with self._radio_lock:
            if config.radio_backend == "hamlib":
                messages.append(
                    self.rigctld.ensure(
                        config.rig_model,
                        config.cat_port,
                        config.rigctld_port,
                        config.cat_baud,
                        ptt_type=config.ptt_type,
                    )
                )
            self.radio.open()
        return messages

    def connect_radio(self) -> bool:
        radio = self.radio

        def operation() -> list[str]:
            return self._open_radio()

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

    def run_ptt_test(
        self,
        seconds: float = PTT_TEST_SECONDS,
        on_result: Callable[[bool, str], None] | None = None,
    ) -> bool:
        """Key the transmitter briefly so the operator can prove PTT works.

        This is the one deliberate carrier Guardian ever puts on air, so it is
        short, it is announced in the log, and the unkey is in a `finally`: an
        interface that keys but never releases is exactly the fault this test
        exists to catch, and it must not be left keyed by our own error.

        `on_result(ok, message)` is delivered on the UI thread by the worker
        pool's completion drain.
        """
        if self.config.radio_backend == "none":
            message = dual(
                "No radio control is configured, so there is no PTT to test.",
                "Řízení rádia není nastaveno, není tedy co testovat.",
            )
            self._log(message, LogLevel.WARNING, source="radio")
            if on_result is not None:
                on_result(False, message)
            return False
        if self.scanner is not None:
            message = dual(
                "Stop the channel scanner before testing PTT.",
                "Před testem PTT zastavte scanner kanálů.",
            )
            self._log(message, LogLevel.WARNING, source="radio")
            if on_result is not None:
                on_result(False, message)
            return False
        if self._payload_active.is_set():
            message = dual(
                "A payload transfer holds the radio; try again when it ends.",
                "Rádio je obsazeno datovým přenosem; zkuste to po jeho skončení.",
            )
            self._log(message, LogLevel.WARNING, source="radio")
            if on_result is not None:
                on_result(False, message)
            return False

        hold = max(0.2, min(float(seconds), PTT_TEST_MAX_SECONDS))

        def operation() -> str:
            transport = self.audio_transport
            if transport is not None and not transport.wait_tx_idle(timeout=10.0):
                raise TimeoutError(
                    dual(
                        "a control burst is still on the air",
                        "řídicí rámec je stále ve vysílání",
                    )
                )
            opened: list[str] = []
            if not self.radio.is_open:
                opened = self._open_radio()
            # A VOX/serial backend can only read back the control line it just
            # asserted, so its "PTT on" says nothing about the transmitter.
            # Trusting it would turn a dead interface into a confident pass.
            confirms = bool(getattr(self.radio, "reports_ptt", False))
            with self._radio_lock:
                keyed_reported = False
                self.radio.set_ptt(True)
                try:
                    # Ask the rig what it thinks it is doing while it is keyed:
                    # a driver that accepts T 1 and transmits nothing is the
                    # interesting failure, and only the readback shows it.
                    time.sleep(hold / 2)
                    keyed_reported = confirms and bool(self.radio.get_state().ptt)
                    time.sleep(hold - hold / 2)
                finally:
                    self.radio.set_ptt(False)
                # Reading our own line back is still worth doing here: a line
                # left asserted is a fault whoever is reporting it.
                released = not self.radio.get_state().ptt
            detail = " ".join(message for message in opened if message)
            return self._ptt_test_report(hold, keyed_reported, released, detail)

        def completed(result: TaskResult) -> None:
            if result.error:
                message = dual(
                    f"PTT test failed: {result.error}",
                    f"Test PTT selhal: {result.error}",
                )
                self._log(message, LogLevel.ERROR, source="radio")
            else:
                message = result.value
                self._log(message, source="radio")
            self.request_radio_poll(force=True)
            if on_result is not None:
                on_result(result.error is None, message)

        submitted = self.workers.submit("radio-control", operation, completed)
        if not submitted:
            message = dual(
                "The radio is busy with another command.",
                "Rádio právě zpracovává jiný příkaz.",
            )
            self._log(message, LogLevel.WARNING, source="radio")
            if on_result is not None:
                on_result(False, message)
            return False
        # Say exactly which wiring is being exercised — when the test fails,
        # this line plus the rigctld command line are the whole diagnosis.
        if self.config.radio_backend == "hamlib":
            path = (
                f"rigctld {self.config.rigctld_host}:{self.config.rigctld_port}"
                f", PTT {self.config.ptt_type or 'RIG'}"
                + (
                    f" on {self.config.cat_port}"
                    if (self.config.ptt_type or "RIG").upper() != "RIG"
                    and self.config.cat_port
                    else ""
                )
            )
        else:
            path = f"{self.config.ptt_line} on {self.config.cat_port or '?'}"
        self._log(
            dual(
                f"PTT test: keying {self.radio.name} for {hold:.1f} s ({path}).",
                f"Test PTT: klíčuji {self.radio.name} na {hold:.1f} s ({path}).",
            ),
            source="radio",
        )
        return True

    def _ptt_test_report(
        self,
        hold: float,
        keyed_reported: bool,
        released: bool,
        detail: str = "",
    ) -> str:
        """Say what the rig actually did, not merely that nothing raised."""
        if not released:
            # Worth shouting about: PTT is still asserted after we asked for it
            # to drop, and the operator should pull the interface.
            return dual(
                f"PTT test: PTT is still asserted after unkeying — "
                f"check the interface now. {detail}".strip(),
                f"Test PTT: PTT je i po odklíčování stále aktivní — "
                f"ihned zkontrolujte rozhraní. {detail}".strip(),
            )
        if keyed_reported:
            return dual(
                f"PTT test passed: keyed for {hold:.1f} s and the radio "
                f"reported TX. {detail}".strip(),
                f"Test PTT prošel: klíčováno {hold:.1f} s a rádio hlásilo "
                f"vysílání. {detail}".strip(),
            )
        return dual(
            f"PTT test: the command was accepted and released after "
            f"{hold:.1f} s, but this backend cannot confirm TX — watch the "
            f"radio itself. {detail}".strip(),
            f"Test PTT: příkaz byl přijat a po {hold:.1f} s uvolněn, ale toto "
            f"rozhraní neumí vysílání potvrdit — sledujte samotné rádio. "
            f"{detail}".strip(),
        )

    def disconnect_radio(self) -> bool:
        self.stop_scanner(restore=False)

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
            executable = self._selected_vara_executable()
            self._configure_vara_encryption()
            try:
                self.vara.connect(timeout=0.5)
            except OSError as first_error:
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
        self.stop_scanner(restore=True)
        self._close_recording_losing_its_source("the control channel was stopped")
        # Detach control first so a payload worker unwinding after cancellation
        # cannot reopen it through _resume_control().
        transport, self.audio_transport = self.audio_transport, None
        if transport is not None:
            transport.stop()
        # Stopping control is a station-wide stop, not merely a UI reset. Abort
        # every live payload before replacing its orchestrator; otherwise the
        # old daemon worker can continue keying a radio no longer shown as busy.
        old_net = self.net
        for message in tuple(old_net.sessions.values()):
            if not message.state.terminal:
                old_net.cancel(message.msg_id, notify=False)
        self.config.control_channel = "off"
        self.config.save()
        self.net = self._build_net(NullTransport())
        self._log(dual(
            "Audio control channel stopped.",
            "Zvukový řídicí kanál byl zastaven.",
        ), source="control")
        self._update_network_snapshot()

    def _announce_prepared_mail(
        self,
        mail,
        bundle: bytes,
        *,
        flags: Flags = Flags.NONE,
    ) -> None:
        self.net.send_message(
            final_dest=mail.final_dest,
            body=mail.subject,
            msg_id=mail.msg_id,
            priority=Priority(mail.priority),
            ttl=self.config.default_ttl,
            flags=flags,
            payload_bytes=bundle,
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
        if self.scanner is not None:
            self._log(
                dual(
                    "Message remains queued: stop the channel scanner before sending.",
                    "Zpráva zůstává ve frontě: před odesláním zastavte scanner kanálů.",
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
        if (
            direct_route
            and route.freq_hz
            and self.config.auto_qsy
            and not self.config.separate_working_channels
        ):
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
        baseline = mail.to_bundle()
        if self.config.guardian_compression and not self.config.vara_file_compression:
            task_name = f"mail-compress-{mail.msg_id}"

            def compressed(result: TaskResult) -> None:
                if result.error:
                    self._log(
                        dual(
                            f"Guardian compression failed for message #{mail.msg_id}; "
                            "sending the standard ZIP bundle.",
                            f"Komprese Guardian pro zprávu #{mail.msg_id} selhala; "
                            "odesílám standardní ZIP balíček.",
                        ),
                        LogLevel.WARNING,
                        source="payload",
                    )
                    self._announce_prepared_mail(mail, baseline)
                    return
                encoding = result.value
                flags = (
                    Flags.COMPRESSED
                    if encoding.method == "bzip2"
                    else Flags.NONE
                )
                self._log(
                    dual(
                        f"Guardian BZIP2 compression produced {encoding.method} for "
                        f"message #{mail.msg_id}: {encoding.baseline_size} → "
                        f"{len(encoding.data)} B ({encoding.saved_percent:.1f}% saved).",
                        f"Komprese Guardian BZIP2 vytvořila {encoding.method} pro zprávu "
                        f"#{mail.msg_id}: {encoding.baseline_size} → "
                        f"{len(encoding.data)} B (úspora {encoding.saved_percent:.1f} %).",
                    ),
                    source="payload",
                )
                self._announce_prepared_mail(
                    mail,
                    encoding.data,
                    flags=flags,
                )

            queued = self.workers.submit(
                task_name,
                lambda: mail.to_guardian_bundle(baseline),
                compressed,
            )
            if not queued:
                self.mailstore.set_status(message_id, status=Status.QUEUED)
                return False
            self._log(
                dual(
                    f"Compressing message #{mail.msg_id} with Guardian BZIP2…",
                    f"Komprimuji zprávu #{mail.msg_id} pomocí Guardian BZIP2…",
                ),
                source="payload",
            )
            return True
        elif self.config.guardian_compression:
            self._log(
                dual(
                    "Guardian compression was not stacked with VARA FILES compression.",
                    "Komprese Guardian nebyla vrstvena přes kompresi VARA FILES.",
                ),
                LogLevel.WARNING,
                source="payload",
            )
        self._announce_prepared_mail(mail, baseline)
        return True

    def tick(self) -> None:
        now = time.monotonic()
        if self.audio_transport is not None:
            self.audio_transport.pump()
        self.net.tick(now)
        self._tick_station_calibration(now)
        self._tick_beacon(now)
        self._tick_auto_deliver(now)
        self._tick_scanner(now)
        self.request_radio_poll(now=now)
        self._update_vara_snapshot()
        self._update_network_snapshot(now)

    def _net_idle(self) -> bool:
        """True when it is safe for Guardian to start transmitting by itself.

        Both automatic behaviours below key the radio without an operator
        asking, so they only run with a live control channel, nothing already
        in flight, and no payload transfer holding the codec.
        """
        if (
            self.audio_transport is None
            or self._payload_active.is_set()
            or self.scanner is not None
            or self.station_lab.state in {
                CalibrationState.OFFERING.value,
                CalibrationState.WAITING_APPROVAL.value,
                CalibrationState.PREPARING.value,
                CalibrationState.MEASURING.value,
                CalibrationState.WAITING_REPORT.value,
            }
        ):
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
                last_attempt = self._auto_delivery_attempted.get(msg_id)
                if (
                    last_attempt is not None
                    and now - last_attempt < self._AUTO_DELIVER_RETRY
                ):
                    continue
                destination = meta.get("final_dest") or ""
                resolved, _how = self.net._resolve_next_hop(destination)
                hop = resolved or meta.get("next_hop") or destination
                if hop.upper() not in heard:
                    continue
                if hop != meta.get("next_hop"):
                    self.mailstore.set_status(msg_id, next_hop=hop)
                self._auto_delivery_attempted[msg_id] = now
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
                transfer_direction=state.transfer_direction,
                rx_transfer_bytes=state.rx_transfer_bytes,
                rx_transfer_total=state.rx_transfer_total,
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
        scanner = self.scanner
        channel = scanner.current if scanner is not None else None
        self.snapshots.update(
            network=NetworkSnapshot(
                active_sessions=self._active_session_count(),
                heard_stations=len(self.heard.active(now)),
                control_channel_active=self.audio_transport is not None,
                scanner_active=bool(scanner and scanner.enabled),
                scanner_holding=bool(scanner and scanner.holding),
                scanner_paused=bool(scanner and self._scanner_paused),
                scanner_channel=channel.name if channel else "",
                scanner_frequency_hz=channel.freq_hz if channel else None,
                scanner_channels=len(scanner.plan) if scanner else 0,
            )
        )

    def _session_event(self, message, event: str) -> None:
        self._log(
            f"[{message.source}#{message.msg_id}] {event}",
            source="session",
        )
        # Adopt the session's negotiated slow-keying gap for its payload phase.
        # STARTING_VARA/RECEIVING are emitted before the selected backend takes
        # the codec, so the value is in place before its first PTT ON.
        delay = int(getattr(message, "ptt_delay_ms", 0) or 0)
        payload_transport = getattr(message, "payload_transport", "vara_p2p")
        if message.state in (
            SessionState.STARTING_VARA,
            SessionState.TRANSFERRING,
            SessionState.RECEIVING,
        ):
            if delay != self._payload_ptt_delay_ms:
                self._payload_ptt_delay_ms = delay
                if delay and payload_transport == "ofdm_vhf":
                    self._log(
                        dual(
                            f"Slow keying negotiated: PTT held {delay} ms "
                            "after each OFDM VHF burst.",
                            f"Vyjednáno pomalé klíčování: PTT drženo {delay} ms "
                            "po každém vysílání OFDM VHF.",
                        ),
                        source="session",
                    )
                elif delay and self.config.vara_host_ptt:
                    self._log(
                        dual(
                            f"Slow keying negotiated: PTT held {delay} ms "
                            "after each VARA burst.",
                            f"Vyjednáno pomalé klíčování: PTT drženo {delay} ms "
                            "po každém vysílání VARA.",
                        ),
                        source="session",
                    )
                elif delay:
                    # Agreed on the air but unusable here: Guardian only slows
                    # keying it performs itself. Silence would leave the peer
                    # believing both ends were holding their tail.
                    self._log(
                        dual(
                            f"Slow keying of {delay} ms was negotiated but "
                            "cannot be applied: Guardian is not keying the "
                            "radio for VARA.",
                            f"Bylo vyjednáno pomalé klíčování {delay} ms, ale "
                            "nelze je uplatnit: Guardian pro VARA neklíčuje.",
                        ),
                        LogLevel.WARNING,
                        source="session",
                    )
        elif message.state.terminal:
            self._payload_ptt_delay_ms = 0
        stored = self.mailstore.get(message.msg_id)
        if message.direction == "out" and stored:
            next_hop = getattr(message, "next_hop", "")
            if next_hop and stored.next_hop != next_hop:
                self.mailstore.set_status(
                    message.msg_id,
                    next_hop=next_hop,
                )
                stored.next_hop = next_hop
            if message.state is SessionState.DELIVERED:
                self.mailstore.set_status(
                    message.msg_id,
                    status=Status.DELIVERED,
                    folder=Folder.SENT,
                )
            elif message.state in (SessionState.CONFIRMED, SessionState.FORWARDED):
                self.mailstore.set_status(
                    message.msg_id,
                    status=Status.FORWARDED,
                    folder=Folder.SENT,
                )
            elif message.state in (SessionState.FAILED, SessionState.CANCELLED):
                if stored.folder == Folder.TRANSIT:
                    self.mailstore.set_status(
                        message.msg_id,
                        status=Status.WAITING_PICKUP,
                        folder=Folder.TRANSIT,
                    )
                else:
                    self.mailstore.set_status(
                        message.msg_id,
                        status=Status.FAILED,
                    )
            if (
                message.state
                in (
                    SessionState.CONFIRMED,
                    SessionState.FORWARDED,
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
                stored_inbound = self.mailstore.store_incoming(
                    message.payload_bytes,
                    self.config.callsign,
                    via=message.source,
                )
                # _maybe_relay() runs after this event callback returns. Feed
                # it the reserialised bundle so every hop becomes part of the
                # route history carried to the final destination.
                message.payload_bytes = stored_inbound.to_bundle()
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
            self._vara_ptt if self.config.vara_host_ptt else None
        )

    def _vara_ptt(self, enabled: bool) -> None:
        """Key the radio for VARA, honouring the negotiated slow-keying tail.

        The gap applies to the *release*: watched on a spectrum display, a
        cheap handheld unkeyed the moment VARA said PTT OFF cut the tail off
        its own burst, and the peer answered into what was still missing.
        Holding PTT for the negotiated time lets the burst finish leaving the
        radio before the carrier drops. Key-up stays immediate — VARA starts
        modulating on its own clock, and keying late would clip the leader
        instead. Control bursts keep their normal timing; they are short and
        already carry their own tail guard.
        """
        if not enabled and self._payload_ptt_delay_ms > 0:
            time.sleep(self._payload_ptt_delay_ms / 1000.0)
        self._radio_ptt(enabled)

    def _payload_ptt(self, enabled: bool) -> None:
        """Key the radio for a payload transport that does its own keying.

        The sibling of `_vara_ptt`, for a backend that generates its own audio
        rather than handing it to VARA. Same negotiated slow-keying tail, for the
        same reason -- the cable and the handheld do not care which modem produced
        the burst. The transport adds its own lead and tail around this on top,
        because it knows how long its waveform is.
        """
        if not enabled and self._payload_ptt_delay_ms > 0:
            time.sleep(self._payload_ptt_delay_ms / 1000.0)
        self._radio_ptt(enabled)

    def _warn_if_nothing_can_key_vara(self) -> bool:
        """Warn when VARA is about to transmit and nobody can key the radio.

        The trap that cost OK2IPW an evening: host PTT off, so Guardian
        ignores VARA's "PTT ON", while Guardian's own rigctld holds the CAT
        port -- leaving VARA no port to key through either. The session looks
        perfect (CONNECT, BITRATE, PTT ON, then DISCONNECTED) and not one
        watt reaches the antenna. Returns True when the warning applied.
        """
        if self.config.payload_backend != "vara_p2p":
            # A native transport keys the radio itself through Guardian's own
            # driver, so the VARA host-PTT setting decides nothing here and the
            # warning would send the operator to fix something that is not wrong.
            return False
        if self.config.vara_host_ptt:
            return False
        if self.config.radio_backend != "hamlib" or not self.config.cat_port:
            return False
        self._log(
            dual(
                f"Guardian is not keying the radio for VARA and rigctld holds "
                f"{self.config.cat_port}, so VARA has no port left to key "
                "through. If the radio stays in receive, enable 'Let Guardian "
                "key the radio for VARA'.",
                f"Guardian pro VARA neklíčuje a rigctld drží "
                f"{self.config.cat_port}, takže VARA nemá čím klíčovat. Pokud "
                "rádio zůstane na příjmu, zapněte „Guardian klíčuje rádio pro "
                "VARA“.",
            ),
            LogLevel.WARNING,
            source="vara",
        )
        return True

    def _suspend_control(self) -> None:
        self._warn_if_nothing_can_key_vara()
        self._close_recording_losing_its_source(
            "a payload transfer needs the audio device")
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

    def _working_channel_guard(self) -> None:
        """Raise when this station must not act on a working channel at all."""
        if not self.config.auto_qsy:
            raise RuntimeError("automatic QSY is disabled")
        if self.config.radio_backend != "hamlib" or self.is_no_cat_radio():
            raise RuntimeError("separate working channels require a real CAT radio")

    def _working_mode_compatible(self, mode: str) -> bool:
        """Can VARA as configured here actually work on this mode?"""
        normal = (mode or "").strip().upper().replace("-", "")
        if (self.config.vara_mode or "FM").strip().upper() == "HF":
            return normal in {"USB", "LSB", "PKTUSB", "PKTLSB", "DATAUSB", "DATALSB"}
        return normal in {"FM", "NFM", "PKTFM", "DATAFM"}

    def _working_channel_offer(self, callsign: str) -> tuple[int, str] | None:
        """Return an opt-in payload channel, refusing unsafe automation."""
        if not self.config.separate_working_channels:
            return None
        target = self.routes.working_for(callsign)
        if target is None:
            return None
        self._working_channel_guard()
        frequency, mode = target
        if not self._working_mode_compatible(mode):
            raise RuntimeError(
                f"working mode {mode or '?'} is incompatible with VARA "
                f"{(self.config.vara_mode or 'FM').strip().upper()}"
            )
        return int(frequency), (mode or "").strip().upper()

    def _working_channel_accept(
        self, callsign: str, token: str
    ) -> tuple[int, str] | None:
        """Agree the payload channel the calling station proposed.

        Requiring both operators to have typed the identical working frequency
        into their own route tables made the negotiation fail for the ordinary
        case: two stations that each have a perfectly good working channel for
        this link, just not the same one. The station that opens the session
        names the channel, and this one follows it when it safely can.
        """
        try:
            local = self._working_channel_offer(callsign)
        except RuntimeError as exc:
            self._log(
                dual(
                    f"Working channel from {callsign} rejected: {exc}.",
                    f"Pracovní kanál od {callsign} odmítnut: {exc}.",
                ),
                LogLevel.WARNING,
                source="session",
            )
            return None
        if local is not None:
            try:
                if working_channel_token(*local) == token:
                    return local
            except ValueError:
                pass
        return self._follow_working_channel(callsign, token, local)

    def _follow_working_channel(
        self, callsign: str, token: str, local: tuple[int, str] | None
    ) -> tuple[int, str] | None:
        """Take the proposer's channel, inside an envelope this station sets.

        A peer may move this radio to another channel of the band the link
        already works on -- never onto another band, never outside the amateur
        service, never onto a mode the local VARA cannot use, and never at all
        unless the operator opted into two-channel sessions with a CAT radio.
        When this station knows of no band for the link at all, the amateur
        bands and the mode are the whole envelope: refusing on a reference we
        could not produce only breaks links that are otherwise fine.
        """
        if not self.config.separate_working_channels:
            return None
        try:
            self._working_channel_guard()
            frequency, mode = parse_working_channel_token(token)
        except (RuntimeError, ValueError) as exc:
            self._log(
                dual(
                    f"Working channel proposed by {callsign} refused: {exc}.",
                    f"Pracovní kanál navržený {callsign} odmítnut: {exc}.",
                ),
                LogLevel.WARNING,
                source="session",
            )
            return None
        channel = f"{frequency / 1_000_000:.4f} MHz {mode}"
        if not self._working_mode_compatible(mode):
            self._refuse_working_channel(
                callsign,
                dual(
                    f"{channel} is not a mode VARA "
                    f"{(self.config.vara_mode or 'FM').strip().upper()} can work",
                    f"{channel} není režim, se kterým VARA "
                    f"{(self.config.vara_mode or 'FM').strip().upper()} pracuje",
                ),
            )
            return None
        if band_for(frequency) is None:
            self._refuse_working_channel(
                callsign,
                dual(
                    f"{channel} is outside the amateur bands",
                    f"{channel} je mimo amatérská pásma",
                ),
            )
            return None
        # An unknown reference is not a reason to refuse. It was: the band test
        # silently failed closed whenever no reference could be produced -- a
        # peer with no route entry of its own, or one CAT poll that errored and
        # blanked the frequency in the snapshot -- and a link with nothing
        # wrong with it could not agree a channel.
        reference, origin = self._working_channel_reference(callsign, local)
        if reference is not None and not same_band(frequency, reference):
            self._refuse_working_channel(
                callsign,
                dual(
                    f"{channel} is not in the band this station works that "
                    f"peer on ({reference / 1_000_000:.4f} MHz, {origin})",
                    f"{channel} není v pásmu, na kterém tato stanice "
                    f"s protistanicí pracuje ({reference / 1_000_000:.4f} MHz, "
                    f"{origin})",
                ),
            )
            return None
        self._log(
            dual(
                f"Following {callsign} to its working channel "
                f"{frequency / 1_000_000:.4f} MHz {mode}"
                + (
                    f" (this station had {local[0] / 1_000_000:.4f} MHz "
                    f"{local[1]} configured)."
                    if local is not None
                    else "."
                ),
                f"Přelaďuji za stanicí {callsign} na její pracovní kanál "
                f"{frequency / 1_000_000:.4f} MHz {mode}"
                + (
                    f" (zde bylo nastaveno {local[0] / 1_000_000:.4f} MHz "
                    f"{local[1]})."
                    if local is not None
                    else "."
                ),
            ),
            source="session",
        )
        return frequency, mode

    def _refuse_working_channel(self, callsign: str, reason: str) -> None:
        """Log a refused proposal so the reason is on the air-side record."""
        self._log(
            dual(
                f"Working channel proposed by {callsign} refused: {reason}.",
                f"Pracovní kanál navržený {callsign} odmítnut: {reason}.",
            ),
            LogLevel.WARNING,
            source="session",
        )

    def _working_channel_reference(
        self, callsign: str, local: tuple[int, str] | None
    ) -> tuple[int | None, str]:
        """What a proposal is judged against, and where that came from.

        Four sources, most specific first, because any one of them can be
        missing on a perfectly healthy station: the peer may have no route
        entry here, and the radio snapshot carries no frequency at all after a
        single CAT poll error. Where the peer was last *heard* is the source
        that survives both. `(None, "")` means nothing is known, and the caller
        must not read that as a reason to refuse.
        """
        if local is not None:
            return local[0], dual("local working channel", "místní pracovní kanál")
        route = self.routes.freq_for(callsign)
        if route is not None and route[0]:
            return int(route[0]), dual("route frequency", "frekvence trasy")
        station = self.heard.get(callsign)
        if station is not None and station.last_freq_hz:
            return int(station.last_freq_hz), dual("heard on", "slyšeno na")
        here = self.current_frequency()
        if here:
            return int(here), dual("current channel", "aktuální kanál")
        return None, ""

    def _payload_send_qsy(self, message) -> bool:
        if self.config.separate_working_channels:
            if not message.working_frequency_hz:
                return True
            return self._qsy_to_channel(
                message.next_hop,
                message.working_frequency_hz,
                message.working_mode,
                allow_manual=False,
                settle=True,
            )
        # Preserve the old behaviour: absence of a route frequency simply
        # means that payload and control share the current channel.
        self._qsy_to(message.next_hop)
        return True

    def _payload_receive_qsy(self, message) -> bool:
        if not message.working_frequency_hz:
            return True
        return self._qsy_to_channel(
            message.source,
            message.working_frequency_hz,
            message.working_mode,
            allow_manual=False,
            settle=True,
        )

    def _payload_restore_calling(self) -> None:
        self._qsy_restore(settle=True)

    def _qsy_to(self, callsign: str) -> bool:
        if not self.config.auto_qsy:
            return False
        target = self.routes.freq_for(callsign)
        if target is None:
            return False
        frequency, mode = target
        return self._qsy_to_channel(
            callsign, frequency, mode, allow_manual=True, settle=False
        )

    def _qsy_to_channel(
        self,
        callsign: str,
        frequency: int,
        mode: str,
        *,
        allow_manual: bool,
        settle: bool,
    ) -> bool:
        if self.is_no_cat_radio():
            if not allow_manual:
                self._log(
                    dual(
                        "Automatic working-channel QSY requires a real CAT radio.",
                        "Automatické QSY na pracovní kanál vyžaduje skutečné CAT rádio.",
                    ),
                    LogLevel.WARNING,
                    source="radio",
                )
                return False
            here = int(self.config.manual_frequency_hz or 0)
            if here == frequency:
                return True
            confirm = self.confirm_manual_qsy
            if confirm is None or not confirm(callsign, frequency, mode):
                self._log(
                    dual(
                        f"Message to {callsign} cancelled: manual QSY to "
                        f"{frequency / 1_000_000:.4f} MHz was not confirmed.",
                        f"Zpráva pro {callsign} zrušena: ruční QSY na "
                        f"{frequency / 1_000_000:.4f} MHz nebylo potvrzeno.",
                    ),
                    LogLevel.WARNING,
                    source="radio",
                )
                return False
            # OK means the operator has physically tuned the dial. Persist the
            # newly reported truth; do not pretend that Dummy tuned it for us.
            self.set_manual_frequency(frequency)
            self._log(
                dual(
                    f"Manual QSY confirmed for {callsign}: "
                    f"{frequency / 1_000_000:.4f} MHz"
                    + (f" {mode}" if mode else ""),
                    f"Ruční QSY pro {callsign} potvrzeno: "
                    f"{frequency / 1_000_000:.4f} MHz"
                    + (f" {mode}" if mode else ""),
                ),
                source="radio",
            )
            return True
        try:
            with self._radio_lock:
                if self._qsy_previous is None:
                    previous = self.radio.get_state()
                    self._qsy_previous = previous.frequency_hz
                    self._qsy_previous_mode = getattr(previous, "mode", "") or ""
                self.radio.set_frequency(frequency)
                if mode:
                    self.radio.set_mode(mode)
            if settle:
                time.sleep(ALERT_SWEEP_SETTLE)
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

    def _qsy_restore(self, *, settle: bool = False) -> None:
        if self._qsy_previous is not None:
            try:
                with self._radio_lock:
                    self.radio.set_frequency(self._qsy_previous)
                    if self._qsy_previous_mode:
                        self.radio.set_mode(self._qsy_previous_mode)
                if settle:
                    time.sleep(ALERT_SWEEP_SETTLE)
            except Exception as exc:
                self._log(
                    dual(
                        f"Return to the calling channel failed: {exc}",
                        f"Návrat na volací kanál selhal: {exc}",
                    ),
                    LogLevel.ERROR,
                    source="radio",
                )
        self._qsy_previous = None
        self._qsy_previous_mode = ""

    def close(self) -> None:
        self.stop_scanner(restore=False, log=False)
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
