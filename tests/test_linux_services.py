"""Linux native services remain lazy, private, and safe without a desktop."""
import importlib
import json
import os
import sys
from types import ModuleType, SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import PySide6

from guardian.ardos_cz.credentials import WindowsCredentials
from guardian.ardos_cz.service import ArdosService
import guardian.qt.notifications as notifications
import guardian_launch


def _secret_backend(monkeypatch, *, error=None):
    calls = []
    secrets = {}

    class Keyring:
        def get_password(self, service, user):
            calls.append(('read', service, user))
            if error:
                raise error
            return secrets.get((service, user))

        def set_password(self, service, user, value):
            calls.append(('write', service, user))
            if error:
                raise error
            secrets[service, user] = value

    keyring = ModuleType('keyring')
    # A generic backend selector must never be used, even if configured by
    # the desktop to a plaintext fallback.
    keyring.get_keyring = lambda: pytest.fail('generic keyring selected')
    backends = ModuleType('keyring.backends')
    secret_service = ModuleType('keyring.backends.SecretService')
    secret_service.Keyring = Keyring
    for module in (keyring, backends, secret_service):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    return secrets, calls


def test_linux_credentials_use_only_secret_service(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'linux')
    secrets, calls = _secret_backend(monkeypatch)
    credentials = WindowsCredentials('https://example.test', 'OK7PS', '/profile')
    assert credentials.load() is None
    value = {'device_id': 'test-device', 'private_key': 'test-secret'}
    credentials.save(value)
    assert credentials.load() == value
    assert calls == [
        ('read', credentials.target, 'Guardian ARDOS CZ'),
        ('write', credentials.target, 'Guardian ARDOS CZ'),
        ('read', credentials.target, 'Guardian ARDOS CZ'),
    ]
    assert json.loads(secrets[credentials.target, 'Guardian ARDOS CZ']) == value
    other = WindowsCredentials('https://example.test', 'OK7PS', '/other-profile')
    assert other.target != credentials.target
    assert other.load() is None


@pytest.mark.parametrize('operation', ['load', 'save'])
def test_linux_credentials_fail_without_secret_service(monkeypatch, operation):
    monkeypatch.setattr(sys, 'platform', 'linux')
    _secret_backend(monkeypatch, error=RuntimeError('backend-secret-detail'))
    credentials = WindowsCredentials('https://example.test', 'OK7PS', '/profile')
    with pytest.raises(RuntimeError, match='Secret Service') as caught:
        if operation == 'load':
            credentials.load()
        else:
            credentials.save({'private_key': 'private-secret'})
    assert 'GNOME Keyring' in str(caught.value)
    assert 'backend-secret-detail' not in str(caught.value)
    assert 'private-secret' not in str(caught.value)


def test_ardos_service_constructs_without_accessing_desktop_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr(WindowsCredentials, '_secret_service',
                        staticmethod(lambda: pytest.fail('desktop store accessed')))
    operations = SimpleNamespace(
        mailstore=SimpleNamespace(root=tmp_path),
        config=SimpleNamespace(ardos_cz_url='https://example.test', callsign='OK7PS'),
    )
    service = ArdosService(operations)
    try:
        client = service._client()
        assert isinstance(client.credentials, WindowsCredentials)
        assert client.credentials.target.startswith('Guardian/ARDOS-CZ/')
    finally:
        service.close()


@pytest.mark.parametrize('operation', ['load', 'save'])
def test_windows_credentials_still_use_credential_manager(monkeypatch, operation):
    monkeypatch.setattr(sys, 'platform', 'win32')
    credentials = WindowsCredentials('https://example.test', 'OK7PS', 'profile')
    monkeypatch.setattr(credentials, '_secret_service',
                        lambda: pytest.fail('Windows selected Linux keyring'))

    def api():
        raise OSError('original Windows credential API')

    monkeypatch.setattr(credentials, '_api', api)
    with pytest.raises(OSError, match='original Windows credential API'):
        if operation == 'load':
            credentials.load()
        else:
            credentials.save({})


