"""Task-oriented, bilingual Guardian settings dialog."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QSettings, Signal
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
from ..i18n import Language, dual, language, set_language, tr
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
        browse = QPushButton(dual("Browse…", "Procházet…"))
        browse.clicked.connect(self._browse)
        layout.addWidget(self.edit, 1)
        layout.addWidget(browse)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            dual("Select executable", "Vyberte spustitelný soubor"),
            self.edit.text(),
            dual(
                f"Executable ({self.executable});;All files (*)",
                f"Spustitelný soubor ({self.executable});;Všechny soubory (*)",
            ),
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
        *,
        settings: QSettings | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.settings = settings or QSettings()
        self.setWindowTitle(tr("settings.title"))
        self.setMinimumSize(800, 620)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        intro = QLabel(tr("settings.intro"))
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
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText(
            tr("common.save")
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(
            tr("common.cancel")
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).setText(
            tr("common.apply")
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
        layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        label = QLabel(description)
        label.setObjectName("Metadata")
        label.setWordWrap(True)
        layout.addRow(label)
        self.tabs.addTab(page, title)
        return layout

    def _build_identity(self) -> None:
        form = self._page(
            tr("settings.station"),
            dual(
                "Your station identity is used in every control frame and message.",
                "Identita stanice se používá v každém řídicím rámci a zprávě.",
            ),
        )
        self.callsign = QLineEdit(self.config.callsign)
        self.callsign.setMaxLength(16)
        self.callsign.setPlaceholderText("OK1AAA")
        self.operator_name = QLineEdit(self.config.operator_name)
        form.addRow(dual("Callsign", "Volací značka"), self.callsign)
        form.addRow(dual("Operator name", "Jméno operátora"), self.operator_name)

    def _build_radio(self) -> None:
        form = self._page(
            tr("settings.radio"),
            dual(
                "Choose how Guardian keys and tunes the radio. Hamlib uses "
                "rigctld; VOX uses only an RTS/DTR serial PTT line.",
                "Zvolte způsob klíčování a ladění rádia. Hamlib používá rigctld; "
                "VOX používá pouze sériovou linku PTT RTS/DTR.",
            ),
        )
        self.radio_backend = QComboBox()
        self.radio_backend.addItem(
            dual("No radio control", "Bez řízení rádia"), "none"
        )
        self.radio_backend.addItem("Hamlib / rigctld", "hamlib")
        self.radio_backend.addItem(
            dual("VOX / serial PTT", "VOX / sériové PTT"), "vox"
        )
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
        form.addRow(dual("Control method", "Způsob řízení"), self.radio_backend)
        form.addRow(dual("Radio model/name", "Model / název rádia"), self.radio_name)
        form.addRow(dual("Hamlib model ID", "ID modelu Hamlib"), self.rig_model)
        form.addRow(dual("CAT / PTT serial port", "Sériový port CAT / PTT"), self.cat_port)
        form.addRow(dual("CAT baud (0 = automatic)", "Rychlost CAT (0 = automaticky)"), self.cat_baud)
        form.addRow(dual("rigctld host", "Adresa rigctld"), self.rigctld_host)
        form.addRow(dual("rigctld port", "Port rigctld"), self.rigctld_port)
        form.addRow(dual("rigctld executable", "Program rigctld"), self.rigctld_path)
        form.addRow(dual("VOX PTT line", "Linka PTT pro VOX"), self.ptt_line)

    def _build_vara(self) -> None:
        form = self._page(
            tr("settings.vara"),
            dual(
                "Select the active VARA flavor and whether Guardian transfers "
                "the payload directly or coordinates a manual Winlink hand-off.",
                "Zvolte variantu VARA a zda Guardian přenese obsah přímo, nebo "
                "bude koordinovat ruční předání přes Winlink.",
            ),
        )
        self.vara_mode = QComboBox()
        self.vara_mode.addItems(["FM", "HF"])
        self.vara_mode.setCurrentText(self.config.vara_mode)
        self.payload_backend = QComboBox()
        self.payload_backend.addItem("Guardian VARA P2P", "vara_p2p")
        self.payload_backend.addItem(
            dual("Manual Winlink hand-off", "Ruční předání přes Winlink"),
            "winlink_manual",
        )
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
        self.control_modem.addItem(
            dual("Automatic for FM/HF", "Automaticky podle FM/HF"), "auto"
        )
        self.control_modem.addItem("AFSK 1200", "afsk1200")
        self.control_modem.addItem("MFSK 16", "mfsk16")
        self.control_modem.setCurrentIndex(
            max(0, self.control_modem.findData(self.config.control_modem))
        )
        form.addRow(dual("Active VARA mode", "Aktivní režim VARA"), self.vara_mode)
        form.addRow(dual("Payload workflow", "Způsob přenosu"), self.payload_backend)
        form.addRow(dual("VARA host", "Adresa VARA"), self.vara_host)
        form.addRow(dual("VARA FM command port", "Příkazový port VARA FM"), self.vara_fm_cmd)
        form.addRow(dual("VARA FM data port", "Datový port VARA FM"), self.vara_fm_data)
        form.addRow(dual("VARA FM executable", "Program VARA FM"), self.vara_fm_path)
        form.addRow(dual("VARA HF command port", "Příkazový port VARA HF"), self.vara_hf_cmd)
        form.addRow(dual("VARA HF data port", "Datový port VARA HF"), self.vara_hf_data)
        form.addRow(dual("VARA HF executable", "Program VARA HF"), self.vara_hf_path)
        form.addRow(dual("Control-burst modem", "Modem řídicích rámců"), self.control_modem)

    def _build_network(self) -> None:
        form = self._page(
            tr("settings.network"),
            dual(
                "Control automatic routing and delivery behavior. These "
                "options do not change payload encoding.",
                "Nastavte automatické směrování a doručování. Tyto volby "
                "nemění kódování přenášeného obsahu.",
            ),
        )
        self.default_ttl = _spin(1, 32, self.config.default_ttl)
        self.auto_route = QCheckBox(
            dual(
                "Discover routes when no manual route exists",
                "Vyhledat trasu, pokud není nastavena ručně",
            )
        )
        self.auto_route.setChecked(self.config.auto_route)
        self.auto_relay = QCheckBox(
            dual("Relay messages for other stations", "Předávat zprávy jiným stanicím")
        )
        self.auto_relay.setChecked(self.config.auto_relay)
        self.auto_deliver = QCheckBox(
            dual(
                "Deliver queued mail when a hop is heard",
                "Doručit zprávy z fronty po zaslechnutí další stanice",
            )
        )
        self.auto_deliver.setChecked(self.config.auto_deliver)
        self.auto_qsy = QCheckBox(
            dual(
                "Tune automatically before VARA P2P",
                "Automaticky přeladit před VARA P2P",
            )
        )
        self.auto_qsy.setChecked(self.config.auto_qsy)
        self.beacon_enabled = QCheckBox(
            dual("Transmit presence beacons", "Vysílat majáky přítomnosti")
        )
        self.beacon_enabled.setChecked(self.config.beacon_enabled)
        self.beacon_interval = _spin(15, 86_400, int(self.config.beacon_interval))
        self.scan_dwell = _spin(1, 300, int(self.config.scan_dwell))
        form.addRow(dual("Default hop limit (TTL)", "Výchozí limit skoků (TTL)"), self.default_ttl)
        form.addRow(self.auto_route)
        form.addRow(self.auto_relay)
        form.addRow(self.auto_deliver)
        form.addRow(self.auto_qsy)
        form.addRow(self.beacon_enabled)
        form.addRow(dual("Beacon interval (seconds)", "Interval majáku (sekundy)"), self.beacon_interval)
        form.addRow(dual("Channel scan dwell (seconds)", "Doba poslechu kanálu (sekundy)"), self.scan_dwell)

    def _build_appearance(self, theme: ThemePreference) -> None:
        form = self._page(
            tr("settings.appearance"),
            dual(
                "Theme and language are applied immediately after Save or Apply.",
                "Motiv a jazyk se použijí ihned po uložení nebo použití změn.",
            ),
        )
        self.theme = QComboBox()
        self.theme.addItem(tr("theme.system"), ThemePreference.SYSTEM.value)
        self.theme.addItem(tr("theme.light"), ThemePreference.LIGHT.value)
        self.theme.addItem(tr("theme.dark"), ThemePreference.DARK.value)
        self.theme.setCurrentIndex(max(0, self.theme.findData(theme.value)))
        self.language = QComboBox()
        self.language.addItem("English", Language.ENGLISH.value)
        self.language.addItem("Čeština", Language.CZECH.value)
        current = str(self.settings.value("ui/language", language().value))
        self.language.setCurrentIndex(max(0, self.language.findData(current)))
        form.addRow(tr("menu.theme"), self.theme)
        form.addRow(tr("settings.language"), self.language)

    @property
    def selected_theme(self) -> ThemePreference:
        return ThemePreference(self.theme.currentData())

    @property
    def selected_language(self) -> Language:
        return Language(self.language.currentData())

    def validation_errors(self) -> list[str]:
        errors: list[str] = []
        callsign = self.callsign.text().strip().upper()
        if callsign != "NOCALL" and not _CALLSIGN.fullmatch(callsign):
            errors.append(
                dual(
                    "Callsign must contain 3–16 letters, digits or '/' characters.",
                    "Volací značka musí obsahovat 3–16 písmen, číslic nebo znak '/'.",
                )
            )
        if not self.rigctld_host.text().strip():
            errors.append(dual("rigctld host cannot be empty.", "Adresa rigctld nesmí být prázdná."))
        if not self.vara_host.text().strip():
            errors.append(dual("VARA host cannot be empty.", "Adresa VARA nesmí být prázdná."))
        if self.radio_backend.currentData() == "hamlib" and self.rig_model.value() < 1:
            errors.append(
                dual(
                    "Choose a Hamlib model ID when Hamlib control is enabled.",
                    "Při řízení přes Hamlib zvolte ID modelu rádia.",
                )
            )
        for label, field in (
            ("rigctld", self.rigctld_path),
            ("VARA FM", self.vara_fm_path),
            ("VARA HF", self.vara_hf_path),
        ):
            value = field.text()
            if value and Path(value).suffix.lower() == ".exe" and not Path(value).is_file():
                errors.append(
                    dual(
                        f"{label} executable does not exist: {value}",
                        f"Program {label} neexistuje: {value}",
                    )
                )
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
        selected_language = self.selected_language
        self.settings.setValue("ui/language", selected_language.value)
        self.settings.sync()
        set_language(selected_language)
        self.saved.emit()
        return True

    def _save_and_accept(self) -> None:
        if self.apply():
            self.accept()
