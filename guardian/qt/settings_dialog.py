"""Task-oriented Guardian settings dialog."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import StationConfig
from .theme import ThemePreference

_CALLSIGN = re.compile(r"^[A-Z0-9/]{3,16}$")


def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
    widget = QSpinBox()
    widget.setRange(minimum, maximum)
    widget.setValue(value)
    return widget


class PathField(QWidget):
    def __init__(self, value: str = "", executable: str = "*.exe"):
        super().__init__()
        self.executable = executable
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.edit = QLineEdit(value)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        layout.addWidget(self.edit, 1)
        layout.addWidget(browse)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select executable",
            self.edit.text(),
            f"Executable ({self.executable});;All files (*)",
        )
        if path:
            self.edit.setText(path)

    def text(self) -> str:
        return self.edit.text().strip()


class SettingsDialog(QDialog):
    saved = Signal()

    def __init__(
        self,
        config: StationConfig,
        theme: ThemePreference,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Guardian settings")
        self.setMinimumSize(760, 590)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        intro = QLabel(
            "Settings are grouped by operator task. Changes are validated before "
            "they are written to the station profile."
        )
        intro.setObjectName("Metadata")
        intro.setWordWrap(True)
        outer.addWidget(intro)
        self.error = QLabel()
        self.error.setProperty("statusRole", "danger")
        self.error.setWordWrap(True)
        self.error.hide()
        outer.addWidget(self.error)

        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        self._build_identity()
        self._build_radio()
        self._build_vara()
        self._build_network()
        self._build_appearance(theme)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply
        )
        self.buttons.accepted.connect(self._save_and_accept)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(
            self.apply
        )
        outer.addWidget(self.buttons)

    def _page(self, title: str, description: str) -> QFormLayout:
        page = QWidget()
        layout = QFormLayout(page)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setHorizontalSpacing(16)
        layout.setVerticalSpacing(10)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        label = QLabel(description)
        label.setObjectName("Metadata")
        label.setWordWrap(True)
        layout.addRow(label)
        self.tabs.addTab(page, title)
        return layout

    def _build_identity(self) -> None:
        form = self._page(
            "Station",
            "Your station identity is used in every control frame and message.",
        )
        self.callsign = QLineEdit(self.config.callsign)
        self.callsign.setMaxLength(16)
        self.callsign.setPlaceholderText("OK1AAA")
        self.operator_name = QLineEdit(self.config.operator_name)
        form.addRow("Callsign", self.callsign)
        form.addRow("Operator name", self.operator_name)

    def _build_radio(self) -> None:
        form = self._page(
            "Radio control",
            "Choose how Guardian keys and tunes the radio. Hamlib uses rigctld; "
            "VOX uses only an RTS/DTR serial PTT line.",
        )
        self.radio_backend = QComboBox()
        self.radio_backend.addItem("No radio control", "none")
        self.radio_backend.addItem("Hamlib / rigctld", "hamlib")
        self.radio_backend.addItem("VOX / serial PTT", "vox")
        index = self.radio_backend.findData(self.config.radio_backend)
        self.radio_backend.setCurrentIndex(max(0, index))
        self.radio_name = QLineEdit(self.config.radio)
        self.rig_model = _spin(0, 999_999, self.config.rig_model)
        self.cat_port = QLineEdit(self.config.cat_port)
        self.cat_port.setPlaceholderText("COM7")
        self.cat_baud = _spin(0, 1_000_000, self.config.cat_baud)
        self.rigctld_host = QLineEdit(self.config.rigctld_host)
        self.rigctld_port = _spin(1, 65_535, self.config.rigctld_port)
        self.rigctld_path = PathField(self.config.rigctld_path, "rigctld.exe")
        self.ptt_line = QComboBox()
        self.ptt_line.addItems(["RTS", "DTR"])
        self.ptt_line.setCurrentText(self.config.ptt_line)
        form.addRow("Control method", self.radio_backend)
        form.addRow("Radio model/name", self.radio_name)
        form.addRow("Hamlib model ID", self.rig_model)
        form.addRow("CAT / PTT serial port", self.cat_port)
        form.addRow("CAT baud (0 = automatic)", self.cat_baud)
        form.addRow("rigctld host", self.rigctld_host)
        form.addRow("rigctld port", self.rigctld_port)
        form.addRow("rigctld executable", self.rigctld_path)
        form.addRow("VOX PTT line", self.ptt_line)

    def _build_vara(self) -> None:
        form = self._page(
            "VARA & payload",
            "Select the active VARA flavor and whether Guardian transfers the "
            "payload directly or coordinates a manual Winlink hand-off.",
        )
        self.vara_mode = QComboBox()
        self.vara_mode.addItems(["FM", "HF"])
        self.vara_mode.setCurrentText(self.config.vara_mode)
        self.payload_backend = QComboBox()
        self.payload_backend.addItem("Guardian VARA P2P", "vara_p2p")
        self.payload_backend.addItem("Manual Winlink hand-off", "winlink_manual")
        self.payload_backend.setCurrentIndex(
            max(0, self.payload_backend.findData(self.config.payload_backend))
        )
        self.vara_host = QLineEdit(self.config.vara_host)
        self.vara_fm_cmd = _spin(1, 65_535, self.config.vara_fm_cmd_port)
        self.vara_fm_data = _spin(1, 65_535, self.config.vara_fm_data_port)
        self.vara_hf_cmd = _spin(1, 65_535, self.config.vara_hf_cmd_port)
        self.vara_hf_data = _spin(1, 65_535, self.config.vara_hf_data_port)
        self.vara_fm_path = PathField(self.config.vara_fm_path, "VARAFM.exe")
        self.vara_hf_path = PathField(self.config.vara_hf_path, "VARA.exe")
        self.control_modem = QComboBox()
        self.control_modem.addItem("Automatic for FM/HF", "auto")
        self.control_modem.addItem("AFSK 1200", "afsk1200")
        self.control_modem.addItem("MFSK 16", "mfsk16")
        self.control_modem.setCurrentIndex(
            max(0, self.control_modem.findData(self.config.control_modem))
        )
        form.addRow("Active VARA mode", self.vara_mode)
        form.addRow("Payload workflow", self.payload_backend)
        form.addRow("VARA host", self.vara_host)
        form.addRow("VARA FM command port", self.vara_fm_cmd)
        form.addRow("VARA FM data port", self.vara_fm_data)
        form.addRow("VARA FM executable", self.vara_fm_path)
        form.addRow("VARA HF command port", self.vara_hf_cmd)
        form.addRow("VARA HF data port", self.vara_hf_data)
        form.addRow("VARA HF executable", self.vara_hf_path)
        form.addRow("Control-burst modem", self.control_modem)

    def _build_network(self) -> None:
        form = self._page(
            "Network behavior",
            "Control automatic routing and delivery behavior. These options do "
            "not change payload encoding.",
        )
        self.default_ttl = _spin(1, 32, self.config.default_ttl)
        self.auto_route = QCheckBox("Discover routes when no manual route exists")
        self.auto_route.setChecked(self.config.auto_route)
        self.auto_relay = QCheckBox("Relay messages for other stations")
        self.auto_relay.setChecked(self.config.auto_relay)
        self.auto_deliver = QCheckBox("Deliver queued mail when a hop is heard")
        self.auto_deliver.setChecked(self.config.auto_deliver)
        self.auto_qsy = QCheckBox("Tune automatically before VARA P2P")
        self.auto_qsy.setChecked(self.config.auto_qsy)
        self.beacon_enabled = QCheckBox("Transmit presence beacons")
        self.beacon_enabled.setChecked(self.config.beacon_enabled)
        self.beacon_interval = _spin(15, 86_400, int(self.config.beacon_interval))
        self.scan_dwell = _spin(1, 300, int(self.config.scan_dwell))
        form.addRow("Default hop limit (TTL)", self.default_ttl)
        form.addRow(self.auto_route)
        form.addRow(self.auto_relay)
        form.addRow(self.auto_deliver)
        form.addRow(self.auto_qsy)
        form.addRow(self.beacon_enabled)
        form.addRow("Beacon interval (seconds)", self.beacon_interval)
        form.addRow("Channel scan dwell (seconds)", self.scan_dwell)

    def _build_appearance(self, theme: ThemePreference) -> None:
        form = self._page(
            "Appearance",
            "The Monitor theme is applied immediately after Save or Apply.",
        )
        self.theme = QComboBox()
        self.theme.addItem("Follow Windows", ThemePreference.SYSTEM.value)
        self.theme.addItem("Light", ThemePreference.LIGHT.value)
        self.theme.addItem("Dark", ThemePreference.DARK.value)
        self.theme.setCurrentIndex(max(0, self.theme.findData(theme.value)))
        form.addRow("Theme", self.theme)

    @property
    def selected_theme(self) -> ThemePreference:
        return ThemePreference(self.theme.currentData())

    def validation_errors(self) -> list[str]:
        errors: list[str] = []
        callsign = self.callsign.text().strip().upper()
        if callsign != "NOCALL" and not _CALLSIGN.fullmatch(callsign):
            errors.append(
                "Callsign must contain 3–16 letters, digits or '/' characters."
            )
        if not self.rigctld_host.text().strip():
            errors.append("rigctld host cannot be empty.")
        if not self.vara_host.text().strip():
            errors.append("VARA host cannot be empty.")
        if self.radio_backend.currentData() == "hamlib" and self.rig_model.value() < 1:
            errors.append("Choose a Hamlib model ID when Hamlib control is enabled.")
        for label, field in (
            ("rigctld", self.rigctld_path),
            ("VARA FM", self.vara_fm_path),
            ("VARA HF", self.vara_hf_path),
        ):
            value = field.text()
            if value and Path(value).suffix.lower() == ".exe" and not Path(value).is_file():
                errors.append(f"{label} executable does not exist: {value}")
        return errors

    def apply(self) -> bool:
        errors = self.validation_errors()
        if errors:
            self.error.setText("\n".join(f"• {message}" for message in errors))
            self.error.show()
            return False
        self.error.hide()
        cfg = self.config
        cfg.callsign = self.callsign.text().strip().upper() or "NOCALL"
        cfg.operator_name = self.operator_name.text().strip()
        cfg.radio_backend = self.radio_backend.currentData()
        cfg.radio = self.radio_name.text().strip()
        cfg.rig_model = self.rig_model.value()
        cfg.cat_port = self.cat_port.text().strip()
        cfg.cat_baud = self.cat_baud.value()
        cfg.rigctld_host = self.rigctld_host.text().strip()
        cfg.rigctld_port = self.rigctld_port.value()
        cfg.rigctld_path = self.rigctld_path.text() or "rigctld"
        cfg.ptt_line = self.ptt_line.currentText()
        cfg.vara_host = self.vara_host.text().strip()
        cfg.vara_fm_cmd_port = self.vara_fm_cmd.value()
        cfg.vara_fm_data_port = self.vara_fm_data.value()
        cfg.vara_hf_cmd_port = self.vara_hf_cmd.value()
        cfg.vara_hf_data_port = self.vara_hf_data.value()
        cfg.vara_fm_path = self.vara_fm_path.text()
        cfg.vara_hf_path = self.vara_hf_path.text()
        cfg.payload_backend = self.payload_backend.currentData()
        cfg.control_modem = self.control_modem.currentData()
        cfg.apply_vara_mode(self.vara_mode.currentText())
        cfg.default_ttl = self.default_ttl.value()
        cfg.auto_route = self.auto_route.isChecked()
        cfg.auto_relay = self.auto_relay.isChecked()
        cfg.auto_deliver = self.auto_deliver.isChecked()
        cfg.auto_qsy = self.auto_qsy.isChecked()
        cfg.beacon_enabled = self.beacon_enabled.isChecked()
        cfg.beacon_interval = float(self.beacon_interval.value())
        cfg.scan_dwell = float(self.scan_dwell.value())
        cfg.appearance = self.selected_theme.value.title()
        cfg.save()
        self.saved.emit()
        return True

    def _save_and_accept(self) -> None:
        if self.apply():
            self.accept()
