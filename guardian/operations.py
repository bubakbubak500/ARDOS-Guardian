"""UI-independent operational controller for radio, VARA and ARDOS sessions."""

from __future__ import annotations

import secrets
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from .config import G2_MAX_TX_SCALE, StationConfig, config_dir
from .install.dependencies import find_vara_fm, find_vara_hf
from .i18n import dual
from .message import Folder, MessageStore, Status
from .modem import make_modem
from .modem.audio import (
    PTT_LEAD_SECONDS,
    PTT_TAIL_SECONDS,
    TX_GUARD_SECONDS,
    AudioControlTransport,
    _import_sounddevice,
    audio_device_host_api,
    resolve_device,
    transmit_waveform,
)
from .payload import make_backend
from .payload.negotiated import NegotiatedPayload
from .payload.vara_p2p import transfer_identity_from_bundle
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
from .radio import Channel, ChannelPlan, ChannelScanner, make_driver
from .radio.bands import band_for, same_band
from .radio.presets import DUMMY_MODEL, find_executable
from .radio.rigctld_launcher import RigctldProcess
from .routing import HeardStations, RouteTable
from .services import (
    EventBus,
    LogEventKind,
    LogLevel,
    MailboxSnapshot,
    NetworkSnapshot,
    RadioSnapshot,
    SnapshotStore,
    StationLabSnapshot,
    TaskResult,
    VaraSnapshot,
    WorkerPool,
)
from .session import NullTransport, Orchestrator, SessionState
from .session.orchestrator import (
    g2_profile_token,
    parse_working_channel_token,
    working_channel_token,
)
from .station_lab import (
    CALIBRATION_PROTOCOL_VERSION,
    CalibrationMode,
    CalibrationReport,
    CalibrationState,
    GainJournal,
    ProbeCommand,
    ProbeResult,
    QUICK_TUNE_BURSTS,
    WindowsGainController,
    full_plan,
    quick_plan,
)
from .vara import VaraClient
from .waveforms.config import profile_for as experimental_profile_for


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
    """Mutable, UI-independent status for one paired SC-FTN AutoTune run."""

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


@dataclass
class QuickSweepMeasurement:
    """The local Quick Tune report and the point returned to the peer."""

    report: CalibrationReport
    selected: ProbeResult


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

