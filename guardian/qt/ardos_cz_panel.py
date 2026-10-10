"""ARDOS CZ session controls; no network calls from Qt callbacks."""
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QInputDialog, QLineEdit, QMessageBox

from ..i18n import dual


def server_status(path):
    state = path.get('state', '')
    if state == 'received' and path.get('origin_verified') is False:
        relay = path.get('relay') or '?'
        return dual(f'Via RF relay {relay}; origin unverified',
                    f'Přes RF relay {relay}; původ neověřen')
    labels = {
        'checking': ('Checking server route', 'Ověřuji serverovou cestu'),
        'uploading': ('Passing to server', 'Předávám serveru'),
        'unknown': ('Upload uncertain; checking', 'Výsledek neznámý; ověřuji'),
        'accepted': ('Server accepted; awaiting recipient', 'Server převzal; čeká na vyzvednutí'),
        'delivered': ('Recipient imported', 'Adresát převzal'),
        'received': ('Received via ARDOS CZ', 'Přijato přes ARDOS CZ'),
        'expired': ('Server retention expired; operator decision required', 'Úložní doba vypršela; nutné rozhodnutí'),
        'rejected': ('Server rejected; RF available', 'Server odmítl; lze použít RF'),
        'not_accepted': ('Server did not accept; RF available', 'Server nepřevzal; lze použít RF'),
        'rf_fallback': ('Uncertain upload; RF fallback', 'Neověřený upload; návrat na RF'),
    }
    return dual(*labels[state]) if state in labels else ''


class ArdosPanel(QWidget):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        layout = QVBoxLayout(self)
        hint = QLabel(dual('Apply the ARDOS CZ settings before registering or connecting.',
                           'Před registrací nebo připojením použijte nastavení ARDOS CZ.'))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        register = QPushButton(dual('Register device with invitation', 'Registrovat zařízení pozvánkou'))
        register.clicked.connect(self.enroll)
        register.setEnabled(service is not None)
        layout.addWidget(register)
        connect = QPushButton(dual('Connect', 'Připojit'))
        connect.clicked.connect(lambda: service.connect())
        connect.setEnabled(service is not None)
        layout.addWidget(connect)
        disconnect = QPushButton(dual('Disconnect', 'Odpojit'))
        disconnect.clicked.connect(lambda: service.disconnect())
        disconnect.setEnabled(service is not None)
        layout.addWidget(disconnect)
        layout.addStretch()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh()

    def refresh(self):
        service = self.service
        snapshot = service.snapshot() if service else {'state': 'disabled', 'last_verified': 0}
        labels = {
            'disabled': ('Disabled', 'Vypnuto'), 'not_enrolled': ('Not registered', 'Neregistrováno'),
            'connecting': ('Connecting', 'Připojuji'), 'online': ('Online', 'Online'),
            'grant_expired': ('Grant expired', 'Oprávnění vypršelo'),
            'grant_denied': ('Grant denied', 'Oprávnění odmítnuto'),
            'device_revoked': ('Device revoked', 'Zařízení odvoláno'),
            'unavailable': ('Server unavailable', 'Server nedostupný'),
            'local_error': ('Local import/storage error; message not acknowledged', 'Chyba místního importu/úložiště; zpráva nepotvrzena'),
        }
        value = dual(*labels[snapshot['state']]) if snapshot['state'] in labels else snapshot['state']
        verified = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(snapshot['last_verified'])) if snapshot['last_verified'] else '—'
        self.status.setText(f"ARDOS CZ: {value}\n" + dual('Last verified: ', 'Poslední ověření: ') + verified)

    def enroll(self):
        service = self.service
        if service is None:
            return
        if not service.enabled():
            QMessageBox.information(self, 'ARDOS CZ', dual('Enable ARDOS CZ in Settings first.', 'Nejprve zapněte ARDOS CZ v Nastavení.'))
            return
        invite, ok = QInputDialog.getText(self, 'ARDOS CZ', dual('Invitation code:', 'Kód pozvánky:'), QLineEdit.EchoMode.Password)
        if ok and invite.strip():
            try:
                accepted = service.enroll(invite.strip())
            except (ValueError, RuntimeError):
                accepted = False
            if not accepted:
                QMessageBox.information(self, 'ARDOS CZ', dual('Check configuration or wait for the current request.', 'Ověřte nastavení nebo vyčkejte na dokončení požadavku.'))
