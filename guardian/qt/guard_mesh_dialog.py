"""Guard Mesh connection settings; the persistent panel owns the BLE event pump."""
from __future__ import annotations

from queue import Empty
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget
from ..guard_mesh import MeshBleClient
from ..guard_mesh_rpc import MeshApi
from ..i18n import dual


class GuardMeshPanel(QWidget):
    def __init__(self, parent=None, *, client=None, runtime=None):
        super().__init__(parent)
        self.client = client if client is not None else MeshBleClient()
        self.api = MeshApi(runtime) if runtime is not None else None
        self._operation = ""
        layout = QVBoxLayout(self)
        intro = QLabel(dual("Enable Bluetooth on your device, then search and connect.",
                            "Zapněte Bluetooth na zařízení, vyhledejte ho a připojte."))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.devices = QListWidget()
        self.devices.setAccessibleName(dual("Guard Mesh devices", "Zařízení Guard Mesh"))
        self.devices.setMinimumHeight(100)
        layout.addWidget(self.devices, 1)
        actions = QHBoxLayout()
        self.scan_button = QPushButton(dual("Search", "Vyhledat"))
        self.connect_button = QPushButton(dual("Pair and connect", "Spárovat a připojit"))
        self.disconnect_button = QPushButton(dual("Disconnect / cancel", "Odpojit / zrušit"))
        for button in (self.scan_button, self.connect_button, self.disconnect_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.scan_button.clicked.connect(self._scan)
        self.connect_button.clicked.connect(self._connect)
        self.disconnect_button.clicked.connect(self._disconnect)
        self.devices.itemSelectionChanged.connect(self._buttons)
        self.connection = QLabel(dual("Disconnected", "Odpojeno"))
        self.message = QLabel(dual("The connection stays active when settings are closed.",
                                  "Připojení zůstane aktivní i po zavření nastavení."))
        for label in (self.connection, self.message):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            layout.addWidget(label)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        self._buttons()

    def _buttons(self):
        busy = self.client.busy
        self.scan_button.setEnabled(not busy)
        self.connect_button.setEnabled(not busy and self.devices.currentItem() is not None and self.api is not None)
        self.disconnect_button.setEnabled(busy and self._operation != "stopping")
        self.devices.setEnabled(not busy)

    def _scan(self):
        self._poll()
        if self.client.scan():
            self._operation = "scan"
            self.devices.clear()
            self.message.setText(dual("Searching…", "Vyhledávám…"))
        self._buttons()

    def _connect(self):
        self._poll()
        item = self.devices.currentItem()
        if item is not None and self.client.connect(item.data(Qt.ItemDataRole.UserRole)):
            self._operation = "connect"
            self.connection.setText(dual("Connecting…", "Připojuji…"))
            self.message.setText(dual("Confirm pairing if prompted.", "Potvrďte případnou výzvu k párování."))
        self._buttons()

    def _disconnect(self):
        self._operation = "stopping"
        self.client.stop()
        self.connection.setText(dual("Disconnecting…", "Odpojuji…"))
        self._buttons()

    def _poll(self):
        # Keep radio/UI work responsive even when a peer sends many requests.
        for _ in range(16):
            try:
                kind, payload = self.client.events.get_nowait()
            except Empty:
                break
            if self._operation == "stopping" and kind != "finished":
                continue
            if kind == "devices":
                self.devices.clear()
                for device in payload:
                    item = QListWidgetItem(f"{device.name} · {device.address} · {device.rssi} dBm")
                    item.setData(Qt.ItemDataRole.UserRole, device)
                    self.devices.addItem(item)
                if payload:
                    self.devices.setCurrentRow(0)
                self.message.setText(dual("Select a device." if payload else "No devices found.",
                                         "Vyberte zařízení." if payload else "Žádné zařízení nebylo nalezeno."))
            elif kind == "connected":
                self.connection.setText(dual("Connected", "Připojeno") + f" · {payload.name}")
                self.message.setText(dual("The connection stays active when settings are closed.",
                                          "Připojení zůstane aktivní i po zavření nastavení."))
            elif kind == "request":
                session, tid, request, peer = payload
                if self.api is not None and self.client.session_active(session):
                    response = self.api.handle(request, peer)
                    self.client.reply(session, tid, response)
            elif kind == "disconnected":
                self.connection.setText(dual("Disconnected", "Odpojeno"))
                self.message.setText(dual("Select a device to reconnect.", "Pro připojení vyberte zařízení."))
            elif kind == "error":
                self.connection.setText(dual("Disconnected", "Odpojeno"))
                self.message.setText(dual("BLE error: ", "Chyba BLE: ") + payload)
            elif kind == "finished":
                if self._operation == "stopping":
                    self.connection.setText(dual("Disconnected", "Odpojeno"))
                    self.message.setText(dual("Cancelled.", "Zrušeno."))
                self._operation = ""
        self._buttons()

    def shutdown(self):
        self.client.stop()
        self.timer.stop()