# Pair/Quick AutoTune control timing.  These are deliberately bounded and
# independent of the normal mail-session retry timers: a lost report must only
# request a cached result, never cause a second RF sweep.
CALIBRATION_OFFER_MIN_INTERVAL = 9.0
CALIBRATION_OFFER_ATTEMPTS = 3
CALIBRATION_RX_SETTLE_SECONDS = 0.35
CALIBRATION_PROBE_GUARD_SECONDS = 0.85
CALIBRATION_REPORT_TIMEOUT = 20.0
CALIBRATION_REPORT_ATTEMPTS = 3
CALIBRATION_SWEEP_REPORT_GUARD = 0.75
CALIBRATION_SWEEP_QUIET_SECONDS = 4.0


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
    # A manual beacon may bypass the configured automatic interval, but two
    # accepted requests must never key the station back-to-back.  This also
    # gives an asynchronous audio transport time to account for the frame.
    _BEACON_MIN_GAP = 15.0

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
        # Mail deletion runs in a worker, while automatic delivery and
        # send_queued run from the operational tick. Keep the short
        # mailbox decision/transition atomic without serialising radio work.
        self._mail_mutation_lock = threading.RLock()
        self._mail_preparing: set[int] = set()
        # Serialise manual/automatic beacon requests separately from the radio
        # CAT lock.  Queueing a control frame does not touch the rig, and the
        # operation takes the CAT lock only as a nonblocking contention check.
        self._beacon_lock = threading.Lock()
        self._payload_active = threading.Event()
        # VARA can keep the native radio keyed after its TCP phase has failed.
        # Operations owns the deferred handoff so the control modem is never
        # restarted, retuned, or allowed to transmit until the RF path is quiet.
        self._payload_handoff_lock = threading.RLock()
        self._payload_handoff_resume: Callable[[], bool] | None = None
        self._payload_handoff_retry_at = 0.0
        self._payload_handoff_fault = ""
        self._payload_handoff_recovery_active = False
        self._closing = threading.Event()
        self._last_radio_poll = 0.0
        self._stored_inbound: set[int] = set()
        self._qsy_previous: int | None = None
        self._qsy_previous_mode: str = ""
        self._last_beacon = 0.0
        self._last_beacon_request: float | None = None
        self._last_auto_deliver = 0.0
        self._auto_delivery_attempted: dict[int, float] = {}
        self._vara_process: subprocess.Popen | None = None
        # Negotiated slow-keying hold-off for the payload session in flight.
        self._payload_ptt_delay_ms = 0
        # Set by the Qt shell. Operations stays UI-independent, while every
        # no-CAT QSY still has a synchronous operator safety gate.
        self.confirm_manual_qsy: Callable[[str, int, str], bool] | None = None
        self.alerts: list[AlertRecord] = []      # newest first
        # Paired AutoTune is opt-in and starts idle.  Keep all calibration state
        # beside the operational controller so the Qt layer can poll it without
        # owning radio, audio, or worker objects.
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
        self._calibration_rx_completed = False
        self._calibration_last_quick_measurement: QuickSweepMeasurement | None = None
        self._calibration_turn_pending = False
        self._calibration_turn_sent_at = 0.0
        self._calibration_turn_attempts = 0
        self._calibration_reverse_started = False
        self._calibration_done_pending = False
        self._calibration_done_sent_at = 0.0
        self._calibration_done_attempts = 0
        self._restore_calibration_gain_after_crash()
        self.net = self._build_net(NullTransport())
        self.scanner: ChannelScanner | None = None
        self._scanner_home: Channel | None = None
        self._scanner_paused = False
        self._scanner_last_heard = 0.0
        self._scanner_generation = 0

    def _restore_calibration_gain_after_crash(self) -> None:
        """Recover an opt-in Windows mixer snapshot after an interrupted run."""
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
        saved_device = " ".join(str(snapshot.device or "").casefold().split())
        current_device = " ".join(str(current.device or "").casefold().split())
        if not saved_device or not current_device or current_device != saved_device:
            self.station_lab.error = dual(
                "AutoTune kept the Windows-volume snapshot because the saved "
                "audio endpoint is missing or no longer selected.",
                "AutoTune ponechal snapshot hlasitosti Windows, protože uložený "
                "zvukový endpoint chybí nebo už není vybrán.",
            )
            return
        saved_host = str(snapshot.host_api or "").strip().casefold()
        current_host = str(current.host_api or "").strip().casefold()
        if (
            not saved_host
            or not current_host
            or saved_host in {"unknown", "unresolved"}
            or current_host in {"unknown", "unresolved"}
            or saved_host != current_host
        ):
            self.station_lab.error = dual(
                "AutoTune kept the Windows-volume snapshot because the audio "
                "host API changed.",
                "AutoTune ponechal snapshot hlasitosti Windows, protože se změnilo "
                "zvukové API.",
            )
            return
        try:
            gain.restore(snapshot)
        except Exception as exc:  # noqa: BLE001 - preserve journal for retry
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
        kind: LogEventKind | str = LogEventKind.GENERAL,
    ) -> None:
        self.events.publish(message, level=level, source=source, kind=kind)

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
        # Retry airtime includes the modem's acquisition/repetition allowance;
        # the direct first-attempt airtime remains the discovery estimate.
        retry_airtime = getattr(modem, "retry_airtime", airtime)
        uart_guard = max(
            0.0, float(getattr(self.radio, "uart_to_ptt_guard_seconds", 0.0))
        )
        exchange = (
            2 * retry_airtime(self._CONTROL_FRAME_BYTES)
            + self._TURNAROUND
            + 2 * uart_guard
        )
        if hasattr(net, "control_exchange_timeout"):
            net.control_exchange_timeout = exchange
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
            clock=time.monotonic if isinstance(transport, AudioControlTransport) else None,
        )
        net.on_alert = self._on_alert
        net.on_discovery_event = self._on_discovery_event
        net.channel_frequency = self.current_frequency
        net.ptt_delay_request = self._vara_keying_delay_request
        net.ofdm_payload_request = self._ofdm_payload_configured
        net.g2_profile = self._g2_profile_token
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
        if self._calibration_active():
            self._log(
                dual(
                    "Network settings were not applied during station calibration.",
                    "Nastavení sítě nebylo použito během kalibrace stanice.",
                ),
                LogLevel.WARNING,
                source="station-lab",
            )
            return
        if self._payload_active.is_set() or self._active_session_count():
            self._log(
                dual(
                    "Network settings were not applied while a session is active.",
                    "Nastavení sítě nebylo použito během aktivní relace.",
                ),
                LogLevel.WARNING,
                source="session",
            )
            return
        if self.payload_handoff_pending():
            self._log(
                dual(
                    "Network settings were not applied while radio handoff is active.",
                    "Nastavení sítě nebylo použito během předávání rádia.",
                ),
                LogLevel.WARNING,
                source="payload",
            )
            return
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
        self.net.ofdm_payload_request = self._ofdm_payload_configured
        self.net.g2_profile = self._g2_profile_token
        self.net.on_calibration_frame = self._on_calibration_frame
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

    def _ofdm_payload_configured(self) -> bool:
        """Whether this station advertises the opt-in SC-FTN payload."""
        return self.config.payload_backend == "ofdm_vhf"

    def _g2_profile_token(self) -> str:
        """Return the exact local SC-FTN profile token used per hop."""
        return g2_profile_token(
            self.config.g2_waveform,
            self.config.g2_bandwidth,
            self.config.g2_policy_version,
        )

    def _vara_keying_delay_request(self) -> int:
        """Slow-keying hold-off this station asks its peer for, in ms.

        Zero unless the operator configured one *and* the band is FM: HF
        stations run better radios and the extra dead air would only slow the
        net down, so the request never leaves an HF configuration.
        """
        if self.config.vara_mode.upper() != "FM":
            return 0
        return max(0, min(int(self.config.vara_ptt_delay_ms or 0), MAX_PTT_DELAY_MS))

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
        elif not self.has_frequency_control():
            reason = dual(
                "a radio with automatic frequency control is required",
                "je potřeba rádio s automatickým řízením frekvence",
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

    def _mail_delete_block_reason(
        self, message_ids: Iterable[int] | None = None
    ) -> str | None:
        """Return a user-facing reason why mailbox deletion must wait.

        The check is called while ``_mail_mutation_lock`` is held. In addition
        to live session state, the preparing set covers the compression window
        before a session exists and after ``send_queued`` has marked SENDING.
        """
        if self._payload_active.is_set() or self._active_session_count():
            return dual(
                "Mail was not deleted: a transfer is in progress.",
                "Zprávy nebyly odstraněny: probíhá přenos.",
            )
        preparing = (
            set(self._mail_preparing)
            if message_ids is None
            else set(message_ids).intersection(self._mail_preparing)
        )
        if preparing:
            return dual(
                "Mail was not deleted: a selected message is being prepared for transmission.",
                "Zprávy nebyly odstraněny: vybraná zpráva se připravuje k přenosu.",
            )
        return None

    def _mailbox_snapshot(self) -> MailboxSnapshot:
        counts = self.mailstore.counts()
        return MailboxSnapshot(
            inbox=counts.get(Folder.INBOX, 0),
            unread=self.mailstore.unread(Folder.INBOX),
            outbox=counts.get(Folder.OUTBOX, 0),
            outbox_failed=self.mailstore.failed(Folder.OUTBOX),
            transit=counts.get(Folder.TRANSIT, 0),
        )

    def delete_mail(
        self,
        message_ids: Iterable[int],
        *,
        folder: str | None = None,
    ) -> int:
        """Delete selected messages from one folder after an in-worker guard.

        ``folder`` is checked again inside the worker so a message moved by
        delivery after the UI selection cannot cause deletion from another
        folder. A guard failure raises ``RuntimeError`` for the UI callback.
        """
        ids = tuple(dict.fromkeys(int(value) for value in message_ids))
        if not ids:
            return 0
        with self._mail_mutation_lock:
            reason = self._mail_delete_block_reason(ids)
            if reason is not None:
                raise RuntimeError(reason)
            indexed = {
                int(meta["msg_id"]): meta
                for meta in self.mailstore.list()
                if "msg_id" in meta
            }
            if folder is not None:
                ids = tuple(
                    msg_id
                    for msg_id in ids
                    if indexed.get(msg_id, {}).get("folder") == folder
                )
            removed = 0
            failures: list[tuple[int, BaseException]] = []
            for msg_id in ids:
                try:
                    if self.mailstore.delete(msg_id, folder=folder):
                        removed += 1
                except Exception as exc:  # worker reports file errors to UI
                    failures.append((msg_id, exc))
            self.snapshots.update(mailbox=self._mailbox_snapshot())
        if failures:
            detail = "; ".join(
                f"#{msg_id}: {exc}" for msg_id, exc in failures
            )
            self._log(
                dual(
                    f"Selected mail partly deleted: {removed} removed; "
                    f"{len(failures)} failed ({detail}).",
                    f"Vybrané zprávy byly odstraněny jen částečně: {removed}; "
                    f"selhalo {len(failures)} ({detail}).",
                ),
                LogLevel.WARNING,
                source="mail",
            )
            if not removed:
                raise RuntimeError(detail)
        self._log(
            dual(
                f"Selected mail deleted: {removed} messages.",
                f"Vybrané zprávy odstraněny: {removed} zpráv.",
            ),
            source="mail",
        )
        return removed

    def clear_mailstore(self) -> int:
        """Delete every stored message; refused while a transfer is running.

        A session mid-flight still reads its message by id, so wiping under it
        would fail the transfer in a confusing way. Returns the number of
        messages removed, or -1 for a refusal.
        """
        with self._mail_mutation_lock:
            reason = self._mail_delete_block_reason()
            if reason is not None:
                self._log(reason, LogLevel.WARNING, source="mail")
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
        """True while VARA owns the shared audio; the UI keeps quiet then."""
        return self._payload_active.is_set()

    def network_settings_busy(self) -> bool:
        """Whether changing network, audio, or radio settings must wait.

        A session is visible in ``net.sessions`` during control negotiation,
        before the payload worker sets ``_payload_active``.  Consumers that
        edit the shared ``StationConfig`` should call this before mutating it;
        Operations repeats the check in each apply/reconfigure entry point.
        """
        return bool(
            self._closing.is_set()
            or self._calibration_active()
            or self._payload_active.is_set()
            or self._active_session_count()
            or self.payload_handoff_pending()
            or bool(getattr(self.station_lab, "pending_offer", False))
        )

    def ofdm_status(self):
        """Return status for the session that actually negotiated SC-FTN.

        The configured OFDM backend may retain a failed status while the next
        transfer falls back to VARA.  Resolve the active message first so that
        the transfer UI never presents stale OFDM state for a VARA hop.
        """
        if not self._payload_active.is_set():
            return None
        payload_states = {
            SessionState.STARTING_VARA,
            SessionState.TRANSFERRING,
            SessionState.RECEIVING,
        }
        active_message = next(
            (
                message
                for message in tuple(self.net.sessions.values())
                if message.state in payload_states
                and getattr(message, "payload_transport", "vara_p2p")
                == "ofdm_vhf"
            ),
            None,
        )
        if active_message is None:
            return None
        payload = getattr(self.net, "payload", None)
        backends = getattr(payload, "backends", None)
        if isinstance(backends, dict):
            payload = backends.get("ofdm_vhf")
        if getattr(payload, "name", None) != "ofdm_vhf":
            return None
        status = getattr(payload, "status", None)
        if status is None:
            return None
        source, destination, via = self._ofdm_transfer_context(active_message)
        # Keep the backend's live object untouched.  A dataclass replacement is
        # a coherent poll snapshot and prevents a later VARA hop or a worker
        # update from mutating the object already handed to the UI.
        if (
            getattr(status, "transfer_source", "") == source
            and getattr(status, "transfer_destination", "") == destination
            and getattr(status, "transfer_via", "") == via
        ):
            return status
        try:
            return replace(
                status,
                transfer_source=source,
                transfer_destination=destination,
                transfer_via=via,
            )
        except TypeError:
            # Older third-party stand-ins may expose a status-shaped object but
            # not a dataclass.  Do not mutate it or let a status poll break the
            # transfer UI; the typed backend uses the branch above.
            return status

    def _ofdm_transfer_context(self, message) -> tuple[str, str, str]:
        """Return trusted identity for the active SC-FTN session leg.

        An inbound control frame identifies only its immediate sender.  That
        station may be a relay, so it is shown as ``via`` while ``source`` stays
        empty until a validated bundle manifest supplies the original source.
        Outbound relay messages are newly originated by this station and may use
        their session source; the next hop remains the leg's ``via`` value.
        """
        direction = str(getattr(message, "direction", "out") or "out").lower()
        immediate_source = str(getattr(message, "source", "") or "").strip().upper()
        destination = str(
            getattr(message, "final_dest", "") or ""
        ).strip().upper()
        if direction == "in":
            source = ""
            via = immediate_source
            payload_bytes = getattr(message, "payload_bytes", None)
            if isinstance(payload_bytes, (bytes, bytearray, memoryview)):
                try:
                    identity = transfer_identity_from_bundle(bytes(payload_bytes))
                except Exception:  # noqa: BLE001 - identity is best effort
                    identity = None
                if identity is not None:
                    source = str(getattr(identity, "source", "") or "").strip().upper()
                    destination = (
                        str(getattr(identity, "destination", "") or "")
                        .strip()
                        .upper()
                        or destination
                    )
            return source, destination, via
        source = immediate_source
        via = str(getattr(message, "next_hop", "") or "").strip().upper()
        payload_bytes = getattr(message, "payload_bytes", None)
        if isinstance(payload_bytes, (bytes, bytearray, memoryview)):
            try:
                identity = transfer_identity_from_bundle(bytes(payload_bytes))
            except Exception:  # noqa: BLE001 - identity is best effort
                identity = None
            if identity is not None:
                source = (
                    str(getattr(identity, "source", "") or "").strip().upper()
                    or source
                )
                destination = (
                    str(getattr(identity, "destination", "") or "")
                    .strip()
                    .upper()
                    or destination
                )
        return source, destination, via

    def _calibration_active(self) -> bool:
        return self.station_lab.state in {
            CalibrationState.OFFERING.value,
            CalibrationState.WAITING_APPROVAL.value,
            CalibrationState.PREPARING.value,
            CalibrationState.MEASURING.value,
            CalibrationState.WAITING_REPORT.value,
        }

    def is_no_cat_radio(self) -> bool:
        return (
            self.config.radio_backend == "vox"
            or (
                self.config.radio_backend == "hamlib"
                and int(self.config.rig_model or 0) == DUMMY_MODEL
            )
        )

    def has_frequency_control(self) -> bool:
        """Whether this backend can read and change the physical radio dial."""
        return (
            self.config.radio_backend
            in {"hamlib", "guardian_k5", "guardian_k61"}
            and not self.is_no_cat_radio()
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
            kind=LogEventKind.ALERT,
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
            kind=LogEventKind.ALERT,
        )

    def apply_vara_session_settings(self) -> bool:
        """Push the per-session tuning commands VARA cannot infer.

        Sent when VARA connects *and* whenever the operator changes them --
        VARA has no way to learn a setting that so far only existed in
        Guardian's config, so before 0.6.33 changing the HF bandwidth left the
        modem on whatever it was given at connect time.

        `CHAT OFF` bounds VARA's idle loops. FILES handles the binary envelope
        independently of Guardian's optional bundle compression. The retired
        compression checkbox must not silently switch the native modem back
        to TEXT after a configuration migration. Bandwidth and
        `P2P SESSION` are HF/SAT only -- the
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
        self.vara.send_command("COMPRESSION FILES")
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

    def reconfigure_radio(self) -> bool:
        """Rebuild the driver after the operator changes radio settings.

        Before this existed a backend or PTT change only took effect after an
        application restart: `make_driver` ran once in `__init__` and the old
        driver (with the old port, host, and `reports_ptt`) lived on. The
        rigctld child is kept — `ensure()` already restarts it when its
        command line no longer matches.
        """
        if self._calibration_active():
            self._log(
                dual(
                    "Radio settings were not changed during station calibration.",
                    "Nastavení rádia nebylo změněno během kalibrace stanice.",
                ),
                LogLevel.WARNING,
                source="station-lab",
            )
            return False
        if self._payload_active.is_set() or self._active_session_count():
            self._log(
                dual(
                    "Radio settings were not changed while a session is active.",
                    "Nastavení rádia nebylo změněno během aktivní relace.",
                ),
                LogLevel.WARNING,
                source="session",
            )
            return False
        if self.payload_handoff_pending():
            self._log(
                dual(
                    "Radio settings were not changed while radio handoff is active.",
                    "Nastavení rádia nebylo změněno během předávání rádia.",
                ),
                LogLevel.WARNING,
                source="payload",
            )
            return False
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
        return (
            self.config.vara_hf_bandwidth,
            self.config.vara_file_compression,
        )

    # ----- transmitting a test burst --------------------------------------

    def transmit_test_burst(
        self,
        *,
        profile_name: str | None = None,
        mcs_index: int | None = None,
        waveform_family: str = "sc_ftn",
        bandwidth: str | None = None,
        fec=None,
        tx_scale: float | None = None,
        seed: int = 0xA5,
        payload_bytes: int = 512,
        repeats: int = 3,
        on_log=None,
    ):
        """Transmit an explicit SC-FTN test burst through the normal PTT guard."""
        if self.payload_active():
            return None
        family = str(waveform_family or "sc_ftn").strip().lower()
        if family != "sc_ftn":
            raise ValueError("SC-FTN is the only production test waveform")
        from .waveforms import bench as experimental_bench
        from .waveforms.config import profile_for, profile_or_default

        if profile_name:
            selected_profile = profile_or_default(profile_name)
            if selected_profile.family != family:
                raise ValueError("test profile must be an SC-FTN profile")
        else:
            selected_profile = profile_for(
                family, bandwidth or self.config.g2_bandwidth
            )
        index = self.config.g2_mcs if mcs_index is None else int(mcs_index)
        samples, _ = experimental_bench.make_test_burst(
            selected_profile,
            index,
            payload_bytes=payload_bytes,
            repeats=repeats,
            seed=int(seed) & 0xFFFFFFFF,
            fec=(self.config.ofdm_fec if fec is None else fec),
        )
        scale = (
            self._current_g2_tx_scale()
            if tx_scale is None else float(tx_scale)
        )
        scale = min(G2_MAX_TX_SCALE, max(0.001, scale))
        samples = np.asarray(samples, dtype=np.float64) * scale
        output = resolve_device(self.config.audio_output, "output")
        if not isinstance(output, int):
            if on_log is not None:
                on_log(dual(
                    "Select an available TX output in Station settings first.",
                    "Nejprve vyberte dostupný výstup TX v nastavení stanice.",
                ))
            return None
        acquired = False
        try:
            self._suspend_control()
            acquired = True
            sd = _import_sounddevice()
            sd.check_output_settings(
                device=output,
                samplerate=selected_profile.sample_rate,
                channels=1,
            )
            return transmit_waveform(
                sd,
                samples,
                device=output,
                sample_rate=selected_profile.sample_rate,
                ptt=self._payload_ptt,
                lead_seconds=max(
                    self.config.ofdm_tx_lead_ms / 1000.0,
                    PTT_LEAD_SECONDS,
                ),
                tail_seconds=max(
                    self.config.ofdm_tx_tail_ms / 1000.0,
                    PTT_TAIL_SECONDS,
                ),
            )
        finally:
            if acquired:
                self._resume_control()

    # ----- paired SC-FTN station calibration -----------------------------

    def start_station_calibration(
        self, peer: str, mode: str = CalibrationMode.QUICK.value,
    ) -> bool:
        """Offer a bounded, opt-in SC-FTN AutoTune run to one peer."""
        if not self._ofdm_payload_configured():
            self.station_lab.error = dual(
                "Select SC-FTN before starting Auto Tune.",
                "Před spuštěním Auto Tune vyberte SC-FTN.",
            )
            return False
        target = str(peer or "").strip().upper()
        selected_mode = (
            CalibrationMode.FULL
            if str(mode).strip().lower() == CalibrationMode.FULL.value
            else CalibrationMode.QUICK
        )
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
            CalibrationState.IDLE.value,
            CalibrationState.COMPLETE.value,
            CalibrationState.CANCELLED.value,
            CalibrationState.FAILED.value,
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
        self._calibration_rx_completed = False
        self._calibration_last_quick_measurement = None
        self._calibration_turn_pending = False
        self._calibration_turn_attempts = 0
        self._calibration_reverse_started = False
        self._calibration_done_pending = False
        self._calibration_done_attempts = 0
        self.station_lab = StationLabStatus(
            state=CalibrationState.OFFERING.value,
            peer=target,
            session_id=session_id,
            mode=selected_mode.value,
            message=dual(
                f"Calling {target} for station calibration…",
                f"Volám {target} pro kalibraci stanice…",
            ),
        )
        self._send_calibration_offer()
        self._update_station_lab_snapshot()
        return True

    def _send_calibration_offer(self) -> None:
        status = self.station_lab
        attempt = self._calibration_offer_attempts + 1
        self.net.send_calibration_frame(
            FrameType.CAL_OFFER,
            status.peer,
            status.session_id,
            f"{'F' if status.mode == CalibrationMode.FULL.value else 'Q'}"
            f"{CALIBRATION_PROTOCOL_VERSION}-{attempt}",
        )
        self._calibration_offer_attempts = attempt
        self._calibration_offer_sent_at = time.monotonic()

    def _tick_station_calibration(self, now: float) -> None:
        """Retry bounded consent/turn/done handshakes and then fail closed."""
        status = self.station_lab
        interval = max(CALIBRATION_OFFER_MIN_INTERVAL, self.net.ack_timeout)
        if self._calibration_turn_pending:
            if now - self._calibration_turn_sent_at < interval:
                return
            if self._calibration_turn_attempts < CALIBRATION_OFFER_ATTEMPTS:
                self._send_calibration_turn()
                return
            self._calibration_turn_pending = False
            self.net.send_calibration_frame(
                FrameType.CAL_CANCEL, status.peer, status.session_id, "TURN_TIMEOUT"
            )
            status.state = CalibrationState.FAILED.value
            status.error = dual(
                f"{status.peer} did not confirm the calibration direction change.",
                f"{status.peer} nepotvrdil otočení směru kalibrace.",
            )
            return
        if self._calibration_done_pending:
            if now - self._calibration_done_sent_at < interval:
                return
            if self._calibration_done_attempts < CALIBRATION_OFFER_ATTEMPTS:
                self._send_calibration_done()
                return
            self._calibration_done_pending = False
            self.net.send_calibration_frame(
                FrameType.CAL_CANCEL, status.peer, status.session_id, "DONE_TIMEOUT"
            )
            status.state = CalibrationState.FAILED.value
            status.error = dual(
                f"{status.peer} did not confirm calibration completion.",
                f"{status.peer} nepotvrdil dokončení kalibrace.",
            )
            return
        if status.state != CalibrationState.OFFERING.value:
            return
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
        """Accept a pending offer; the caller must make this an explicit action."""
        if not self._ofdm_payload_configured():
            return False
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
            FrameType.CAL_ACCEPT,
            status.peer,
            status.session_id,
            f"{'F' if status.mode == CalibrationMode.FULL.value else 'Q'}"
            f"{CALIBRATION_PROTOCOL_VERSION}",
        )
        self._update_station_lab_snapshot()
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
        self._update_station_lab_snapshot()
        return True

    def cancel_station_calibration(self) -> bool:
        status = self.station_lab
        if status.state in {
            CalibrationState.IDLE.value,
            CalibrationState.COMPLETE.value,
        }:
            return False
        self._calibration_cancel.set()
        self._calibration_turn_pending = False
        self._calibration_done_pending = False
        if status.peer and status.session_id:
            self.net.send_calibration_frame(
                FrameType.CAL_CANCEL, status.peer, status.session_id, "CANCEL"
            )
        status.state = CalibrationState.CANCELLED.value
        status.message = dual("Calibration cancelled.", "Kalibrace zrušena.")
        self._update_station_lab_snapshot()
        return True

    def apply_station_calibration(self) -> bool:
        """Apply a report only after the operator explicitly chooses Apply."""
        report = self.station_lab.report
        recommendation = report.recommendation if report is not None else None
        if recommendation is None:
            return False
        self._apply_quick_station_level(
            recommendation.waveform,
            recommendation.bandwidth,
            recommendation.tx_scale,
        )
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
        self._update_station_lab_snapshot()
        return True

    def _on_calibration_frame(self, frame: ControlFrame) -> None:
        """Handle a directed AutoTune frame without creating a mail session."""
        if frame.type is FrameType.CAL_OFFER:
            self._calibration_offer(frame)
            return
        status = self.station_lab
        if (
            frame.message_id != status.session_id
            or frame.source.strip().upper() != status.peer
        ):
            return
        if frame.type is FrameType.CAL_ACCEPT:
            if status.state != CalibrationState.OFFERING.value:
                return
            expected = (
                f"{'F' if status.mode == CalibrationMode.FULL.value else 'Q'}"
                f"{CALIBRATION_PROTOCOL_VERSION}"
            )
            if frame.next_hop.strip().upper() != expected:
                self.net.send_calibration_frame(
                    FrameType.CAL_CANCEL, status.peer, status.session_id, "VERSION"
                )
                status.state = CalibrationState.FAILED.value
                status.error = dual(
                    "The peer uses an incompatible station-calibration protocol.",
                    "Protistanice používá nekompatibilní protokol kalibrace stanice.",
                )
                return
            self._calibration_offer_attempts = 0
            status.state = CalibrationState.PREPARING.value
            status.message = dual(
                f"{status.peer} accepted; preparing the measurement.",
                f"{status.peer} přijal požadavek; připravuji měření.",
            )
            queued = self.workers.submit(
                "station-calibration",
                self._run_station_calibration,
                self._station_calibration_finished,
            )
            if not queued:
                status.state = CalibrationState.FAILED.value
                status.error = dual(
                    "Calibration worker is already active.",
                    "Kalibrační úloha již běží.",
                )
            return
        if frame.type is FrameType.CAL_PROBE:
            try:
                command = ProbeCommand.decode(frame.next_hop)
            except (TypeError, ValueError) as exc:
                self._log(
                    f"Calibration probe rejected: {exc}",
                    LogLevel.WARNING,
                    source="station-lab",
                )
                return
            if status.state not in {
                CalibrationState.PREPARING.value,
                CalibrationState.MEASURING.value,
            }:
                return
            if self._calibration_initiator:
                self._calibration_turn_pending = False
            if self._calibration_rx_completed:
                # A duplicate probe only asks for the cached Quick result.
                measurement = self._calibration_last_quick_measurement
                if status.mode == CalibrationMode.QUICK.value and measurement:
                    self.net.send_calibration_frame(
                        FrameType.CAL_REPORT,
                        status.peer,
                        status.session_id,
                        measurement.selected.encode_report(),
                    )
                return
            if self.workers.is_active("station-calibration-rx"):
                return
            status.state = CalibrationState.MEASURING.value
            status.total = (
                QUICK_TUNE_BURSTS
                if status.mode == CalibrationMode.QUICK.value else 1
            )
            status.progress = 0
            status.current = dual(
                f"Receiving {command.waveform} MCS{command.mcs}",
                f"Přijímám {command.waveform} MCS{command.mcs}",
            )
            queued = self.workers.submit(
                "station-calibration-rx",
                lambda: (
                    self._receive_quick_calibration(command)
                    if status.mode == CalibrationMode.QUICK.value
                    else self._receive_calibration_probe(command)
                ),
                self._station_calibration_rx_finished,
            )
            if queued:
                self._calibration_last_rx_command = command
            return
        if frame.type is FrameType.CAL_REPORT:
            for candidate in list(self._calibration_commands.values()):
                try:
                    result = ProbeResult.decode_report(frame.next_hop, candidate)
                except (TypeError, ValueError):
                    continue
                self._calibration_results[result.sequence] = result
                self._calibration_report_ready.set()
                break
            return
        if frame.type in {FrameType.CAL_BUSY, FrameType.CAL_CANCEL}:
            self._calibration_cancel.set()
            self._calibration_turn_pending = False
            self._calibration_done_pending = False
            status.state = (
                CalibrationState.FAILED.value
                if frame.type is FrameType.CAL_BUSY
                else CalibrationState.CANCELLED.value
            )
            status.error = dual(
                f"{status.peer} refused or cancelled calibration.",
                f"{status.peer} kalibraci odmítl nebo zrušil.",
            )
            return
        if frame.type is FrameType.CAL_DONE:
            marker = frame.next_hop.strip().upper()
            if marker == "TURN" and not self._calibration_initiator:
                if self._calibration_reverse_started:
                    if status.state == CalibrationState.COMPLETE.value:
                        self.net.send_calibration_frame(
                            FrameType.CAL_DONE,
                            status.peer,
                            status.session_id,
                            "DONE",
                        )
                    return
                self._calibration_reverse_started = True
                status.state = CalibrationState.PREPARING.value
                status.message = dual(
                    "The first direction is complete; reversing the link.",
                    "První směr je hotový; obracím směr linky.",
                )
                self.workers.submit(
                    "station-calibration",
                    self._run_station_calibration,
                    self._station_calibration_finished,
                )
            elif marker == "DONE" and self._calibration_initiator:
                self.net.send_calibration_frame(
                    FrameType.CAL_DONE,
                    status.peer,
                    status.session_id,
                    "COMPLETE",
                )
                self._calibration_turn_pending = False
                status.state = CalibrationState.COMPLETE.value
                status.message = dual(
                    "Both directions completed calibration.",
                    "Kalibrace v obou směrech byla dokončena.",
                )
            elif marker == "COMPLETE" and self._calibration_done_pending:
                self._calibration_done_pending = False
                status.state = CalibrationState.COMPLETE.value
                status.message = dual(
                    "Both directions completed calibration.",
                    "Kalibrace v obou směrech byla dokončena.",
                )
            else:
                if status.state in {
                    CalibrationState.COMPLETE.value,
                    CalibrationState.CANCELLED.value,
                    CalibrationState.FAILED.value,
                }:
                    return
                self._calibration_turn_pending = False
                self._calibration_done_pending = False
                status.state = CalibrationState.FAILED.value
                status.error = dual(
                    f"Unknown station-calibration completion marker: {marker}.",
                    f"Neznámá značka dokončení kalibrace stanice: {marker}.",
                )
                self._log(status.error, LogLevel.WARNING, source="station-lab")
                self.net.send_calibration_frame(
                    FrameType.CAL_CANCEL,
                    status.peer,
                    status.session_id,
                    "MARKER",
                )

    def _calibration_offer(self, frame: ControlFrame) -> None:
        source = frame.source.strip().upper()
        if not self._ofdm_payload_configured():
            self.net.send_calibration_frame(
                FrameType.CAL_BUSY, source, frame.message_id, "SC-FTN OFF"
            )
            return
        status = self.station_lab
        same_offer = (
            bool(source)
            and source == status.peer
            and frame.message_id == status.session_id
        )
        if same_offer:
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
                    FrameType.CAL_ACCEPT,
                    status.peer,
                    status.session_id,
                    f"{'F' if status.mode == CalibrationMode.FULL.value else 'Q'}"
                    f"{CALIBRATION_PROTOCOL_VERSION}",
                )
                return
        if (
            not source
            or self.payload_active()
            or self.station_lab.state not in {
                CalibrationState.IDLE.value,
                CalibrationState.COMPLETE.value,
                CalibrationState.CANCELLED.value,
                CalibrationState.FAILED.value,
            }
        ):
            self.net.send_calibration_frame(
                FrameType.CAL_BUSY, source, frame.message_id, "BUSY"
            )
            return
        token = frame.next_hop.strip().upper()
        try:
            offered_version = int(token[1:].split("-", 1)[0])
        except (ValueError, IndexError):
            offered_version = 0
        if (
            offered_version != CALIBRATION_PROTOCOL_VERSION
            or not token.startswith(("Q", "F"))
        ):
            self.net.send_calibration_frame(
                FrameType.CAL_BUSY, source, frame.message_id, "VERSION"
            )
            return
        mode = (
            CalibrationMode.FULL.value
            if token.startswith("F") else CalibrationMode.QUICK.value
        )
        self.station_lab = StationLabStatus(
            state=CalibrationState.WAITING_APPROVAL.value,
            peer=source,
            session_id=frame.message_id,
            mode=mode,
            pending_offer=True,
            message=dual(
                f"{source} requests a {'full' if mode == 'full' else 'quick'} station test.",
                f"{source} žádá {'úplný' if mode == 'full' else 'rychlý'} test stanice.",
            ),
        )
        self._calibration_initiator = False
        self._calibration_cancel.clear()
        self._calibration_rx_completed = False
        self._calibration_last_quick_measurement = None
        self._calibration_turn_pending = False
        self._calibration_turn_attempts = 0
        self._calibration_reverse_started = False
        self._calibration_done_pending = False
        self._calibration_done_attempts = 0
        allowed = {str(value).strip().upper() for value in self.config.calibration_allowlist}
        if self.config.calibration_auto_accept and source in allowed:
            self.accept_station_calibration()

    def _run_station_calibration(self) -> CalibrationReport:
        status = self.station_lab
        started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        report = CalibrationReport(
            status.session_id,
            status.peer,
            status.mode,
            "outbound",
            started,
            radio=self.config.radio,
            audio_input=self.config.audio_input,
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
        if status.mode == CalibrationMode.QUICK.value:
            return self._run_quick_station_calibration(report)

        plan = full_plan()
        status.total = len(plan)
        status.progress = 0
        gain = WindowsGainController(self.config.audio_output)
        snapshot = gain.snapshot()
        journal = GainJournal()
        windows_sweep = bool(self.config.calibration_windows_gain and snapshot.supported)
        if windows_sweep:
            journal.arm(snapshot)
        stop_reason = "complete"
        try:
            deadline = time.monotonic() + min(
                900, max(30, int(self.config.calibration_max_seconds))
            )
            for index, command in enumerate(plan, 1):
                if self._calibration_cancel.is_set() or time.monotonic() >= deadline:
                    break
                endpoint = None
                if windows_sweep:
                    ladder = (0.35, 0.50, 0.65, 0.80, 0.95)
                    endpoint = ladder[min(
                        len(ladder) - 1,
                        (index - 1) * len(ladder) // max(1, len(plan)),
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
                    FrameType.CAL_PROBE,
                    status.peer,
                    status.session_id,
                    command.encode(),
                )
                control_flushed = True
                if self.audio_transport is not None:
                    control_flushed = bool(
                        self.audio_transport.wait_tx_idle(timeout=8.0)
                    )
                if endpoint is not None:
                    gain.set_endpoint_volume(endpoint)
                if control_flushed:
                    time.sleep(CALIBRATION_PROBE_GUARD_SECONDS)
                point_started = time.monotonic()
                aired = (
                    self.transmit_test_burst(
                        waveform_family=command.waveform,
                        bandwidth=command.bandwidth,
                        mcs_index=command.mcs,
                        fec=command.fec,
                        tx_scale=command.tx_scale,
                        payload_bytes=512,
                        repeats=1,
                        seed=status.session_id ^ command.sequence,
                    )
                    if control_flushed else None
                )
                if windows_sweep:
                    gain.restore(snapshot)
                status.state = CalibrationState.WAITING_REPORT.value
                got = (
                    bool(self._calibration_report_ready.wait(
                        timeout=CALIBRATION_REPORT_TIMEOUT
                    )) if control_flushed else False
                )
                result = (
                    self._calibration_results.pop(command.sequence, None)
                    if got else None
                )
                if result is None:
                    result = ProbeResult(
                        command.sequence,
                        command.tx_scale,
                        command.waveform,
                        command.mcs,
                        command.fec,
                        False,
                        wall_seconds=max(0.001, time.monotonic() - point_started),
                        error=(
                            "no calibration report"
                            if control_flushed
                            else "control probe did not leave the radio"
                        ),
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
            else:
                stop_reason = "complete"
            reason = (
                "cancelled"
                if self._calibration_cancel.is_set()
                else "time limit"
                if time.monotonic() >= deadline
                else stop_reason
            )
            status.total = status.progress
            report.finish(reason)
            paths = report.save()
            status.report_json, status.report_csv = map(str, paths)
            self._signal_calibration_direction_complete()
            return report
        finally:
            if windows_sweep:
                try:
                    gain.restore(snapshot)
                except Exception as exc:  # noqa: BLE001 - retain crash journal
                    status.error = dual(
                        f"Windows audio gain could not be restored: {exc}",
                        f"Hlasitost Windows se nepodařilo obnovit: {exc}",
                    )
                    self._log(
                        status.error,
                        LogLevel.ERROR,
                        source="station-lab",
                    )
                    raise
                else:
                    journal.clear()

    def _quick_calibration_plan(self) -> list[ProbeCommand]:
        from .ofdm.coding import FecProfile

        # CAL probes are SC-FTN only.  Keep the historical LDPC-1/2 numeric
        # value in the command so reports and wire tokens retain their IDs.
        return quick_plan(
            "sc_ftn",
            1,
            int(FecProfile.LDPC_1_2),
            self.config.g2_bandwidth,
        )

    def _calibration_profile_codec(self, command: ProbeCommand):
        from .waveforms.framing import ExperimentalBurstCodec

        if str(command.waveform).strip().lower() != "sc_ftn":
            raise ValueError("station calibration supports SC-FTN only")
        from .waveforms.config import profile_for

        return profile_for("sc_ftn", command.bandwidth), ExperimentalBurstCodec()

    def _build_calibration_waveform(
        self, command: ProbeCommand, total: int
    ) -> tuple[object, object, np.ndarray]:
        from .ofdm.framing import OfdmFrameType, PhyHeader

        profile, codec = self._calibration_profile_codec(command)
        size = min(512, int(profile.block_size))
        # Keep the payload bytes identical at each volume point so the sweep
        # compares the physical path rather than a different random PAPR sample.
        payload = np.random.default_rng(
            self.station_lab.session_id
        ).integers(0, 256, size, dtype=np.uint8).tobytes()
        header = PhyHeader(
            OfdmFrameType.DATA,
            self.station_lab.session_id,
            block_seq=command.sequence,
            block_count=total,
            mcs=command.mcs,
            fec=command.fec,
            payload_len=len(payload),
        )
        waveform = codec.build_burst(profile, header, payload)
        return profile, codec, np.asarray(waveform, dtype=np.float64)

    def _transmit_quick_calibration(self, plan: list[ProbeCommand]) -> int:
        """Transmit a bounded SC-FTN volume sweep after consent."""
        output = resolve_device(self.config.audio_output, "output")
        if not isinstance(output, int):
            raise RuntimeError("the configured calibration output is unavailable")
        completed = 0
        acquired = False
        try:
            self._suspend_control()
            acquired = True
            sd = _import_sounddevice()
            for index, command in enumerate(plan, 1):
                if self._calibration_cancel.is_set():
                    break
                profile, _codec, waveform = self._build_calibration_waveform(
                    command, len(plan)
                )
                sd.check_output_settings(
                    device=output, samplerate=profile.sample_rate, channels=1
                )
                self.station_lab.current = dual(
                    f"Burst {index}/{len(plan)}: {command.tx_scale * 100:.1f}%",
                    f"Dávka {index}/{len(plan)}: {command.tx_scale * 100:.1f}%",
                )
                scaled_waveform = waveform * command.tx_scale
                source_peak = float(np.max(np.abs(scaled_waveform)))
                source_clipped = int(np.count_nonzero(np.abs(scaled_waveform) >= 0.999))
                self._log(
                    f"Quick Tune TX {index}/{len(plan)}: SC-FTN "
                    f"MCS{command.mcs}, volume={command.tx_scale * 100:.1f}%, "
                    f"source_peak={source_peak:.3f}, "
                    f"source_clipped={source_clipped}",
                    source="station-lab",
                )
                transmit_waveform(
                    sd,
                    scaled_waveform,
                    device=output,
                    sample_rate=profile.sample_rate,
                    ptt=self._payload_ptt,
                    lead_seconds=max(
                        self.config.ofdm_tx_lead_ms / 1000.0,
                        PTT_LEAD_SECONDS,
                    ),
                    tail_seconds=max(
                        self.config.ofdm_tx_tail_ms / 1000.0,
                        PTT_TAIL_SECONDS,
                    ),
                )
                completed = index
                self.station_lab.progress = index
                if index < len(plan):
                    pause = 1.5 * self._quick_probe_seconds(profile, waveform)
                    if self._calibration_cancel.wait(pause):
                        break
            return completed
        finally:
            if acquired:
                self._resume_control()

    def _quick_probe_seconds(self, profile, waveform) -> float:
        return (
            len(waveform) / profile.sample_rate
            + TX_GUARD_SECONDS
            + max(self.config.ofdm_tx_lead_ms / 1000.0, PTT_LEAD_SECONDS)
            + max(self.config.ofdm_tx_tail_ms / 1000.0, PTT_TAIL_SECONDS)
            + 1.0
        )

    def _legacy_g2_calibration_key(
        self, waveform: str | None = None, bandwidth: str | None = None
    ) -> str:
        return "|".join(
            (
                str(waveform or self.config.g2_waveform).strip().lower(),
                str(bandwidth or self.config.g2_bandwidth).strip().upper(),
                f"policy-{int(self.config.g2_policy_version)}",
                str(self.config.radio).strip(),
                str(self.config.audio_output).strip(),
                str(self.config.vara_mode).strip().upper(),
            )
        )

    def _g2_calibration_key_v2(
        self, waveform: str | None = None, bandwidth: str | None = None
    ) -> str:
        """Pre host-API path key retained as an identity reference only."""
        c = self.config
        control_endpoint = (
            f"cat-{str(c.cat_port).strip().upper() or '-'}|"
            f"rigctld-{str(c.rigctld_host).strip().lower()}:{int(c.rigctld_port)}"
        )
        return "|".join(
            (
                str(waveform or c.g2_waveform).strip().lower(),
                str(bandwidth or c.g2_bandwidth).strip().upper(),
                f"policy-{int(c.g2_policy_version)}",
                "radio-path-v2",
                str(c.radio_backend).strip().lower(),
                str(c.radio).strip(),
                f"rig-{int(c.rig_model)}",
                control_endpoint,
                str(c.audio_output).strip(),
                str(c.vara_mode).strip().upper(),
            )
        )

    def _g2_calibration_key(
        self, waveform: str | None = None, bandwidth: str | None = None
    ) -> str:
        """Readable identity for the current local SC-FTN TX path."""
        c = self.config
        control_endpoint = (
            f"cat-{str(c.cat_port).strip().upper() or '-'}|"
            f"rigctld-{str(c.rigctld_host).strip().lower()}:{int(c.rigctld_port)}"
        )
        return "|".join(
            (
                str(waveform or c.g2_waveform).strip().lower(),
                str(bandwidth or c.g2_bandwidth).strip().upper(),
                f"policy-{int(c.g2_policy_version)}",
                "radio-path-v3",
                str(c.radio_backend).strip().lower(),
                str(c.radio).strip(),
                f"rig-{int(c.rig_model)}",
                control_endpoint,
                str(c.audio_output).strip(),
                f"hostapi-{audio_device_host_api(c.audio_output, 'output').casefold()}",
                str(c.vara_mode).strip().upper(),
            )
        )

    def _save_g2_calibration_record(
        self, waveform: str, bandwidth: str, scale: float, *, confidence: str
    ) -> None:
        c = self.config
        waveform = str(waveform).strip().lower()
        bandwidth = str(bandwidth).strip().upper()
        if waveform != "sc_ftn":
            raise ValueError("station calibration supports SC-FTN only")
        c.g2_tx_calibrations[self._g2_calibration_key(waveform, bandwidth)] = {
            "tx_scale": min(G2_MAX_TX_SCALE, max(0.001, float(scale))),
            "waveform": waveform,
            "bandwidth": bandwidth,
            "radio": c.radio,
            "radio_backend": c.radio_backend,
            "rig_model": c.rig_model,
            "control_endpoint": (
                f"cat-{str(c.cat_port).strip().upper() or '-'}|"
                f"rigctld-{str(c.rigctld_host).strip().lower()}:{int(c.rigctld_port)}"
            ),
            "audio_output": c.audio_output,
            "audio_host_api": audio_device_host_api(c.audio_output, "output"),
            "mode": c.vara_mode,
            "policy_version": c.g2_policy_version,
            "confidence": confidence,
            "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def _current_g2_tx_scale(self) -> float:
        """Use only an exact-path calibration; old keys never cross hardware."""
        c = self.config
        record = c.g2_tx_calibrations.get(self._g2_calibration_key())
        if isinstance(record, dict):
            try:
                return min(G2_MAX_TX_SCALE, max(0.001, float(record["tx_scale"])))
            except (KeyError, TypeError, ValueError):
                pass
        # The profile value is an operator setting/default, so it is a usable
        # uncalibrated drive level.  Do not migrate v1/v2 records into the
        # radio-path-v3 key: a changed CAT port or host API is a new TX path.
        try:
            return min(
                G2_MAX_TX_SCALE,
                max(0.001, float(c.g2_tx_scales.get("sc_ftn", 1.0))),
            )
        except (AttributeError, TypeError, ValueError):
            return 1.0

    def _apply_quick_station_level(
        self, waveform: str, bandwidth: str, scale: float
    ) -> None:
        if str(waveform).strip().lower() != "sc_ftn":
            raise ValueError("station calibration supports SC-FTN only")
        value = min(G2_MAX_TX_SCALE, max(0.001, float(scale)))
        self.config.g2_tx_scales["sc_ftn"] = value
        self._save_g2_calibration_record(
            "sc_ftn", bandwidth, value, confidence="paired-repeat-sweep"
        )
        payload = self.net.payload
        backend = (
            payload.backends.get("ofdm_vhf")
            if isinstance(payload, NegotiatedPayload)
            else payload
        )
        if backend is not None and hasattr(backend, "tx_scale"):
            backend.tx_scale = value
        self.config.save()

    def _run_quick_station_calibration(
        self, report: CalibrationReport
    ) -> CalibrationReport:
        status = self.station_lab
        plan = self._quick_calibration_plan()
        status.total = len(plan)
        status.progress = 0
        self._calibration_commands = {item.sequence: item for item in plan}
        self._calibration_results.clear()
        self._calibration_report_ready.clear()
        status.state = CalibrationState.MEASURING.value
        self.net.send_calibration_frame(
            FrameType.CAL_PROBE,
            status.peer,
            status.session_id,
            plan[0].encode(),
        )
        control_flushed = (
            bool(self.audio_transport.wait_tx_idle(timeout=8.0))
            if self.audio_transport is not None else True
        )
        if control_flushed:
            time.sleep(CALIBRATION_PROBE_GUARD_SECONDS)
            completed = self._transmit_quick_calibration(plan)
        else:
            completed = 0
        status.progress = completed
        status.state = CalibrationState.WAITING_REPORT.value
        status.current = dual(
            "Waiting for the peer's selected level",
            "Čekám na vybranou úroveň protistanice",
        )
        got = False
        if completed == len(plan):
            for attempt in range(CALIBRATION_REPORT_ATTEMPTS):
                got = bool(self._calibration_report_ready.wait(
                    CALIBRATION_REPORT_TIMEOUT
                ))
                if got or self._calibration_cancel.is_set():
                    break
                if attempt + 1 < CALIBRATION_REPORT_ATTEMPTS:
                    self._log(
                        "Quick Tune report missing; requesting the cached result "
                        f"again ({attempt + 2}/{CALIBRATION_REPORT_ATTEMPTS})",
                        LogLevel.WARNING,
                        source="station-lab",
                    )
                    self.net.send_calibration_frame(
                        FrameType.CAL_PROBE,
                        status.peer,
                        status.session_id,
                        plan[0].encode(),
                    )
        selected = next(iter(self._calibration_results.values()), None) if got else None
        for command in plan:
            if selected is not None and command.tx_scale == selected.tx_scale:
                candidate = replace(selected, sequence=command.sequence)
                candidate.payload_bytes = 512
                report.results.append(candidate)
            else:
                report.results.append(ProbeResult(
                    command.sequence,
                    command.tx_scale,
                    command.waveform,
                    command.mcs,
                    command.fec,
                    False,
                    error="tested; peer selected another level",
                    bandwidth=command.bandwidth,
                ))
        report.finish(
            "complete" if selected is not None
            else "peer did not return a sweep result"
        )
        if report.recommendation is not None:
            self._apply_quick_station_level(
                report.recommendation.waveform,
                report.recommendation.bandwidth,
                report.recommendation.tx_scale,
            )
            report.applied = True
            status.message = dual(
                f"Quick Tune selected and saved {report.recommendation.tx_scale * 100:.0f}%.",
                f"Quick Tune vybral a uložil {report.recommendation.tx_scale * 100:.0f}%.",
            )
            self._log(
                f"Quick Tune selected {report.recommendation.tx_scale * 100:.0f}% "
                f"for SC-FTN MCS{report.recommendation.mcs}",
                source="station-lab",
            )
        paths = report.save()
        status.report_json, status.report_csv = map(str, paths)
        if selected is None:
            self.net.send_calibration_frame(
                FrameType.CAL_CANCEL,
                status.peer,
                status.session_id,
                "NO_REPORT",
            )
            raise RuntimeError(
                "the peer did not return a Quick Tune result; "
                "calibration direction was not changed"
            )
        self._signal_calibration_direction_complete()
        return report

    def _signal_calibration_direction_complete(self) -> None:
        status = self.station_lab
        status.state = CalibrationState.PREPARING.value
        if self._calibration_initiator:
            self._calibration_turn_pending = True
            self._calibration_turn_attempts = 0
            self._send_calibration_turn()
        else:
            self._calibration_done_pending = True
            self._calibration_done_attempts = 0
            self._send_calibration_done()

    def _send_calibration_turn(self) -> None:
        status = self.station_lab
        self.net.send_calibration_frame(
            FrameType.CAL_DONE,
            status.peer,
            status.session_id,
            "TURN",
        )
        self._calibration_turn_attempts += 1
        self._calibration_turn_sent_at = time.monotonic()

    def _send_calibration_done(self) -> None:
        status = self.station_lab
        self.net.send_calibration_frame(
            FrameType.CAL_DONE,
            status.peer,
            status.session_id,
            "DONE",
        )
        self._calibration_done_attempts += 1
        self._calibration_done_sent_at = time.monotonic()

    def _receive_quick_calibration(
        self, first: ProbeCommand
    ) -> QuickSweepMeasurement:
        """Receive the complete repeated SC-FTN sweep and cache one report."""
        from .ofdm.bench import write_wav
        from .payload.ofdm_vhf import RadioAudioPipe

        plan = quick_plan(first.waveform, first.mcs, first.fec, first.bandwidth)
        profile, codec = self._calibration_profile_codec(plan[0])
        pipe = RadioAudioPipe(
            profile,
            input_device=resolve_device(self.config.audio_input, "input"),
            output_device=resolve_device(self.config.audio_output, "output"),
            ptt=self._payload_ptt,
            codec=codec,
            tx_lead_ms=self.config.ofdm_tx_lead_ms,
            tx_tail_ms=self.config.ofdm_tx_tail_ms,
        )
        started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        report = CalibrationReport(
            self.station_lab.session_id,
            self.station_lab.peer,
            CalibrationMode.QUICK.value,
            "inbound",
            started_utc,
            radio=self.config.radio,
            audio_input=self.config.audio_input,
            audio_output=self.config.audio_output,
            frequency_hz=self.current_frequency(),
        )
        expected = 8.0
        longest_slot = 0.0
        for command in plan:
            candidate_profile, _candidate_codec, candidate = (
                self._build_calibration_waveform(command, len(plan))
            )
            slot = 2.5 * self._quick_probe_seconds(candidate_profile, candidate)
            expected += slot
            longest_slot = max(longest_slot, slot)
        deadline = time.monotonic() + expected
        observed: dict[int, ProbeResult] = {}
        last_activity = time.monotonic()
        acquired = False
        try:
            self._suspend_control()
            acquired = True
            time.sleep(CALIBRATION_RX_SETTLE_SECONDS)
            pipe.start()
            while time.monotonic() < deadline and not self._calibration_cancel.is_set():
                remaining = deadline - time.monotonic()
                samples = pipe.receive(timeout=min(3.0, remaining))
                if samples is None:
                    if (
                        observed
                        and max(observed) >= len(plan) - 3
                        and time.monotonic() - last_activity
                        >= max(CALIBRATION_SWEEP_QUIET_SECONDS, 2 * longest_slot)
                    ):
                        break
                    continue
                last_activity = time.monotonic()
                decoded = codec.decode_burst(profile, samples)
                header = decoded.header
                if (
                    header is None
                    or header.msg_id != self.station_lab.session_id
                    or not 0 <= header.block_seq < len(plan)
                ):
                    self._log(
                        "Quick Tune RX ignored a key-up transient or unreadable capture",
                        source="station-lab",
                    )
                    continue
                command = plan[header.block_seq]
                metrics = decoded.metrics
                samples_array = np.asarray(samples)
                peak = float(np.max(np.abs(samples_array))) if len(samples_array) else 0.0
                rms = float(np.sqrt(np.mean(samples_array ** 2))) if len(samples_array) else 0.0
                _source_profile, _source_codec, source_waveform = (
                    self._build_calibration_waveform(command, len(plan))
                )
                source_clipped = int(np.count_nonzero(
                    np.abs(source_waveform * command.tx_scale) >= 0.999
                ))
                clipped = max(
                    int(np.count_nonzero(np.abs(samples_array) >= 0.999)),
                    source_clipped,
                )
                raw_path = write_wav(
                    config_dir() / "station-lab" / "captures" / (
                        f"cal-{self.station_lab.session_id:08x}-{command.sequence:03d}-"
                        f"sc_ftn-{command.bandwidth}-mcs{command.mcs}.wav"
                    ),
                    samples_array,
                    profile.sample_rate,
                )
                result = ProbeResult(
                    command.sequence,
                    command.tx_scale,
                    command.waveform,
                    command.mcs,
                    command.fec,
                    bool(decoded.ok),
                    header_ok=True,
                    snr_db=metrics.residual_snr_db or metrics.snr_db,
                    evm_rms=metrics.evm_rms,
                    audio_peak=(
                        metrics.audio_rms * 10 ** ((metrics.crest_factor_db or 0) / 20)
                        if metrics.audio_rms is not None else peak
                    ),
                    audio_rms=metrics.audio_rms if metrics.audio_rms is not None else rms,
                    clipped_samples=clipped,
                    flat_top=clipped >= 3,
                    sync_confidence=metrics.sync_confidence,
                    cfo_hz=metrics.cfo_hz,
                    payload_bytes=header.payload_len,
                    wall_seconds=len(samples_array) / profile.sample_rate,
                    error=metrics.error or "",
                    bandwidth=command.bandwidth,
                    capture_path=str(raw_path),
                )
                observed[command.sequence] = result
                if command.sequence == len(plan) - 1:
                    break
                self.station_lab.progress = max(
                    self.station_lab.progress, command.sequence + 1
                )
                self.station_lab.current = dual(
                    f"Measured burst {command.sequence + 1}/{len(plan)}: "
                    f"{command.tx_scale * 100:.1f}%",
                    f"Změřena dávka {command.sequence + 1}/{len(plan)}: "
                    f"{command.tx_scale * 100:.1f}%",
                )
            for command in plan:
                report.results.append(observed.get(command.sequence) or ProbeResult(
                    command.sequence,
                    command.tx_scale,
                    command.waveform,
                    command.mcs,
                    command.fec,
                    False,
                    error="measuring burst was not decoded",
                    bandwidth=command.bandwidth,
                ))
            report.finish("complete")
            report.save()
            selected = (
                next(
                    item for item in report.results
                    if report.recommendation is not None
                    and item.tx_scale == report.recommendation.tx_scale
                ) if report.recommendation is not None else max(
                    report.results,
                    key=lambda item: (
                        item.frame_ok,
                        item.header_ok,
                        item.sync_confidence or 0.0,
                        -(item.evm_rms if item.evm_rms is not None else 999.0),
                    ),
                )
            )
            return QuickSweepMeasurement(report, selected)
        finally:
            pipe.stop()
            if acquired:
                self._resume_control()

    def _receive_calibration_probe(self, command: ProbeCommand) -> ProbeResult:
        """Receive one SC-FTN burst for Full AutoTune."""
        from .payload.ofdm_vhf import RadioAudioPipe
        from .waveforms.framing import ExperimentalBurstCodec

        profile, codec = self._calibration_profile_codec(command)
        pipe = RadioAudioPipe(
            profile,
            input_device=resolve_device(self.config.audio_input, "input"),
            output_device=resolve_device(self.config.audio_output, "output"),
            ptt=self._payload_ptt,
            codec=codec,
            tx_lead_ms=self.config.ofdm_tx_lead_ms,
            tx_tail_ms=self.config.ofdm_tx_tail_ms,
        )
        acquired = False
        started = time.monotonic()
        try:
            self._suspend_control()
            acquired = True
            time.sleep(CALIBRATION_RX_SETTLE_SECONDS)
            pipe.start()
            samples = pipe.receive(timeout=12.0)
            if samples is None:
                return ProbeResult(
                    command.sequence,
                    command.tx_scale,
                    command.waveform,
                    command.mcs,
                    command.fec,
                    False,
                    wall_seconds=time.monotonic() - started,
                    error="no measuring burst heard",
                    bandwidth=command.bandwidth,
                )
            samples_array = np.asarray(samples)
            decoded = codec.decode_burst(profile, samples_array)
            from .ofdm.bench import write_wav

            capture_root = config_dir() / "station-lab" / "captures"
            capture_root.mkdir(parents=True, exist_ok=True)
            raw_path = write_wav(
                capture_root / (
                    f"cal-{self.station_lab.session_id:08x}-{command.sequence:03d}-"
                    f"sc_ftn-{command.bandwidth}-mcs{command.mcs}.wav"
                ),
                samples_array,
                profile.sample_rate,
            )
            metrics = decoded.metrics
            peak = float(np.max(np.abs(samples_array))) if len(samples_array) else 0.0
            rms = float(np.sqrt(np.mean(samples_array ** 2))) if len(samples_array) else 0.0
            clipped = int(np.count_nonzero(np.abs(samples_array) >= 0.999))
            header = decoded.header
            return ProbeResult(
                command.sequence,
                command.tx_scale,
                command.waveform,
                command.mcs,
                command.fec,
                bool(decoded.ok),
                header_ok=header is not None,
                snr_db=metrics.residual_snr_db or metrics.snr_db,
                evm_rms=metrics.evm_rms,
                audio_peak=(
                    metrics.audio_rms * 10 ** ((metrics.crest_factor_db or 0) / 20)
                    if metrics.audio_rms is not None else peak
                ),
                audio_rms=metrics.audio_rms if metrics.audio_rms is not None else rms,
                clipped_samples=clipped,
                flat_top=clipped >= 3,
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
        if isinstance(task.value, QuickSweepMeasurement):
            if (
                self._calibration_cancel.is_set()
                or status.state == CalibrationState.CANCELLED.value
            ):
                return
            measurement = task.value
            self._calibration_rx_completed = True
            self._calibration_last_quick_measurement = measurement
            time.sleep(CALIBRATION_SWEEP_REPORT_GUARD)
            self.net.send_calibration_frame(
                FrameType.CAL_REPORT,
                status.peer,
                status.session_id,
                measurement.selected.encode_report(),
            )
            status.progress = QUICK_TUNE_BURSTS
            status.state = CalibrationState.PREPARING.value
            return
        if task.error is not None:
            command = self._calibration_last_rx_command or ProbeCommand(
                0, "sc_ftn", 1, 1, 0.05, self.config.g2_bandwidth
            )
            result = ProbeResult(
                command.sequence,
                command.tx_scale,
                command.waveform,
                command.mcs,
                command.fec,
                False,
                error=str(task.error),
                bandwidth=command.bandwidth,
            )
        else:
            result = task.value
        if not isinstance(result, ProbeResult):
            return
        self.net.send_calibration_frame(
            FrameType.CAL_REPORT,
            status.peer,
            status.session_id,
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
                "Station calibration failed.",
                "Kalibrace stanice selhala.",
            )
            return
        status.report = (
            task.value if isinstance(task.value, CalibrationReport) else None
        )
        if self._calibration_cancel.is_set():
            status.state = CalibrationState.CANCELLED.value
        elif self._calibration_initiator:
            status.state = CalibrationState.PREPARING.value
            status.message = dual(
                "This direction is complete; the peer is measuring the reverse direction.",
                "Tento směr je hotový; protistanice měří opačný směr.",
            )
            return
        elif self._calibration_done_pending:
            status.state = CalibrationState.PREPARING.value
            status.message = dual(
                "Both directions are measured; waiting for completion confirmation.",
                "Oba směry jsou změřeny; čekám na potvrzení dokončení.",
            )
            return
        else:
            status.state = CalibrationState.COMPLETE.value
        if status.report is not None and status.report.mode == CalibrationMode.QUICK.value:
            status.message = dual(
                "Quick Tune complete. The selected Guardian volume was saved automatically.",
                "Quick Tune je dokončen. Vybraná hlasitost Guardianu byla automaticky uložena.",
            )
        else:
            status.message = dual(
                "Measurement complete. Review the report before applying it.",
                "Měření dokončeno. Před použitím zkontrolujte report.",
            )

    def _make_payload_backend(self):
        """Build the next-session payload backend from the current settings."""
        deps = self._payload_dependencies()
        primary = make_backend(self.config.payload_backend, **deps)
        if self.config.payload_backend == "vara_p2p":
            return primary
        # SC-FTN is negotiated per hop.  A peer that only has the established
        # VARA path therefore falls back to the same VARA backend used by G1.
        return NegotiatedPayload(
            default=make_backend("vara_p2p", **deps),
            backends={primary.name: primary},
            on_log=lambda value: self._log(value, source="payload"),
        )

    def _payload_dependencies(self) -> dict:
        """Dependencies shared by VARA and the opt-in SC-FTN backend."""
        c = self.config
        return dict(
            vara=self.vara,
            on_log=lambda value: self._log(value, source="payload"),
            on_qsy=self._payload_send_qsy,
            on_receive_qsy=(
                self._payload_receive_qsy
                if c.separate_working_channels else None
            ),
            on_unqsy=(
                self._payload_restore_calling
                if c.separate_working_channels else None
            ),
            on_acquire=self._suspend_control,
            on_release=self._resume_control,
            on_handoff_failed=self._payload_handoff_failed,
            audio_input=c.audio_input,
            audio_output=c.audio_output,
            ptt=self._payload_ptt,
            ptt_turnaround_ms=self._payload_ptt_delay_ms,
            # Retain all established OFDM field names for the backend factory;
            # the production factory currently consumes the SC values below.
            ofdm_profile=c.ofdm_profile,
            ofdm_mcs=c.ofdm_mcs,
            ofdm_tx_lead_ms=c.ofdm_tx_lead_ms,
            ofdm_tx_tail_ms=c.ofdm_tx_tail_ms,
            ofdm_max_retries=c.ofdm_max_retries,
            ofdm_adaptive_fec=c.ofdm_adaptive_fec,
            ofdm_modern_ldpc=c.ofdm_modern_ldpc,
            ofdm_fec=c.ofdm_fec,
            ofdm_adaptive_burst=c.ofdm_adaptive_burst,
            ofdm_burst_bytes=c.ofdm_burst_bytes,
            ofdm_min_burst_bytes=c.ofdm_min_burst_bytes,
            ofdm_max_burst_bytes=c.ofdm_max_burst_bytes,
            ofdm_arq_block_bytes=c.ofdm_arq_block_bytes,
            ofdm_timeout_multiplier=c.ofdm_timeout_multiplier,
            ofdm_legacy_mode=c.ofdm_legacy_mode,
            ofdm_train_bursts=c.ofdm_train_bursts,
            ofdm_adaptive_train=c.ofdm_adaptive_train,
            ofdm_superframe=c.ofdm_superframe,
            ofdm_train_gap_ms=c.ofdm_train_gap_ms,
            ofdm_max_train_seconds=c.ofdm_max_train_seconds,
            g2_waveform=c.g2_waveform,
            g2_bandwidth=c.g2_bandwidth,
            g2_mcs=c.g2_mcs,
            g2_adaptive_mcs=c.g2_adaptive_mcs,
            g2_tx_scale=self._current_g2_tx_scale(),
            # Hardware identity is part of the calibrated TX path.  Passing
            # these explicitly prevents a backend from silently applying a
            # scale measured through a different radio or control route.
            radio_backend=c.radio_backend,
            radio_model=c.radio,
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
                self._log(
                    dual("Radio disconnected.", "Rádio odpojeno."),
                    source="radio",
                    kind=LogEventKind.CONNECTION_LOST,
                )
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
                self._log(
                    dual("VARA disconnected.", "VARA odpojena."),
                    source="vara",
                    kind=LogEventKind.CONNECTION_LOST,
                )
            self._update_vara_snapshot()

        return self.workers.submit(
            "vara-control",
            self.vara.disconnect,
            completed,
        )

    def start_control_channel(self) -> bool:
        if self._closing.is_set():
            return False
        if self.audio_transport is not None:
            return True
        if self.payload_handoff_pending():
            self._log(
                dual(
                    "Control audio will restart after the VARA radio handoff is quiet.",
                    "Řídicí zvuk se restartuje po ztichnutí předávaného rádia VARA.",
                ),
                LogLevel.WARNING,
                source="payload",
            )
            return False
        if self._active_session_count():
            self._log(
                dual(
                    "Control audio will start after the active session ends.",
                    "Řídicí zvuk se spustí po skončení aktivní relace.",
                ),
                LogLevel.WARNING,
                source="session",
            )
            return False
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
        if self._calibration_active():
            self._log(
                dual(
                    "Audio settings were not changed during station calibration.",
                    "Nastavení zvuku nebylo změněno během kalibrace stanice.",
                ),
                LogLevel.WARNING,
                source="station-lab",
            )
            return False
        # The control transport is also the shared owner of the soundcard while
        # a payload session is negotiating. ``_payload_active`` is set only
        # after that handoff has acquired the device, so inspect both markers
        # before stopping the transport. Replacing it here would leave the
        # payload worker with an audio owner that Operations no longer tracks.
        if self._payload_active.is_set() or self._active_session_count():
            self._log(
                dual(
                    "Audio settings were not changed while a session is active.",
                    "Nastavení zvuku nebylo změněno během aktivní relace.",
                ),
                LogLevel.WARNING,
                source="session",
            )
            return False
        if self.payload_handoff_pending():
            self._log(
                dual(
                    "Audio settings were not changed while radio handoff is active.",
                    "Nastavení zvuku nebylo změněno během předávání rádia.",
                ),
                LogLevel.WARNING,
                source="payload",
            )
            return False
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
        if self._calibration_active():
            self.cancel_station_calibration()
        self.stop_scanner(restore=True)
        # Detach the transport before stopping payload work.  A worker that is
        # unwinding after cancellation must not find the old control object and
        # reopen it through its release callback.
        transport, self.audio_transport = self.audio_transport, None
        if transport is not None:
            try:
                transport.stop()
            except Exception as exc:  # noqa: BLE001 - continue teardown
                self._log(
                    dual(
                        f"Audio control channel stop failed: {exc}",
                        f"Zastavení zvukového řídicího kanálu selhalo: {exc}",
                    ),
                    LogLevel.WARNING,
                    source="control",
                )
        # Stopping control is a station-wide stop.  Cancel every live session
        # before replacing the orchestrator so an old SC/VARA worker cannot keep
        # keying a radio that the new net no longer tracks.
        old_net = self.net
        for message in tuple(getattr(old_net, "sessions", {}).values()):
            if not message.state.terminal:
                try:
                    old_net.cancel(message.msg_id, notify=False)
                except Exception as exc:  # noqa: BLE001 - keep tearing down
                    self._log(
                        dual(
                            f"Payload cancellation failed for #{message.msg_id}: {exc}",
                            f"Zrušení datového přenosu #{message.msg_id} selhalo: {exc}",
                        ),
                        LogLevel.WARNING,
                        source="payload",
                    )
        payload = getattr(old_net, "payload", None)
        shutdown = getattr(payload, "shutdown", None)
        if not callable(shutdown):
            shutdown = getattr(payload, "close", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception as exc:  # noqa: BLE001 - teardown is best effort
                self._log(
                    dual(
                        f"Payload shutdown failed: {exc}",
                        f"Ukončení datového modemu selhalo: {exc}",
                    ),
                    LogLevel.WARNING,
                    source="payload",
                )
        self.config.control_channel = "off"
        self.config.save()
        if self._closing.is_set():
            self._update_network_snapshot()
            return
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
        # The status transition is the hand-off point with mailbox deletion.
        # Keep route/QSY checks and the transition together so a worker cannot
        # remove the bundle between the final read and marking it in flight.
        if not self._mail_mutation_lock.acquire(blocking=False):
            self._log(
                dual(
                    "Message remains queued: mailbox maintenance is in progress.",
                    "Zpráva zůstává ve frontě: probíhá údržba schránky.",
                ),
                LogLevel.WARNING,
                source="mail",
            )
            return False
        try:
            if message_id in self._mail_preparing:
                self._log(
                    dual(
                        f"Message #{message_id} is already being prepared for transmission.",
                        f"Zpráva #{message_id} se již připravuje k přenosu.",
                    ),
                    LogLevel.WARNING,
                    source="mail",
                )
                return False
            active = self.net.sessions.get(message_id)
            if active is not None and not active.state.terminal:
                self._log(
                    dual(
                        f"Message #{message_id} is already in a transfer.",
                        f"Zpráva #{message_id} je již v přenosu.",
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
            # This covers both the compression worker and the short window
            # before a normal bundle is announced as a network session.
            self._mail_preparing.add(message_id)
        finally:
            self._mail_mutation_lock.release()
        aggressive = bool(getattr(self.config, "guardian_aggressive_compression", False))
        guardian_bzip2 = bool(self.config.guardian_compression)
        native_vara = bool(self.config.vara_file_compression)
        enabled_count = sum((aggressive, guardian_bzip2, native_vara))
        compression_job = None
        compression_label = ""
        if enabled_count == 1 and aggressive:
            compression_job = lambda: mail.to_aggressive_bundle(baseline)
            compression_label = "Guardian XZ/LZMA2 + image optimization"
        elif enabled_count == 1 and guardian_bzip2:
            compression_job = lambda: mail.to_guardian_bundle(baseline)
            compression_label = "Guardian BZIP2"
        if compression_job is not None:
            task_name = f"mail-compress-{mail.msg_id}"

            def compressed(result: TaskResult) -> None:
                # Do not let deletion begin between releasing the worker's
                # active marker and announcing the session.
                with self._mail_mutation_lock:
                    try:
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
                            if encoding.method != "standard" else Flags.NONE
                        )
                        detail = (
                            f" Details: {'; '.join(encoding.details)}."
                            if getattr(encoding, "details", ()) else ""
                        )
                        self._log(
                            dual(
                                f"{compression_label} produced {encoding.method} for "
                                f"message #{mail.msg_id}: {encoding.baseline_size} → "
                                f"{len(encoding.data)} B ({encoding.saved_percent:.1f}% saved).{detail}",
                                f"Komprese {compression_label} vytvořila {encoding.method} pro "
                                f"zprávu #{mail.msg_id}: {encoding.baseline_size} → "
                                f"{len(encoding.data)} B (úspora {encoding.saved_percent:.1f} %).{detail}",
                            ),
                            source="payload",
                        )
                        self._announce_prepared_mail(mail, encoding.data, flags=flags)
                    finally:
                        self._mail_preparing.discard(mail.msg_id)

            try:
                queued = self.workers.submit(
                    task_name,
                    compression_job,
                    compressed,
                )
            except Exception:
                with self._mail_mutation_lock:
                    self._mail_preparing.discard(message_id)
                    self.mailstore.set_status(message_id, status=Status.QUEUED)
                raise
            if not queued:
                with self._mail_mutation_lock:
                    self._mail_preparing.discard(message_id)
                    self.mailstore.set_status(message_id, status=Status.QUEUED)
                return False
            self._log(
                dual(
                    f"Compressing message #{mail.msg_id} with {compression_label}…",
                    f"Komprimuji zprávu #{mail.msg_id} pomocí {compression_label}…",
                ),
                source="payload",
            )
            return True
        if enabled_count > 1:
            self._log(
                dual(
                    "Compression modes were not stacked; sending the standard ZIP bundle.",
                    "Režimy komprese nebyly vrstveny; odesílám standardní ZIP balíček.",
                ),
                LogLevel.WARNING,
                source="payload",
            )
        with self._mail_mutation_lock:
            try:
                self._announce_prepared_mail(mail, baseline)
            finally:
                self._mail_preparing.discard(message_id)
        return True

    def tick(self) -> None:
        now = time.monotonic()
        if self.audio_transport is not None:
            self.audio_transport.pump()
        self._tick_payload_handoff(now)
        self.net.tick(now, control_available=not self._payload_active.is_set())
        self._tick_station_calibration(now)
        self._update_station_lab_snapshot()
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

    def _control_tx_idle(self, transport=None) -> bool:
        """Return whether the live control transport has no TX in flight.

        ``AudioControlTransport`` exposes a zero-timeout wait for this exact
        hand-off.  The small fallbacks keep dry-run transports used by tests and
        integrations compatible while still refusing a transport that reports
        pending work.  This method never waits for a frame to finish.
        """
        transport = self.audio_transport if transport is None else transport
        if transport is None:
            return False
        pending = getattr(transport, "_pending_tx", None)
        if pending is not None:
            try:
                if int(pending) > 0:
                    return False
            except (TypeError, ValueError):
                return False
        wait = getattr(transport, "wait_tx_idle", None)
        if not callable(wait):
            return True
        try:
            return bool(wait(timeout=0.0))
        except TypeError:
            # A few lightweight transports accept the timeout positionally.
            try:
                return bool(wait(0.0))
            except TypeError:
                # A no-argument wait may use a long default timeout, so refuse
                # it rather than blocking the UI while checking this gate.
                return False
            except Exception:  # noqa: BLE001 - fail closed for a gate
                return False
        except Exception:  # noqa: BLE001 - fail closed for a gate
            return False

    def _beacon_block_reason(
        self,
        *,
        now: float | None = None,
        manual: bool = False,
    ) -> str | None:
        """Explain why a beacon cannot be queued at this instant.

        Automatic beacons call this as a silent gate from the periodic tick;
        manual callers receive the same reason in the operational log and UI.
        Keeping the checks in one place prevents the manual action from cutting
        across a VARA hand-off or an alert frequency sweep.
        """
        if self.audio_transport is None:
            return dual(
                "Start control channel before sending a beacon.",
                "Před odesláním majáku spusťte řídicí kanál.",
            )
        if self._calibration_active():
            return dual(
                "Beacon not queued: station calibration is active.",
                "Maják nezařazen: probíhá kalibrace stanice.",
            )
        if self.payload_handoff_pending():
            return dual(
                "Beacon not queued: VARA radio handoff is still waiting for RF quiet.",
                "Maják nezařazen: předání rádia VARA čeká na ztichnutí vysílání.",
            )
        if self._payload_active.is_set() or bool(
            getattr(getattr(self.vara, "state", None), "ptt", False)
        ):
            return dual(
                "Beacon not queued: a VARA payload transfer is active.",
                "Maják nezařazen: probíhá datový přenos přes VARA.",
            )
        if self._active_session_count():
            return dual(
                "Beacon not queued: a network session is active.",
                "Maják nezařazen: probíhá síťová relace.",
            )
        if self.scanner is not None:
            return dual(
                "Beacon not queued: stop the channel scanner first.",
                "Maják nezařazen: nejprve zastavte scanner kanálů.",
            )
        if self.workers.is_active("alert-sweep"):
            return dual(
                "Beacon not queued: an alert frequency sweep is active.",
                "Maják nezařazen: probíhá přelaďování výstrahy.",
            )
        if self.workers.is_active("radio-control"):
            return dual(
                "Beacon not queued: radio control is busy.",
                "Maják nezařazen: řízení rádia je zaneprázdněné.",
            )
        pending_alerts = getattr(self.net, "alerts_pending", None)
        if callable(pending_alerts):
            try:
                if int(pending_alerts()) > 0:
                    return dual(
                        "Beacon not queued: control frames are already pending.",
                        "Maják nezařazen: řídicí rámce už čekají ve frontě.",
                    )
            except Exception:  # noqa: BLE001 - a broken status must fail closed
                return dual(
                    "Beacon not queued: control transmit state is unavailable.",
                    "Maják nezařazen: stav vysílací fronty řídicích rámců není dostupný.",
                )
        if not self._control_tx_idle():
            return dual(
                "Beacon not queued: a control burst is still on the air.",
                "Maják nezařazen: řídicí rámec se stále vysílá.",
            )
        if manual:
            now = time.monotonic() if now is None else now
            last = self._last_beacon_request
            # Keep compatibility with code that inspects or restores the
            # historical `_last_beacon` field directly.
            if last is None and self._last_beacon > 0.0:
                last = self._last_beacon
            if last is not None and now - last < self._BEACON_MIN_GAP:
                return dual(
                    "Beacon not queued: another beacon was just queued; wait a few seconds.",
                    "Maják nezařazen: další maják byl právě zařazen; chvíli počkejte.",
                )
        return None

    def beacon_block_reason(self) -> str | None:
        """Return the current manual-beacon status for the Network workspace."""
        return self._beacon_block_reason(manual=True)

    def _queue_beacon(self, now: float, *, manual: bool) -> bool:
        """Queue one beacon after the shared safety gate has passed.

        ``True`` means the control frame was handed to the transport queue. It
        says nothing about a peer hearing or acknowledging the beacon; BEACON
        frames have no reception acknowledgement.
        """
        reason: str | None
        # Queueing a control frame does not touch the radio. Keep this lock
        # separate from ``_radio_lock`` and acquire both without waiting so a
        # second click cannot freeze behind a CAT command already in flight.
        if not self._beacon_lock.acquire(blocking=False):
            reason = dual(
                "Beacon not queued: another beacon request is in progress.",
                "Maják nezařazen: právě se zpracovává jiný požadavek na maják.",
            )
            if manual:
                self._log(reason, LogLevel.WARNING, source="network")
            return False
        if not self._radio_lock.acquire(blocking=False):
            reason = dual(
                "Beacon not queued: radio control is busy.",
                "Maják nezařazen: řízení rádia je zaneprázdněné.",
            )
            self._beacon_lock.release()
            if manual:
                self._log(reason, LogLevel.WARNING, source="network")
            return False
        try:
            reason = self._beacon_block_reason(now=now, manual=manual)
            if reason is not None:
                if manual:
                    self._log(reason, LogLevel.WARNING, source="network")
                return False
            # Record the request before calling into the transport.  A broken
            # transport must not turn a click or a timer tick into a tight retry
            # loop, and a manual request must hold off the next auto tick.
            self._last_beacon = now
            self._last_beacon_request = now
            try:
                self.net.beacon()
            except Exception as exc:  # noqa: BLE001 - operation must stay alive
                self._log(
                    dual(f"Beacon failed: {exc}", f"Maják selhal: {exc}"),
                    LogLevel.WARNING,
                    source="network",
                )
                return False
        finally:
            self._radio_lock.release()
            self._beacon_lock.release()
        self._log(
            dual(
                "Presence beacon queued for transmission.",
                "Maják přítomnosti zařazen k vysílání.",
            ),
            source="network",
        )
        return True

    def send_beacon_now(self) -> bool:
        """Queue one presence beacon, regardless of automatic beacon settings."""
        return self._queue_beacon(time.monotonic(), manual=True)

    def _tick_beacon(self, now: float) -> None:
        """Announce presence so peers can hear this station and route to it."""
        if not self.config.beacon_enabled:
            return
        interval = max(self._BEACON_MIN_GAP, float(self.config.beacon_interval))
        if now - self._last_beacon < interval:
            return
        self._queue_beacon(now, manual=False)

    def _tick_auto_deliver(self, now: float) -> None:
        # Keep the mailbox selection and the send_queued transition together
        # with worker-side deletion. A busy worker simply lets the next tick
        # retry; no radio or UI thread waits on the mailbox lock.
        if not self._mail_mutation_lock.acquire(blocking=False):
            return
        try:
            self._tick_auto_deliver_locked(now)
        finally:
            self._mail_mutation_lock.release()

    def _tick_auto_deliver_locked(self, now: float) -> None:
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
                transfer_source=state.transfer_source,
                transfer_destination=state.transfer_destination,
                transfer_via=state.transfer_via,
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

    def _update_station_lab_snapshot(self) -> None:
        status = self.station_lab
        self.snapshots.update(
            station_lab=StationLabSnapshot(
                state=status.state,
                peer=status.peer,
                session_id=int(status.session_id),
                mode=status.mode,
                progress=max(0, int(status.progress)),
                total=max(0, int(status.total)),
                current=status.current,
                message=status.message,
                pending_offer=bool(status.pending_offer),
                report_json=status.report_json,
                report_csv=status.report_csv,
                error=status.error,
            )
        )

    def _update_payload_ptt_turnaround(
        self, payload_transport: str, delay_ms: int | float
    ) -> None:
        """Propagate a negotiated keying gap to the live SC backend.

        The payload wrapper is created when the control channel starts, while
        the peer's PTT delay arrives with the session handshake.  Updating only
        Operations' callback delay leaves the already-created OFDM backend's
        ARQ timeout budget at its default 250 ms.  Keep this update scoped to
        SC-FTN and tolerate a third-party backend that does not expose the
        optional setter.
        """
        if str(payload_transport or "").strip().lower() != "ofdm_vhf":
            return
        payload = getattr(self.net, "payload", None)
        candidates = [payload]
        backends = getattr(payload, "backends", None)
        if isinstance(backends, dict):
            candidates.extend(backends.values())
        default = getattr(payload, "default", None)
        if default is not None:
            candidates.append(default)
        seen: set[int] = set()
        for backend in candidates:
            if backend is None or id(backend) in seen:
                continue
            seen.add(id(backend))
            setter = getattr(backend, "set_ptt_turnaround_ms", None)
            if not callable(setter):
                continue
            try:
                setter(delay_ms)
            except Exception as exc:  # noqa: BLE001 - timing must not break state
                self._log(
                    dual(
                        f"Could not update SC-FTN PTT timing: {exc}",
                        f"Časování PTT SC-FTN nelze aktualizovat: {exc}",
                    ),
                    LogLevel.WARNING,
                    source="session",
                )

    def _session_event(self, message, event: str) -> None:
        kind = LogEventKind.GENERAL
        if message.state in (SessionState.STARTING_VARA, SessionState.RECEIVING):
            kind = LogEventKind.TRANSFER_STARTED
        elif message.state in (
            SessionState.CONFIRMED,
            SessionState.FORWARDED,
            SessionState.RECEIVED_OK,
            SessionState.DELIVERED,
        ):
            kind = LogEventKind.TRANSFER_COMPLETED
        elif message.state in (SessionState.FAILED, SessionState.CANCELLED):
            kind = LogEventKind.TRANSFER_FAILED
        self._log(
            f"[{message.source}#{message.msg_id}] {event}",
            source="session",
            kind=kind,
        )
        # Adopt the session's negotiated slow-keying gap for the VARA phase.
        # STARTING_VARA/RECEIVING are emitted before the payload backend takes
        # the codec, so the value is in place before VARA's first PTT ON.
        delay = int(getattr(message, "ptt_delay_ms", 0) or 0)
        payload_transport = getattr(message, "payload_transport", "vara_p2p")
        if message.state in (
            SessionState.STARTING_VARA,
            SessionState.TRANSFERRING,
            SessionState.RECEIVING,
        ):
            self._update_payload_ptt_turnaround(payload_transport, delay)
            if delay != self._payload_ptt_delay_ms:
                self._payload_ptt_delay_ms = delay
                if delay and payload_transport == "ofdm_vhf":
                    self._log(
                        dual(
                            f"Slow keying negotiated: PTT held {delay} ms "
                            "after each OFDM VHF burst.",
                            f"Vyjednáno pomalé klíčování: PTT drženo {delay} ms "
                            "po každé dávce OFDM VHF.",
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
            self._update_payload_ptt_turnaround(payload_transport, 0)
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
                self.mailstore.mark_sent(message.msg_id)
            elif message.state in (SessionState.CONFIRMED, SessionState.FORWARDED):
                self.mailstore.set_status(
                    message.msg_id,
                    status=Status.FORWARDED,
                    folder=Folder.SENT,
                )
                self.mailstore.mark_sent(message.msg_id)
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
        notification = text.strip().upper()
        if notification in {"PTT ON", "PTT OFF"}:
            kind = LogEventKind.VARA_PTT
        elif notification in {"BUSY ON", "BUSY OFF"}:
            kind = LogEventKind.VARA_BUSY
        else:
            kind = LogEventKind.GENERAL
        self._log(f"[VARA] {text}", source="vara", kind=kind)

    def _radio_ptt(self, enabled: bool) -> None:
        try:
            with self._radio_lock:
                self.radio.set_ptt(enabled)
        except Exception as exc:
            self._log(f"PTT error: {exc}", LogLevel.ERROR, source="radio")
            # A rejected keying command must reach the caller.  Native SC-FTN
            # audio and the control transmitter use this callback as their
            # failure boundary; swallowing the exception lets a failed PTT
            # look like a transmitted burst.  VaraClient still guards its
            # asynchronous notification hook, so this does not tear down its
            # reader thread.
            raise

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

    def payload_handoff_pending(self) -> bool:
        """Whether VARA still owns the radio while waiting for RF quiet."""
        with self._payload_handoff_lock:
            return self._payload_handoff_resume is not None

    def payload_handoff_error(self) -> str:
        """Return the latest visible handoff fault, if one is pending."""
        with self._payload_handoff_lock:
            return self._payload_handoff_fault

    def _payload_handoff_failed(self, resume: Callable[[], bool]) -> None:
        """Retain a VARA completion until its native RF tail is quiet.

        The VARA worker calls this hook after its bounded idle probe fails.  The
        continuation itself performs the second idle check and only then calls
        Operations' audio/QSY release hooks.  We schedule those checks from the
        normal worker pool, keeping the UI tick nonblocking and the retry rate
        bounded.
        """
        if not callable(resume):
            return
        if self._closing.is_set():
            return
        with self._payload_handoff_lock:
            if self._payload_handoff_resume is not None:
                return
            self._payload_handoff_resume = resume
            self._payload_handoff_retry_at = 0.0
            self._payload_handoff_fault = dual(
                "VARA is still transmitting; control audio will resume after the radio is quiet.",
                "VARA stále vysílá; řídicí zvuk se obnoví, až rádio ztichne.",
            )
        self._log(self.payload_handoff_error(), LogLevel.WARNING, source="payload")

    def _tick_payload_handoff(self, now: float) -> None:
        """Try one bounded native-idle probe and retain it on failure."""
        if self._closing.is_set():
            return
        with self._payload_handoff_lock:
            resume = self._payload_handoff_resume
            if (
                resume is None
                or self._payload_handoff_recovery_active
                or now < self._payload_handoff_retry_at
            ):
                return
            self._payload_handoff_recovery_active = True

        def operation() -> bool:
            return bool(resume())

        def completed(result: TaskResult) -> None:
            recovered = bool(result.error is None and result.value)
            with self._payload_handoff_lock:
                self._payload_handoff_recovery_active = False
                if recovered:
                    self._payload_handoff_resume = None
                    self._payload_handoff_fault = ""
                else:
                    self._payload_handoff_retry_at = (
                        time.monotonic() + 0.75
                    )
            if result.error is not None:
                self._log(
                    dual(
                        f"VARA radio handoff check failed: {result.error}",
                        f"Kontrola předání rádia VARA selhala: {result.error}",
                    ),
                    LogLevel.WARNING,
                    source="payload",
                )
            elif recovered:
                self._log(
                    dual(
                        "VARA radio handoff completed; control audio resumed.",
                        "Předání rádia VARA dokončeno; řídicí zvuk obnoven.",
                    ),
                    source="payload",
                )

        if not self.workers.submit("payload-handoff", operation, completed):
            with self._payload_handoff_lock:
                self._payload_handoff_recovery_active = False
                self._payload_handoff_retry_at = now + 0.75

    def _payload_ptt(self, enabled: bool) -> None:
        """PTT callback for a payload backend that generates its own audio."""
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
        # This diagnostic applies only to the native VARA payload.  SC-FTN
        # keys through Guardian's own payload callback, so a false VARA host
        # PTT preference must not warn during an SC session.
        if self.config.payload_backend != "vara_p2p":
            return False
        if self.config.vara_host_ptt:
            return False
        if self.config.radio_backend not in {
            "hamlib", "guardian_k5", "guardian_k61"
        } or not self.config.cat_port:
            return False
        owner = (
            "rigctld"
            if self.config.radio_backend == "hamlib"
            else "the Guardian UART driver"
        )
        owner_cs = (
            "rigctld"
            if self.config.radio_backend == "hamlib"
            else "ovladač Guardian UART"
        )
        self._log(
            dual(
                f"Guardian is not keying the radio for VARA and {owner} holds "
                f"{self.config.cat_port}, so VARA has no port left to key "
                "through. If the radio stays in receive, enable 'Let Guardian "
                "key the radio for VARA'.",
                f"Guardian pro VARA neklíčuje a {owner_cs} drží "
                f"{self.config.cat_port}, takže VARA nemá čím klíčovat. Pokud "
                "rádio zůstane na příjmu, zapněte „Guardian klíčuje rádio pro "
                "VARA“.",
            ),
            LogLevel.WARNING,
            source="vara",
        )
        return True

    def _suspend_control(self) -> None:
        if self._closing.is_set():
            raise RuntimeError("Operations is closing")
        self._warn_if_nothing_can_key_vara()
        self._payload_active.set()
        try:
            # Wait for a radio poll already in progress before VARA begins.
            # Future polls remain suppressed until the codec is returned.
            with self._radio_lock:
                pass
            if self.audio_transport is not None:
                suspend = getattr(self.audio_transport, "suspend", None)
                timeout = max(8.0, self.net.start_timeout)
                if suspend is not None:
                    # Atomically gate future control sends before draining
                    # START_VARA and releasing the shared audio device.
                    control_released = suspend(timeout=timeout)
                else:
                    control_released = self.audio_transport.wait_tx_idle(timeout=timeout)
                    if control_released:
                        self.audio_transport.stop()
                if not control_released:
                    raise TimeoutError(
                        dual(
                            "The pending control burst did not finish before VARA handoff.",
                            "Čekající řídicí rámec nebyl dokončen před předáním VARA.",
                        )
                    )
                self._log(dual(
                    "Control audio released for payload.",
                    "Řídicí zvuk uvolněn pro datový přenos.",
                ), source="control")
        except Exception:
            self._payload_active.clear()
            raise

    def _resume_control(self) -> None:
        with self._payload_handoff_lock:
            if self._closing.is_set():
                self._payload_active.clear()
                return
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
        if not self.has_frequency_control():
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
        self._closing.set()
        self._calibration_cancel.set()
        with self._payload_handoff_lock:
            self._payload_handoff_resume = None
            self._payload_handoff_fault = ""
            self._payload_handoff_retry_at = 0.0
        payload = getattr(getattr(self, "net", None), "payload", None)
        shutdown = getattr(payload, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                pass
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
        self._payload_active.clear()
