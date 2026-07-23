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
        return f"{self.frequency_hz / 1_000_000:.5f} MHz"


@dataclass(frozen=True, slots=True)
class VaraSnapshot:
    command_connected: bool = False
    data_connected: bool = False
    mycall: str = ""
    link_state: str = "DISCONNECTED"
    last_notification: str = ""
    error: str | None = None


@dataclass(frozen=True, slots=True)
class MailboxSnapshot:
    inbox: int = 0
    unread: int = 0
    outbox: int = 0
    transit: int = 0


@dataclass(frozen=True, slots=True)
class NetworkSnapshot:
    active_sessions: int = 0
    heard_stations: int = 0
    control_channel_active: bool = False
    scanner_active: bool = False


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
