"""Desktop control surface for Guardian's offline phone companion."""

from __future__ import annotations

import io
import socket

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..i18n import dual


def local_ipv4_addresses(*, hotspot: bool = False) -> list[str]:
    """Return useful LAN addresses without making an internet request."""
    found: set[str] = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = item[4][0]
            if address and not address.startswith("127.") and address != "0.0.0.0":
                found.add(address)
    except OSError:
        pass
    ordered = sorted(found, key=lambda value: (not value.startswith("192.168."), value))
    if hotspot and "192.168.137.1" not in ordered:
        ordered.insert(0, "192.168.137.1")
    return ordered or ["192.168.137.1"]


def _qr_pixmap(value: str, size: int = 210) -> QPixmap | None:
    try:
        import qrcode

        image = qrcode.make(value, box_size=7, border=3)
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        pixmap = QPixmap()
        if pixmap.loadFromData(stream.getvalue(), "PNG"):
            return pixmap.scaled(size, size)
    except Exception:
        pass
    return None


class CompanionDialog(QDialog):
    """Start/stop the server, Wi-Fi Direct AP, pairing and RF authority."""

    def __init__(self, runtime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.controller = runtime.companion
        self.hotspot = runtime.companion_hotspot
        self._last_wifi_value = ""
        self._last_link_value = ""
        self.setWindowTitle(dual("Offline phone companion", "Offline companion pro telefon"))
        self.setMinimumSize(760, 650)

        outer = QVBoxLayout(self)
        intro = QLabel(
            dual(
                "Guardian serves the companion directly to iOS and Android. "
                "Nothing leaves the local Wi-Fi network. Pairing is one-use and expires after five minutes.",
                "Guardian poskytuje companion přímo pro iOS a Android. Nic neopustí místní Wi-Fi. "
                "Párování je jednorázové a vyprší za pět minut.",
            )
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)
        install_hint = QLabel(
            dual(
                "iPhone: open the link in Safari → Share → Add to Home Screen.  "
                "Android: open it in Chrome → menu → Add to Home screen.",
                "iPhone: otevřete odkaz v Safari → Sdílet → Přidat na plochu.  "
                "Android: otevřete v Chrome → nabídka → Přidat na plochu.",
            )
        )
        install_hint.setWordWrap(True)
        outer.addWidget(install_hint)

        controls = QHBoxLayout()
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(int(getattr(runtime.config, "companion_port", 8765)))
        self.server_button = QPushButton()
        self.server_button.clicked.connect(self._toggle_server)
        self.hotspot_button = QPushButton()
        self.hotspot_button.clicked.connect(self._toggle_hotspot)
        settings = QPushButton(dual("Windows hotspot settings", "Nastavení hotspotu Windows"))
        settings.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("ms-settings:network-mobilehotspot"))
        )
        controls.addWidget(QLabel(dual("Port", "Port")))
        controls.addWidget(self.port)
        controls.addWidget(self.server_button)
        controls.addWidget(self.hotspot_button)
        controls.addWidget(settings)
        outer.addLayout(controls)

        form = QFormLayout()
        self.server_status = QLabel()
        self.server_status.setWordWrap(True)
        self.hotspot_status = QLabel()
        self.hotspot_status.setWordWrap(True)
        self.address = QComboBox()
        self.address.currentIndexChanged.connect(self._render_pairing)
        self.clients = QLabel()
        self.clients.setWordWrap(True)
        form.addRow(dual("Server", "Server"), self.server_status)
        form.addRow(dual("Wi-Fi", "Wi-Fi"), self.hotspot_status)
        form.addRow(dual("Phone address", "Adresa pro telefon"), self.address)
        form.addRow(dual("Paired phones", "Spárované telefony"), self.clients)
        outer.addLayout(form)

        permissions = QHBoxLayout()
        self.emergency = QCheckBox(
            dual(
                "Arm immediate emergency RF transmission from phone",
                "Povolit okamžité nouzové RF vysílání z telefonu",
            )
        )
        self.emergency.toggled.connect(self._set_emergency)
        self.previews = QCheckBox(
            dual("Hide message previews on phone", "Skrýt náhledy zpráv v telefonu")
        )
        self.previews.toggled.connect(self._set_previews)
        permissions.addWidget(self.emergency)
        permissions.addWidget(self.previews)
        outer.addLayout(permissions)

        qr_row = QHBoxLayout()
        wifi_box = QWidget()
        wifi_layout = QVBoxLayout(wifi_box)
        wifi_title = QLabel(dual("1 · Join Guardian Wi-Fi", "1 · Připojit Guardian Wi-Fi"))
        wifi_title.setObjectName("PanelHeader")
        self.wifi_qr = QLabel()
        self.wifi_qr.setMinimumSize(215, 215)
        self.wifi_qr.setScaledContents(False)
        self.wifi_text = QLabel()
        self.wifi_text.setWordWrap(True)
        wifi_layout.addWidget(wifi_title)
        wifi_layout.addWidget(self.wifi_qr)
        wifi_layout.addWidget(self.wifi_text)
        link_box = QWidget()
        link_layout = QVBoxLayout(link_box)
        link_title = QLabel(dual("2 · Open and pair", "2 · Otevřít a spárovat"))
        link_title.setObjectName("PanelHeader")
        self.link_qr = QLabel()
        self.link_qr.setMinimumSize(215, 215)
        self.link_text = QLabel()
        self.link_text.setWordWrap(True)
        link_layout.addWidget(link_title)
        link_layout.addWidget(self.link_qr)
        link_layout.addWidget(self.link_text)
        qr_row.addWidget(wifi_box, 1)
        qr_row.addWidget(link_box, 1)
        outer.addLayout(qr_row, 1)

        bottom = QHBoxLayout()
        renew = QPushButton(dual("New one-use pairing QR", "Nový jednorázový párovací QR"))
        renew.clicked.connect(self._renew_pairing)
        revoke = QPushButton(dual("Disconnect all phones", "Odpojit všechny telefony"))
        revoke.clicked.connect(self.controller.revoke_all)
        close = QPushButton(dual("Close", "Zavřít"))
        close.clicked.connect(self.accept)
        bottom.addWidget(renew)
        bottom.addWidget(revoke)
        bottom.addStretch()
        bottom.addWidget(close)
        outer.addLayout(bottom)

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def _toggle_server(self) -> None:
        if self.controller.running:
            self.controller.stop()
            self.refresh()
            return
        try:
            selected_port = int(self.port.value())
            self.controller.start(port=selected_port)
        except OSError as exc:
            QMessageBox.warning(self, self.windowTitle(), str(exc))
            return
        self.runtime.config.companion_port = selected_port
        self.runtime.config.save()
        self._refresh_addresses()
        self.refresh()

    def _toggle_hotspot(self) -> None:
        if self.hotspot.running:
            result = self.hotspot.stop()
        else:
            result = self.hotspot.start(self.runtime.config.callsign)
        if not result.ok:
            QMessageBox.warning(self, self.windowTitle(), result.message)
            QDesktopServices.openUrl(QUrl("ms-settings:network-mobilehotspot"))
        self._refresh_addresses()
        self.refresh()

    def _set_emergency(self, checked: bool) -> None:
        if checked:
            answer = QMessageBox.warning(
                self,
                dual("Remote emergency transmission", "Vzdálené nouzové vysílání"),
                dual(
                    "A paired phone will be able to key the configured radio for an EMERGENCY message. "
                    "Guardian still requires a live control channel. Arm this only while you are away from the console.",
                    "Spárovaný telefon bude moci zaklíčovat nakonfigurované rádio pro NOUZOVOU zprávu. "
                    "Guardian stále vyžaduje aktivní řídicí kanál. Povolte pouze po dobu mimo konzoli.",
                ),
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Ok:
                self.emergency.blockSignals(True)
                self.emergency.setChecked(False)
                self.emergency.blockSignals(False)
                return
        self.controller.remote_emergency_armed = checked
        self.controller.poll()

    def _set_previews(self, checked: bool) -> None:
        self.controller.hide_message_previews = checked
        self.controller.poll()

    def _renew_pairing(self) -> None:
        if not self.controller.running:
            QMessageBox.information(
                self, self.windowTitle(), dual("Start the server first.", "Nejdříve spusťte server.")
            )
            return
        self.controller.new_pairing()
        self._render_pairing()

    def _refresh_addresses(self) -> None:
        selected = self.address.currentText()
        values = local_ipv4_addresses(hotspot=self.hotspot.running)
        self.address.blockSignals(True)
        self.address.clear()
        for value in values:
            self.address.addItem(value)
        if selected in values:
            self.address.setCurrentText(selected)
        self.address.blockSignals(False)
        self._render_pairing()

    def refresh(self) -> None:
        self.server_button.setText(
            dual("Stop companion", "Zastavit companion")
            if self.controller.running
            else dual("Start companion", "Spustit companion")
        )
        self.hotspot_button.setText(
            dual("Stop Wi-Fi Direct", "Zastavit Wi-Fi Direct")
            if self.hotspot.running
            else dual("Start offline Wi-Fi", "Spustit offline Wi-Fi")
        )
        self.port.setEnabled(not self.controller.running)
        self.server_status.setText(
            dual(
                f"Running locally on port {self.controller.bound_port}.",
                f"Běží lokálně na portu {self.controller.bound_port}.",
            )
            if self.controller.running
            else dual("Stopped.", "Zastaven.")
        )
        if self.hotspot.running:
            self.hotspot_status.setText(
                f"{self.hotspot.ssid}  ·  {self.hotspot.password}"
            )
        else:
            self.hotspot_status.setText(
                dual(
                    "Use an existing shared Wi-Fi, Windows Mobile Hotspot, or start Guardian Wi-Fi Direct.",
                    "Použijte společnou Wi-Fi, Mobilní hotspot Windows, nebo spusťte Guardian Wi-Fi Direct.",
                )
            )
        clients = self.controller.clients()
        self.clients.setText(
            ", ".join(
                f"{'●' if item['online'] else '○'} {item['name']} ({item['address']})"
                for item in clients
            )
            or dual("None", "Žádné")
        )
        if self.address.count() == 0:
            self._refresh_addresses()
        else:
            self._render_pairing()

    def _render_pairing(self) -> None:
        ssid = self.hotspot.ssid
        password = self.hotspot.password
        if ssid:
            wifi_value = f"WIFI:T:WPA;S:{ssid};P:{password};;"
            if wifi_value != self._last_wifi_value:
                pixmap = _qr_pixmap(wifi_value)
                self.wifi_qr.setPixmap(pixmap or QPixmap())
                self._last_wifi_value = wifi_value
            self.wifi_text.setText(f"SSID: {ssid}\n{dual('Password', 'Heslo')}: {password}")
        else:
            self.wifi_qr.clear()
            self._last_wifi_value = ""
            self.wifi_text.setText(
                dual("Already on the same Wi-Fi? Continue with step 2.", "Jste už na stejné Wi-Fi? Pokračujte krokem 2.")
            )
        address = self.address.currentText().strip()
        token = self.controller.pairing_fragment()
        if self.controller.running and address and token:
            url = f"http://{address}:{self.controller.bound_port}/#pair={token}"
            if url != self._last_link_value:
                pixmap = _qr_pixmap(url)
                self.link_qr.setPixmap(pixmap or QPixmap())
                self._last_link_value = url
            self.link_text.setText(url)
        elif self.controller.running and not token:
            self.link_qr.clear()
            self._last_link_value = ""
            self.link_text.setText(
                dual("Pairing QR was used. Create a new one for another phone.", "Párovací QR byl použit. Pro další telefon vytvořte nový.")
            )
        else:
            self.link_qr.clear()
            self._last_link_value = ""
            self.link_text.setText(dual("Start the companion server.", "Spusťte companion server."))
