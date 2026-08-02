"""Desktop notifications: a toast for mail, an unmissable window for alerts.

Two levels on purpose. A routine message gets the polite treatment -- a tray
toast and a soft chime, skipped entirely when the operator is already looking
at Guardian. An URGENT or EMERGENCY alert gets a window that stays on top and
a sound that repeats until someone acknowledges it, deliberately bypassing
anything Windows could be told to silence: a station running unattended in a
crisis is the whole reason this application exists.

The sound has one hard rule: it must never leave the transmitter. Guardian's
audio output is wired to the radio, and on many stations that same USB codec
is also the Windows *default* device. The player therefore checks what the
default output is and refuses to chime at all when it cannot prove the chime
stays in the shack.
"""

from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..assets.sounds import ensure_emergency_wav, ensure_notify_wav
from ..i18n import tr
from ..message import Folder, Status
from ..modem.audio import match_device_name
from ..protocol import Priority

# An alert older than this no longer interrupts anyone; the banner and the log
# keep the history.
ALERT_ANNOUNCE_WINDOW = 900.0
EMERGENCY_REPEAT_MS = 4_000


def default_output_device() -> str | None:
    """Name of the Windows default output device, or None when unknowable."""
    try:
        import sounddevice

        info = sounddevice.query_devices(kind="output")
        return str(info["name"]) if info else None
    except Exception:  # noqa: BLE001 - PortAudio hiccups must not raise here
        return None


def play_wav(path) -> bool:
    """Asynchronously play a WAV on the system default output device."""
    try:
        import winsound

        winsound.PlaySound(
            str(path),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
        return True
    except Exception:  # noqa: BLE001 - a sound API fault must not stop mail
        return False


class SoundPlayer:
    """Plays the chimes on the system default device -- unless that IS the radio.

    Failing closed is deliberate: an unplayed chime is an annoyance, a chime
    transmitted on the air is not. When the default output cannot be
    determined, or it resolves to the device configured as the radio's audio
    output, nothing is played and the refusal is reported once.
    """

    def __init__(self, config, on_refused: Callable[[str], None] | None = None):
        self.config = config
        self.on_refused = on_refused
        self._refusal_reported = False

    def _blocked(self) -> str:
        """Why the chime must stay silent, or "" when it may play."""
        default = default_output_device()
        if default is None:
            return "the system default output device is unknown"
        radio = (self.config.audio_output or "").strip()
        if radio and match_device_name([default], radio) is not None:
            return f"the system default output is the radio ({default})"
        return ""

    def play(self, kind: str) -> bool:
        if not getattr(self.config, "notify_sound", True):
            return False
        reason = self._blocked()
        if reason:
            if not self._refusal_reported and self.on_refused is not None:
                self._refusal_reported = True
                self.on_refused(reason)
            return False
        path = ensure_emergency_wav() if kind == "emergency" else ensure_notify_wav()
        return play_wav(path)


class NotificationCenter:
    """Decides what deserves an announcement. Owns no widgets.

    The first poll seeds what already exists without announcing any of it --
    starting Guardian over a full mailbox must not replay a week of chimes.
    Everything after that is announced exactly once.
    """

    def __init__(
        self,
        runtime,
        *,
        toast: Callable[[str, str], None],
        emergency: Callable[[str, str], None],
        play: Callable[[str], bool],
        window_active: Callable[[], bool] = lambda: False,
        sound_allowed: Callable[[], bool] = lambda: True,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.runtime = runtime
        self.toast = toast
        self.emergency = emergency
        self.play = play
        self.window_active = window_active
        self.sound_allowed = sound_allowed
        self.clock = clock
        self._seen_mail: set[int] | None = None
        self._seen_alerts: set[tuple[str, float]] | None = None

    # ------------------------------------------------------------------ #
    def poll(self) -> None:
        first = self._seen_mail is None
        self._poll_mail(announce=not first)
        self._poll_alerts(announce=not first)

    def _inbox(self) -> list[dict]:
        return [
            meta
            for meta in self.runtime.mailstore.list(Folder.INBOX)
            if meta.get("status") == Status.RECEIVED and not meta.get("read", True)
        ]

    def _poll_mail(self, *, announce: bool) -> None:
        if self._seen_mail is None:
            self._seen_mail = set()
        for meta in self._inbox():
            msg_id = int(meta.get("msg_id", 0))
            if msg_id in self._seen_mail:
                continue
            self._seen_mail.add(msg_id)
            if not announce or not getattr(self.runtime.config, "notify_incoming", True):
                continue
            source = str(meta.get("source", ""))
            subject = str(meta.get("subject", "")) or tr("mail.no_subject")
            if int(meta.get("priority", 0)) >= int(Priority.URGENT):
                self.emergency(
                    tr("notify.urgent_mail_title", source=source), subject
                )
                continue
            if not self.window_active():
                self.toast(tr("notify.mail_title", source=source), subject)
                if self.sound_allowed():
                    self.play("mail")

    def _poll_alerts(self, *, announce: bool) -> None:
        if self._seen_alerts is None:
            self._seen_alerts = set()
        from .alerts import alert_headline  # local: avoids a module cycle

        now = self.clock()
        for record in getattr(self.runtime.operations, "alerts", []):
            key = (record.source, record.received)
            if key in self._seen_alerts:
                continue
            self._seen_alerts.add(key)
            if not announce or record.mine:
                continue
            if now - record.received > ALERT_ANNOUNCE_WINDOW:
                continue
            headline = alert_headline(record)
            body = f"{record.source}" + (f" — {record.note}" if record.note else "")
            if int(record.priority) >= int(Priority.URGENT):
                # Deliberately not gated on notify_incoming or on the window
                # being active: an emergency on the net is what the station is
                # listening for.
                self.emergency(headline, body)
            elif getattr(self.runtime.config, "notify_incoming", True):
                if not self.window_active():
                    self.toast(headline, body)
                    if self.sound_allowed():
                        self.play("mail")


class EmergencyDialog(QDialog):
    """Stays on top and keeps sounding until the operator acknowledges it.

    One instance serves the whole session: a second emergency arriving while
    the first is unacknowledged replaces the text and re-raises the window
    rather than stacking dialogs over each other.
    """

    def __init__(self, play: Callable[[str], bool], sound_allowed: Callable[[], bool]):
        super().__init__(None)
        self.play = play
        self.sound_allowed = sound_allowed
        self.setWindowTitle(tr("notify.emergency_window"))
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setMinimumWidth(420)
        self.setObjectName("EmergencyDialog")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 14)
        outer.setSpacing(8)
        self.headline = QLabel()
        self.headline.setObjectName("AlertHeadline")
        self.headline.setWordWrap(True)
        self.body = QLabel()
        self.body.setWordWrap(True)
        outer.addWidget(self.headline)
        outer.addWidget(self.body)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        acknowledge = QPushButton(tr("notify.acknowledge"))
        acknowledge.clicked.connect(self.accept)
        buttons.addWidget(acknowledge)
        outer.addLayout(buttons)

        self._repeat = QTimer(self)
        self._repeat.setInterval(EMERGENCY_REPEAT_MS)
        self._repeat.timeout.connect(self._sound)

    def announce(self, title: str, body: str) -> None:
        self.headline.setText(title)
        self.body.setText(body)
        self.show()
        self.raise_()
        self.activateWindow()
        self._sound()
        self._repeat.start()

    def _sound(self) -> None:
        if self.sound_allowed():
            self.play("emergency")

    def done(self, result: int) -> None:  # noqa: N802 - Qt override
        self._repeat.stop()
        super().done(result)
