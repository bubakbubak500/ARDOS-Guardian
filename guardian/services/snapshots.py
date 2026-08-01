"""Immutable UI-facing snapshots of mutable application state."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class RadioSnapshot:
    connected: bool = False
    name: str = "none"
    frequency_hz: int | None = None
    mode: str | None = None
    ptt: bool = False
    signal: int | None = None
    error: str | None = None

    def freq_mhz(self) -> str:
        if self.frequency_hz is None:
            return "--"
        return f"{self.frequency_hz / 1_000_000:.4f} MHz"


@dataclass(frozen=True, slots=True)
class VaraSnapshot:
    command_connected: bool = False
    data_connected: bool = False
    mycall: str = ""
    link_state: str = "DISCONNECTED"
    last_notification: str = ""
    transport_lost: bool = False
    tx_buffer_bytes: int | None = None
    buffer_reports: int = 0
    rejected_commands: int = 0
    data_socket_reopens: int = 0
    tx_bitrate_bps: int | None = None
    data_bytes_written: int = 0
    data_bytes_read: int = 0
    data_socket_generation: int = 0
    data_local_endpoint: str | None = None
    data_peer_endpoint: str | None = None
    ptt: bool = False
    ptt_keyings: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class MailboxSnapshot:
    inbox: int = 0
    unread: int = 0
    outbox: int = 0
    # Of `outbox`, how many are parked after a failed send rather than queued.
    outbox_failed: int = 0
    transit: int = 0


@dataclass(frozen=True, slots=True)
class NetworkSnapshot:
    active_sessions: int = 0
    heard_stations: int = 0
    control_channel_active: bool = False
    scanner_active: bool = False
    scanner_holding: bool = False
    scanner_paused: bool = False
    scanner_channel: str = ""
    scanner_frequency_hz: int | None = None
    scanner_channels: int = 0


@dataclass(frozen=True, slots=True)
class DependencySnapshot:
    hamlib_available: bool = False
    hamlib_path: str | None = None
    vara_fm_available: bool = False
    vara_hf_available: bool = False


@dataclass(frozen=True, slots=True)
class ApplicationSnapshot:
    revision: int = 0
    radio: RadioSnapshot = RadioSnapshot()
    vara: VaraSnapshot = VaraSnapshot()
    mailbox: MailboxSnapshot = MailboxSnapshot()
    network: NetworkSnapshot = NetworkSnapshot()
    dependencies: DependencySnapshot = DependencySnapshot()


class SnapshotStore:
    """Atomic snapshot replacement for worker/UI hand-off."""

    def __init__(self) -> None:
        self._value = ApplicationSnapshot()
        self._lock = threading.Lock()

    def read(self) -> ApplicationSnapshot:
        with self._lock:
            return self._value

    def update(self, **changes: object) -> ApplicationSnapshot:
        with self._lock:
            self._value = replace(
                self._value,
                revision=self._value.revision + 1,
                **changes,
            )
            return self._value