def _qt_audio(monkeypatch, *, null=False, error=False):
    output = SimpleNamespace(isNull=lambda: null, description=lambda: 'Laptop speakers')
    sounds = []

    class Sound:
        Status = SimpleNamespace(Error='error')

        def __init__(self, parent):
            self.parent = parent
            self.plays = 0
            sounds.append(self)

        def setSource(self, url):
            self.url = url

        def setAudioDevice(self, device):
            self.device = device

        def status(self):
            return 'error' if error else 'ready'

        def play(self):
            self.plays += 1

    multimedia = ModuleType('PySide6.QtMultimedia')
    multimedia.QMediaDevices = SimpleNamespace(defaultAudioOutput=lambda: output)
    multimedia.QSoundEffect = Sound
    monkeypatch.setitem(sys.modules, multimedia.__name__, multimedia)
    monkeypatch.setattr(notifications, '_linux_sounds', {})
    application = object()
    monkeypatch.setattr(notifications.QApplication, 'instance', lambda: application)
    return output, sounds, application


def test_linux_chime_retains_player_and_uses_checked_default_device(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'platform', 'linux')
    output, sounds, app = _qt_audio(monkeypatch)
    assert notifications.default_output_device() == 'Laptop speakers'
    path = tmp_path / 'notify.wav'
    assert notifications.play_wav(path)
    assert notifications.play_wav(path)
    assert len(sounds) == 1
    assert sounds[0].parent is app
    assert sounds[0].device is output
    assert sounds[0].url.toLocalFile() == str(path.resolve()).replace('\\', '/')
    assert sounds[0].plays == 2


@pytest.mark.parametrize('null,error', [(True, False), (False, True)])
def test_linux_chime_handles_unavailable_audio(monkeypatch, tmp_path, null, error):
    monkeypatch.setattr(sys, 'platform', 'linux')
    _, sounds, _ = _qt_audio(monkeypatch, null=null, error=error)
    assert not notifications.play_wav(tmp_path / 'notify.wav')
    if null:
        assert notifications.default_output_device() is None
        assert not sounds
    else:
        assert sounds[0].plays == 0


def test_linux_chime_refuses_radio_output(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'linux')
    _, sounds, _ = _qt_audio(monkeypatch)
    config = SimpleNamespace(audio_output='Laptop speakers', notify_sound=True)
    refused = []
    player = notifications.SoundPlayer(config, on_refused=refused.append)
    assert not player.play('mail')
    assert not sounds
    assert len(refused) == 1


def test_windows_chime_uses_original_winsound_flags(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'platform', 'win32')
    played = []
    winsound = SimpleNamespace(SND_FILENAME=1, SND_ASYNC=2, SND_NODEFAULT=4,
                              PlaySound=lambda path, flags: played.append((path, flags)))
    monkeypatch.setitem(sys.modules, 'winsound', winsound)
    path = tmp_path / 'notify.wav'
    assert notifications.play_wav(path)
    assert played == [(str(path), 7)]


@pytest.mark.parametrize('platform,backend', [('linux', 'bluezdbus'), ('win32', 'winrt')])
def test_frozen_qt_self_test_uses_platform_ble_backend(monkeypatch, tmp_path, platform, backend):
    monkeypatch.setattr(sys, 'platform', platform)
    report = tmp_path / 'qt-self-test.txt'
    monkeypatch.setattr(sys, 'argv', ['guardian', '--qt-self-test-report', str(report)])
    imported = []

    def module(name):
        imported.append(name)
        return ModuleType(name)

    monkeypatch.setattr(importlib, 'import_module', module)
    monkeypatch.setattr(PySide6, 'QtMultimedia', ModuleType('PySide6.QtMultimedia'), raising=False)
    guardian_launch._run_qt_self_test()
    content = report.read_text(encoding='utf-8')
    assert content.startswith('PASS\n')
    assert f'BLE client=bleak.backends.{backend}.client' in content
    assert f'BLE scanner=bleak.backends.{backend}.scanner' in content
    if platform == 'linux':
        assert not any('winrt' in name for name in imported)
        assert 'keyring.backends.SecretService' in imported
        assert 'QtMultimedia module=PySide6.QtMultimedia' in content
    else:
        assert imported[:2] == ['winrt.windows.devices.geolocation', 'winrt.windows.foundation']
        assert not any('bluezdbus' in name or 'SecretService' in name for name in imported)
        assert 'WinRT geolocation=winrt.windows.devices.geolocation' in content
