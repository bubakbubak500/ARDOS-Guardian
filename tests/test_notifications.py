"""The desktop announcement layer: what fires, what stays quiet, and why."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
import wave
from types import SimpleNamespace

from guardian.assets.sounds import ensure_emergency_wav, ensure_notify_wav
from guardian.config import StationConfig
from guardian.message import Folder, MailMessage, MessageStore, Status
from guardian.operations import AlertRecord
from guardian.protocol import Priority
import guardian.qt.notifications as notifications
from guardian.qt.notifications import NotificationCenter, SoundPlayer


def _wav_seconds(path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / handle.getframerate()


def test_chimes_are_generated_valid_and_distinct(tmp_path) -> None:
    mail = ensure_notify_wav(tmp_path / "notify.wav")
    emergency = ensure_emergency_wav(tmp_path / "emergency.wav")
    assert _wav_seconds(mail) > 0.2
    # The emergency voice must be unmistakably longer and different.
    assert _wav_seconds(emergency) > _wav_seconds(mail) + 0.2
    # Idempotent: a second call reuses the cached file.
    assert ensure_notify_wav(tmp_path / "notify.wav") == mail


def test_chime_never_plays_toward_the_radio(monkeypatch) -> None:
    played, refused = [], []
    monkeypatch.setattr(notifications, "play_wav", lambda p: played.append(p) or True)
    config = StationConfig(audio_output="Głośniki (USB Audio CODEC )")
    player = SoundPlayer(config, on_refused=refused.append)

    # The system default output IS the radio codec: refuse, and say so once.
    monkeypatch.setattr(
        notifications,
        "default_output_device",
        lambda: "Głośniki (USB Audio CODEC )",
    )
    assert not player.play("mail")
    assert not player.play("emergency")
    assert played == []
    assert len(refused) == 1

    # Unknown default device: still refuse — a chime on the air is worse
    # than a missed chime in the shack.
    monkeypatch.setattr(notifications, "default_output_device", lambda: None)
    assert not player.play("mail")
    assert played == []

    # A laptop speaker as default: play.
    monkeypatch.setattr(
        notifications, "default_output_device", lambda: "Reproduktory (Realtek)"
    )
    assert player.play("mail")
    assert len(played) == 1

    # The master sound switch silences everything, including emergencies.
    config.notify_sound = False
    assert not player.play("emergency")
    assert len(played) == 1


def _center(tmp_path, **config_overrides):
    config = StationConfig(callsign="OK7PS", **config_overrides)
    runtime = SimpleNamespace(
        mailstore=MessageStore(tmp_path / "mail"),
        operations=SimpleNamespace(alerts=[]),
        config=config,
    )
    log = SimpleNamespace(toasts=[], emergencies=[], played=[], active=False)
    center = NotificationCenter(
        runtime,
        toast=lambda title, body: log.toasts.append((title, body)),
        emergency=lambda title, body: log.emergencies.append((title, body)),
        play=lambda kind: log.played.append(kind) or True,
        window_active=lambda: log.active,
        sound_allowed=lambda: not getattr(log, "ptt", False),
    )
    return runtime, center, log


def _inbox_mail(runtime, msg_id, *, subject="Hello", priority=0):
    mail = MailMessage(
        msg_id=msg_id,
        source="OK2IPW",
        final_dest=runtime.config.callsign,
        subject=subject,
        priority=priority,
        created=time.time(),
        folder=Folder.INBOX,
        status=Status.RECEIVED,
    )
    mail.read = False
    runtime.mailstore.add(mail)
    return mail


def test_existing_mail_is_seeded_and_new_mail_announced_once(tmp_path) -> None:
    runtime, center, log = _center(tmp_path)
    _inbox_mail(runtime, 1, subject="Old news")
    center.poll()
    assert log.toasts == []  # a week of unread mail must not replay at startup

    _inbox_mail(runtime, 2, subject="Fresh")
    center.poll()
    center.poll()
    assert len(log.toasts) == 1
    assert "OK2IPW" in log.toasts[0][0]
    assert log.toasts[0][1] == "Fresh"
    assert log.played == ["mail"]


def test_no_toast_while_the_operator_is_already_looking(tmp_path) -> None:
    runtime, center, log = _center(tmp_path)
    center.poll()
    log.active = True
    _inbox_mail(runtime, 3)
    center.poll()
    assert log.toasts == []
    # And it was consumed, not postponed: going inactive replays nothing.
    log.active = False
    center.poll()
    assert log.toasts == []


def test_urgent_mail_raises_the_window_even_when_active(tmp_path) -> None:
    runtime, center, log = _center(tmp_path)
    center.poll()
    log.active = True
    _inbox_mail(runtime, 4, subject="Need help", priority=int(Priority.EMERGENCY))
    center.poll()
    assert len(log.emergencies) == 1
    assert log.toasts == []


def test_alerts_follow_priority_and_skip_our_own(tmp_path) -> None:
    runtime, center, log = _center(tmp_path)
    center.poll()
    now = time.time()
    runtime.operations.alerts.insert(
        0,  # 0x10 = QRT, Priority.ROUTINE
        AlertRecord(code=0x10, note="", source="OK7PS", received=now, mine=True),
    )
    runtime.operations.alerts.insert(
        0, AlertRecord(code=0x10, note="73", source="OK2IPW", received=now, mine=False)
    )
    center.poll()
    assert len(log.toasts) == 1        # the neighbour's routine alert
    assert log.emergencies == []       # ours stayed silent, and QRT is routine

    runtime.operations.alerts.insert(
        0,  # 0x02 = medical emergency
        AlertRecord(code=0x02, note="", source="OK1AAA", received=now, mine=False),
    )
    stale = AlertRecord(
        code=0x02, note="", source="OK5OLD", received=now - 3_600, mine=False
    )
    runtime.operations.alerts.insert(0, stale)
    center.poll()
    assert len(log.emergencies) == 1   # the fresh medical one
    assert len(log.toasts) == 1        # the stale one was too old to interrupt


def test_disabling_notifications_still_lets_an_emergency_through(tmp_path) -> None:
    runtime, center, log = _center(tmp_path, notify_incoming=False)
    center.poll()
    _inbox_mail(runtime, 5)
    runtime.operations.alerts.insert(
        0,
        AlertRecord(  # 0x01 = MAYDAY, Priority.EMERGENCY
            code=0x01, note="", source="OK2IPW", received=time.time(), mine=False
        ),
    )
    center.poll()
    assert log.toasts == []          # the polite level honours the switch
    assert len(log.emergencies) == 1  # the net's emergency does not


def test_sound_suppressed_while_transmitting(tmp_path) -> None:
    runtime, center, log = _center(tmp_path)
    center.poll()
    log.ptt = True
    _inbox_mail(runtime, 6)
    center.poll()
    assert len(log.toasts) == 1  # the toast still appears
    assert log.played == []      # the shack stays quiet
