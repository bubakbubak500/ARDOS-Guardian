"""Task-oriented, bilingual Guardian settings dialog."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QSettings, Signal, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import StationConfig
from ..i18n import Language, dual, language, set_language, tr
from ..modem.audio import match_device_name, scan_audio_devices
from ..protocol import MAX_PTT_DELAY_MS, PTT_DELAY_STEP_MS
from ..radio.presets import CURATED, load_hamlib_models
from ..radio.usb_serial import list_serial_ports, port_device
from .theme import ThemePreference
from .inputs import UppercaseLineEdit

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
        operations=None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.settings = settings or QSettings()
        # Only the live station can key a radio. Without it (tests, previews)
        # the PTT test is offered but disabled rather than hidden, so the
        # dialog looks the same wherever it is opened.
        self.operations = operations
        self.setWindowTitle(tr("settings.title"))
        self.setMinimumSize(960, 650)

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
        self._build_audio()
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
        self.callsign = UppercaseLineEdit(self.config.callsign)
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
        self.radio_model = QComboBox()
        self.radio_model.addItem(
            dual("Select a supported radio…", "Vyberte podporované rádio…"),
            0,
        )
        for preset in CURATED:
            if preset.backend == "hamlib" and preset.rig_model > 0:
                self.radio_model.addItem(preset.label, preset.rig_model)
        selected = self.radio_model.findData(self.config.rig_model)
        if self.config.rig_model > 0 and selected < 0:
            label = self.config.radio or dual("Saved radio", "Uložené rádio")
            self.radio_model.addItem(label, self.config.rig_model)
            selected = self.radio_model.count() - 1
        self.radio_model.setCurrentIndex(max(0, selected))
        browse_radios = QPushButton(
            dual("Browse all supported radios…", "Všechna podporovaná rádia…")
        )
        browse_radios.clicked.connect(self._browse_radios)
        radio_picker = QWidget()
        radio_picker_layout = QHBoxLayout(radio_picker)
        radio_picker_layout.setContentsMargins(0, 0, 0, 0)
        radio_picker_layout.setSpacing(6)
        radio_picker_layout.addWidget(self.radio_model, 1)
        radio_picker_layout.addWidget(browse_radios)
        # Typing "COM7" from memory was the only way to set this. The ports are
        # enumerable, so offer them -- still editable, because a port that is
        # unplugged right now is a perfectly valid thing to configure.
        self.cat_port = QComboBox()
        self.cat_port.setEditable(True)
        self.cat_port.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.cat_port.lineEdit().setPlaceholderText("COM7")
        refresh_ports = QPushButton(tr("settings.refresh_ports"))
        refresh_ports.clicked.connect(self._refresh_serial_ports)
        cat_port_row = QWidget()
        cat_port_layout = QHBoxLayout(cat_port_row)
        cat_port_layout.setContentsMargins(0, 0, 0, 0)
        cat_port_layout.setSpacing(6)
        cat_port_layout.addWidget(self.cat_port, 1)
        cat_port_layout.addWidget(refresh_ports)
        self._refresh_serial_ports()
        self.cat_baud = _spin(0, 1_000_000, self.config.cat_baud)
        self.rigctld_host = QLineEdit(self.config.rigctld_host)
        self.rigctld_port = _spin(1, 65_535, self.config.rigctld_port)
        self.rigctld_path = PathField(self.config.rigctld_path, "rigctld.exe")
        self.ptt_line = QComboBox()
        self.ptt_line.addItems(["RTS", "DTR"])
        self.ptt_line.setCurrentText(self.config.ptt_line)
        # rigctld can key three ways; the default CAT command only exists on
        # radios that *have* CAT. A no-CAT handheld behind an AIOC or data
        # cable is keyed by a serial control line, paired with the Dummy model.
        self.ptt_type = QComboBox()
        self.ptt_type.addItem(
            dual("CAT command (rig with CAT)", "Povel CAT (rádio s CAT)"), "RIG"
        )
        self.ptt_type.addItem(
            dual("RTS line on the serial port", "Linka RTS na sériovém portu"),
            "RTS",
        )
        self.ptt_type.addItem(
            dual("DTR line on the serial port", "Linka DTR na sériovém portu"),
            "DTR",
        )
        ptt_index = self.ptt_type.findData(
            (self.config.ptt_type or "RIG").upper()
        )
        self.ptt_type.setCurrentIndex(max(0, ptt_index))
        self.ptt_type.setToolTip(
            dual(
                "How rigctld keys the transmitter. For a radio without CAT "
                "(Baofeng-class handheld on an AIOC cable), pick the Hamlib "
                "Dummy model and RTS or DTR here — with CAT command the COM "
                "port is never touched at all.",
                "Jak rigctld klíčuje vysílač. Pro rádio bez CAT (ruční stanice "
                "přes kabel AIOC) zvolte model Hamlib Dummy a zde RTS nebo "
                "DTR — s povelem CAT se COM portu nikdy nikdo nedotkne.",
            )
        )
        form.addRow(dual("Control method", "Způsob řízení"), self.radio_backend)
        form.addRow(dual("Radio model", "Model rádia"), radio_picker)
        form.addRow(
            dual("CAT / PTT serial port", "Sériový port CAT / PTT"),
            cat_port_row,
        )
        form.addRow(dual("CAT baud (0 = automatic)", "Rychlost CAT (0 = automaticky)"), self.cat_baud)
        form.addRow(dual("rigctld host", "Adresa rigctld"), self.rigctld_host)
        form.addRow(dual("rigctld port", "Port rigctld"), self.rigctld_port)
        form.addRow(dual("rigctld executable", "Program rigctld"), self.rigctld_path)
        form.addRow(dual("Hamlib PTT via", "PTT přes (Hamlib)"), self.ptt_type)
        form.addRow(dual("VOX PTT line", "Linka PTT pro VOX"), self.ptt_line)

        # Slow-keying gap for cheap handhelds (AIOC-class cables): negotiated
        # with the peer during the handshake, applied before each VARA key-up.
        self.vara_ptt_delay = QSpinBox()
        self.vara_ptt_delay.setRange(0, MAX_PTT_DELAY_MS)
        self.vara_ptt_delay.setSingleStep(PTT_DELAY_STEP_MS)
        self.vara_ptt_delay.setSuffix(" ms")
        self.vara_ptt_delay.setSpecialValueText(
            dual("Off (default)", "Vypnuto (výchozí)")
        )
        self.vara_ptt_delay.setValue(
            max(0, min(int(self.config.vara_ptt_delay_ms or 0), MAX_PTT_DELAY_MS))
        )
        self.vara_ptt_delay.setToolTip(
            dual(
                "VARA FM only. If this radio clips the end of its bursts "
                "(typical for cheap handhelds on an AIOC cable), both "
                "stations agree during the handshake to keep PTT keyed this "
                "long after every VARA burst, so the tail leaves the radio "
                "before the carrier drops and the peer stops answering too "
                "early. Needs 'Let Guardian key the radio for VARA'. VARA's "
                "speed is not affected; 0 keeps today's behaviour.",
                "Pouze VARA FM. Pokud toto rádio ustřihává konec vysílání "
                "(typické pro levné ruční stanice přes kabel AIOC), obě "
                "stanice si při handshaku dohodnou, že po každém vysílání "
                "VARA podrží PTT ještě tuto dobu, aby konec burstu stihl "
                "opustit rádio, než spadne nosná — a protistanice tak "
                "neodpovídala příliš brzy. Vyžaduje „Guardian klíčuje rádio "
                "pro VARA“. Rychlost VARA se nemění; 0 ponechá dnešní "
                "chování.",
            )
        )
        form.addRow(
            dual("VARA FM keying delay", "Zpoždění klíčování VARA FM"),
            self.vara_ptt_delay,
        )

        # Proving that keying works is the one thing this page cannot tell you
        # from its own fields: the wiring is only ever confirmed on air.
        self.ptt_test_button = QPushButton(tr("settings.ptt_test"))
        self.ptt_test_button.setToolTip(tr("settings.ptt_test_hint"))
        self.ptt_test_button.clicked.connect(self._run_ptt_test)
        self.ptt_test_button.setEnabled(self.operations is not None)
        self.ptt_status = QLabel(tr("settings.ptt_test_hint"))
        self.ptt_status.setObjectName("Metadata")
        self.ptt_status.setWordWrap(True)
        test_row = QWidget()
        test_layout = QHBoxLayout(test_row)
        test_layout.setContentsMargins(0, 0, 0, 0)
        test_layout.setSpacing(6)
        test_layout.addWidget(self.ptt_test_button)
        test_layout.addStretch(1)
        form.addRow("", test_row)
        form.addRow("", self.ptt_status)

    def _refresh_serial_ports(self) -> None:
        """List the COM ports that exist, keeping whatever is configured."""
        selected = self.cat_port.currentText().strip() or self.config.cat_port
        device = port_device(selected)
        labels = list_serial_ports()
        self.cat_port.blockSignals(True)
        self.cat_port.clear()
        self.cat_port.addItem("")
        self.cat_port.addItems(labels)
        match = next(
            (label for label in labels if port_device(label) == device), device
        )
        self.cat_port.setCurrentText(match)
        self.cat_port.blockSignals(False)

    def selected_cat_port(self) -> str:
        """The bare device from the picker, without its description."""
        return port_device(self.cat_port.currentText().strip())

    def _run_ptt_test(self) -> None:
        if self.operations is None:
            return
        # The test keys the radio the live station is using, so unapplied
        # fields would prove nothing about it. Said in the status line rather
        # than a dialog -- the operator asked for one click, not two.
        if self._radio_settings_changed():
            self.ptt_status.setText(tr("settings.ptt_test_unsaved"))
            return
        self.ptt_test_button.setEnabled(False)
        self.ptt_status.setText(tr("settings.ptt_test_running"))
        # A refusal is reported synchronously through the same callback.
        self.operations.run_ptt_test(on_result=self._ptt_test_finished)

    def _ptt_test_finished(self, _ok: bool, message: str) -> None:
        try:
            self.ptt_status.setText(message)
            self.ptt_test_button.setEnabled(True)
        except RuntimeError:
            # The operator closed the dialog while the radio was keyed; the
            # result is in the log either way.
            pass

    def _radio_settings_changed(self) -> bool:
        return (
            self.radio_backend.currentData() != self.config.radio_backend
            or int(self.radio_model.currentData() or 0) != self.config.rig_model
            or self.selected_cat_port() != self.config.cat_port
            or self.cat_baud.value() != self.config.cat_baud
            or self.rigctld_host.text().strip() != self.config.rigctld_host
            or self.rigctld_port.value() != self.config.rigctld_port
            or self.rigctld_path.text() != self.config.rigctld_path
            or self.ptt_line.currentText() != self.config.ptt_line
            or self.ptt_type.currentData() != (self.config.ptt_type or "RIG").upper()
        )

    def _browse_radios(self) -> None:
        models = load_hamlib_models(self.rigctld_path.text())
        if not models:
            QMessageBox.warning(
                self,
                dual("Supported radios", "Podporovaná rádia"),
                dual(
                    "The Hamlib radio list is unavailable. Install or locate "
                    "Hamlib, then try again.",
                    "Seznam rádií Hamlib není dostupný. Nainstalujte nebo "
                    "vyhledejte Hamlib a zkuste to znovu.",
                ),
            )
            return
        models.sort(key=lambda model: model.label.casefold())
        labels = [model.label for model in models]
        label, accepted = QInputDialog.getItem(
            self,
            dual("Select radio", "Vyberte rádio"),
            dual("Supported radio model:", "Podporovaný model rádia:"),
            labels,
            editable=False,
        )
        if not accepted:
            return
        model = models[labels.index(label)]
        index = self.radio_model.findData(model.model_id)
        if index < 0:
            self.radio_model.addItem(model.label, model.model_id)
            index = self.radio_model.count() - 1
        self.radio_model.setCurrentIndex(index)
        self.radio_backend.setCurrentIndex(
            self.radio_backend.findData("hamlib")
        )

    def _build_audio(self) -> None:
        form = self._page(
            dual("Audio", "Zvuk"),
            dual(
                "Select the radio interface used for received and transmitted "
                "audio. Guardian never substitutes the Windows default "
                "microphone when no RX input is selected.",
                "Vyberte rozhraní rádia pro přijímaný a vysílaný zvuk. Pokud "
                "není vybrán RX vstup, Guardian jej nenahrazuje výchozím "
                "mikrofonem Windows.",
            ),
        )
        self.audio_input = QComboBox()
        self.audio_output = QComboBox()
        for combo in (self.audio_input, self.audio_output):
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToContentsOnFirstShow
            )
            combo.setMinimumContentsLength(55)
        refresh = QPushButton(dual("Refresh devices", "Obnovit zařízení"))
        refresh.clicked.connect(self._rescan_audio_devices)
        self.audio_status = QLabel()
        self.audio_status.setObjectName("Metadata")
        self.audio_status.setWordWrap(True)
        form.addRow(dual("Radio RX input", "Vstup RX rádia"), self.audio_input)
        form.addRow(dual("Radio TX output", "Výstup TX rádia"), self.audio_output)
        form.addRow(refresh, self.audio_status)
        self._refresh_audio_devices()

    @staticmethod
    def _populate_device_combo(
        combo: QComboBox,
        names: list[str],
        selected: str,
    ) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("")
        for name in names:
            combo.addItem(name)
            combo.setItemData(
                combo.count() - 1,
                name,
                Qt.ItemDataRole.ToolTipRole,
            )
        combo.setCurrentText(selected)
        combo.setToolTip(selected)
        if combo.lineEdit() is not None:
            combo.lineEdit().setToolTip(selected)
        combo.view().setTextElideMode(Qt.TextElideMode.ElideNone)
        metrics = QFontMetrics(combo.font())
        content_width = max(
            (metrics.horizontalAdvance(name) for name in [""] + names),
            default=0,
        )
        screen = combo.screen()
        available = screen.availableGeometry().width() if screen else 1200
        combo.view().setMinimumWidth(min(content_width + 42, available - 80))
        combo.blockSignals(False)

    def _rescan_audio_devices(self) -> None:
        """Refresh button: make PortAudio look at the hardware again.

        Without this the button re-read a snapshot taken when Guardian
        started, so a codec plugged in afterwards could never appear. The
        rescan is skipped while the control channel owns a stream — pulling
        the device out from under it would kill the running channel.
        """
        live = (
            self.operations is not None
            and getattr(self.operations, "audio_transport", None) is not None
        )
        self._refresh_audio_devices(reinitialise=not live)
        if live:
            self.audio_status.setText(
                dual(
                    "Rescanned the existing list only — stop the control "
                    "channel to detect newly connected devices. "
                    + self.audio_status.text(),
                    "Obnoven pouze stávající seznam — pro rozpoznání nově "
                    "připojených zařízení zastavte řídicí kanál. "
                    + self.audio_status.text(),
                )
            )

    def _refresh_audio_devices(self, *, reinitialise: bool = False) -> None:
        selected_input = (
            self.audio_input.currentText().strip()
            if self.audio_input.count()
            else self.config.audio_input
        )
        selected_output = (
            self.audio_output.currentText().strip()
            if self.audio_output.count()
            else self.config.audio_output
        )
        scan = scan_audio_devices(reinitialise=reinitialise)
        inputs, outputs = scan.inputs, scan.outputs
        selected_input = (
            match_device_name(inputs, selected_input) or selected_input
        )
        selected_output = (
            match_device_name(outputs, selected_output) or selected_output
        )
        self._populate_device_combo(self.audio_input, inputs, selected_input)
        self._populate_device_combo(self.audio_output, outputs, selected_output)
        if inputs or outputs:
            unavailable: list[str] = []
            if selected_input and selected_input not in inputs:
                unavailable.append(
                    dual(
                        "saved RX input is unavailable",
                        "uložený RX vstup není dostupný",
                    )
                )
            if selected_output and selected_output not in outputs:
                unavailable.append(
                    dual(
                        "saved TX output is unavailable",
                        "uložený TX výstup není dostupný",
                    )
                )
            suffix = f" {'; '.join(unavailable)}." if unavailable else ""
            self.audio_status.setText(
                dual(
                    f"Found {len(inputs)} input(s) and {len(outputs)} output(s).{suffix}",
                    f"Nalezeno vstupů: {len(inputs)}, výstupů: {len(outputs)}.{suffix}",
                )
            )
        else:
            # Say which of the three very different failures this is; the
            # blanket "check your settings" sent operators hunting through
            # Windows privacy for a missing DLL.
            reason = scan.error or dual(
                "no device was reported", "nebylo hlášeno žádné zařízení"
            )
            self.audio_status.setText(
                dual(
                    f"No audio devices: {reason}. Plug the interface in, then "
                    "press Refresh devices — and if it stays empty, export "
                    "Help ▸ Diagnostics, which now lists what the audio "
                    "backend itself reports.",
                    f"Žádná zvuková zařízení: {reason}. Připojte rozhraní a "
                    "stiskněte Obnovit zařízení — pokud zůstane prázdno, "
                    "exportujte Nápověda ▸ Diagnostika, kde je nově vypsáno, "
                    "co hlásí samotná zvuková vrstva.",
                )
            )

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
        # The manual Winlink hand-off was dropped in 0.6.26 once VARA P2P was
        # proven on air. The picker stays for the next transport rather than
        # being rebuilt from scratch.
        self.payload_backend = QComboBox()
        self.payload_backend.addItem("Guardian VARA P2P", "vara_p2p")
        self.payload_backend.setCurrentIndex(0)
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
        # VARA HF only; the reference lists no bandwidth command for FM, so the
        # row is hidden in FM mode rather than sending something VARA rejects.
        self.vara_hf_bandwidth = QComboBox()
        self.vara_hf_bandwidth.addItem(
            dual("2300 Hz — standard (default)", "2300 Hz — standardní (výchozí)"),
            "BW2300",
        )
        self.vara_hf_bandwidth.addItem(
            dual("500 Hz — narrow", "500 Hz — úzké"), "BW500"
        )
        self.vara_hf_bandwidth.addItem(
            dual("2750 Hz — tactical", "2750 Hz — taktické"), "BW2750"
        )
        self.vara_hf_bandwidth.setCurrentIndex(
            max(0, self.vara_hf_bandwidth.findData(self.config.vara_hf_bandwidth))
        )
        self.vara_hf_bandwidth.setToolTip(dual(
            "Both stations must agree. Narrower is slower but survives worse "
            "conditions and fits a crowded band.",
            "Obě stanice se musí shodnout. Užší je pomalejší, ale snese horší "
            "podmínky a vejde se do obsazeného pásma.",
        ))
        self.vara_host_ptt = QCheckBox(dual(
            "Let Guardian key the radio for VARA",
            "Klíčovat rádio pro VARA prostřednictvím Guardianu",
        ))
        self.vara_host_ptt.setChecked(self.config.vara_host_ptt)
        self.vara_host_ptt.setToolTip(dual(
            "Guardian reacts to VARA PTT ON/OFF through Hamlib. "
            "Do not configure VARA to own the same COM port.",
            "Guardian reaguje na povely VARA PTT ON/OFF přes Hamlib. "
            "Nenastavujte ve VARA současně vlastnictví stejného portu COM.",
        ))
        form.addRow(dual("Active VARA mode", "Aktivní režim VARA"), self.vara_mode)
        form.addRow(dual("Payload workflow", "Způsob přenosu"), self.payload_backend)
        form.addRow(dual("VARA host", "Adresa VARA"), self.vara_host)
        form.addRow(dual("VARA FM command port", "Příkazový port VARA FM"), self.vara_fm_cmd)
        form.addRow(dual("VARA FM data port", "Datový port VARA FM"), self.vara_fm_data)
        form.addRow(dual("VARA FM executable", "Program VARA FM"), self.vara_fm_path)
        form.addRow(dual("VARA HF command port", "Příkazový port VARA HF"), self.vara_hf_cmd)
        form.addRow(dual("VARA HF data port", "Datový port VARA HF"), self.vara_hf_data)
        form.addRow(dual("VARA HF executable", "Program VARA HF"), self.vara_hf_path)
        self.vara_hf_bandwidth_label = QLabel(
            dual("VARA HF bandwidth", "Šířka pásma VARA HF")
        )
        form.addRow(self.vara_hf_bandwidth_label, self.vara_hf_bandwidth)
        self.vara_mode.currentTextChanged.connect(self._sync_bandwidth_row)
        self._sync_bandwidth_row(self.vara_mode.currentText())
        form.addRow(dual("Control-burst modem", "Modem řídicích rámců"), self.control_modem)

        form.addRow(self.vara_host_ptt)

    def _sync_bandwidth_row(self, mode: str) -> None:
        """Bandwidth is a VARA HF command; hide it when the station is on FM."""
        visible = mode.upper() == "HF"
        self.vara_hf_bandwidth.setVisible(visible)
        self.vara_hf_bandwidth_label.setVisible(visible)

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
        if self.radio_backend.currentData() == "hamlib" and self.radio_model.currentData() < 1:
            errors.append(
                dual(
                    "Choose a supported radio model when Hamlib control is enabled.",
                    "Při řízení přes Hamlib vyberte podporovaný model rádia.",
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
        if cfg.radio_backend == "hamlib":
            cfg.radio = self.radio_model.currentText()
            cfg.rig_model = int(self.radio_model.currentData())
        else:
            cfg.radio = ""
            cfg.rig_model = 0
        cfg.cat_port = self.selected_cat_port()
        cfg.cat_baud = self.cat_baud.value()
        cfg.rigctld_host = self.rigctld_host.text().strip()
        cfg.rigctld_port = self.rigctld_port.value()
        cfg.rigctld_path = self.rigctld_path.text() or "rigctld"
        cfg.ptt_line = self.ptt_line.currentText()
        cfg.ptt_type = self.ptt_type.currentData()
        cfg.vara_ptt_delay_ms = self.vara_ptt_delay.value()
        cfg.audio_input = self.audio_input.currentText().strip()
        cfg.audio_output = self.audio_output.currentText().strip()
        cfg.vara_host = self.vara_host.text().strip()
        cfg.vara_fm_cmd_port = self.vara_fm_cmd.value()
        cfg.vara_fm_data_port = self.vara_fm_data.value()
        cfg.vara_hf_cmd_port = self.vara_hf_cmd.value()
        cfg.vara_hf_data_port = self.vara_hf_data.value()
        cfg.vara_fm_path = self.vara_fm_path.text()
        cfg.vara_hf_path = self.vara_hf_path.text()
        cfg.payload_backend = self.payload_backend.currentData()
        cfg.control_modem = self.control_modem.currentData()
        cfg.vara_hf_bandwidth = self.vara_hf_bandwidth.currentData()
        cfg.vara_host_ptt = self.vara_host_ptt.isChecked()
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
