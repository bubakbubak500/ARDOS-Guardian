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
    QDoubleSpinBox,
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

from ..config import StationConfig, radio_profile_name
from ..i18n import Language, dual, language, set_language, tr
from ..install.dependencies import find_vara_fm, find_vara_hf
from ..install.hamlib_installer import existing_rigctld
from ..modem.audio import match_device_name, scan_audio_devices
from ..ofdm import MCS_TABLE
from ..ofdm.adaptation import BURST_LADDER
from ..ofdm.coding import FEC_SPECS
from ..ofdm.config import PROFILE_LADDER, profile_or_default
from ..protocol import MAX_PTT_DELAY_MS, PTT_DELAY_STEP_MS
from ..radio.presets import CURATED, load_hamlib_models
from ..radio.usb_serial import list_serial_ports, port_device
from .theme import ThemePreference
from .inputs import UppercaseLineEdit, callsign_list
from .ofdm_labels import profile_rung_label

_CALLSIGN = re.compile(r"^[A-Z0-9/]{3,16}$")


def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
    widget = QSpinBox()
    widget.setRange(minimum, maximum)
    widget.setValue(value)
    return widget


class PathField(QWidget):
    """A path with a Browse button, and what detection found when it is empty.

    An empty field means "follow detection", not "nothing is installed" -- but
    it looked identical to a station whose VARA was genuinely missing. Showing
    the detected path keeps the field honest without pinning it: the value only
    becomes an override once the operator types or browses to one.
    """

    def __init__(
        self,
        value: str = "",
        executable: str = "*.exe",
        detected: str | None = None,
    ):
        super().__init__()
        self.executable = executable
        self.detected = detected
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.edit = QLineEdit(value)
        if detected:
            self.edit.setPlaceholderText(
                dual(
                    f"Detected automatically: {detected}",
                    f"Nalezeno automaticky: {detected}",
                )
            )
            self.edit.setToolTip(
                dual(
                    "Leave empty to keep following the detected installation. "
                    f"Guardian is using {detected}.",
                    "Ponechte prázdné, aby Guardian dál používal nalezenou "
                    f"instalaci. Nyní používá {detected}.",
                )
            )
        else:
            self.edit.setPlaceholderText(
                dual("Not detected", "Nenalezeno")
            )
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

    def setText(self, value: str) -> None:  # noqa: N802 - mirrors QLineEdit
        self.edit.setText(str(value or ""))


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
        self._build_payload_options()
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

        self.notify_incoming = QCheckBox(tr("settings.notify_incoming"))
        self.notify_incoming.setChecked(self.config.notify_incoming)
        self.notify_incoming.setToolTip(tr("settings.notify_incoming_hint"))
        self.notify_sound = QCheckBox(tr("settings.notify_sound"))
        self.notify_sound.setChecked(self.config.notify_sound)
        self.notify_sound.setToolTip(tr("settings.notify_sound_hint"))
        form.addRow(self.notify_incoming)
        form.addRow(self.notify_sound)

        # Recovery, not housekeeping: when the mailbox state itself is the
        # problem, the operator needs a way back to a clean slate.
        self.clear_mail_button = QPushButton(tr("settings.clear_mail"))
        self.clear_mail_button.setToolTip(tr("settings.clear_mail_hint"))
        self.clear_mail_button.clicked.connect(self._clear_mail)
        self.clear_mail_button.setEnabled(self.operations is not None)
        clear_row = QWidget()
        clear_layout = QHBoxLayout(clear_row)
        clear_layout.setContentsMargins(0, 12, 0, 0)
        clear_layout.setSpacing(6)
        clear_layout.addWidget(self.clear_mail_button)
        clear_layout.addStretch(1)
        form.addRow("", clear_row)

    def _clear_mail(self) -> None:
        if self.operations is None:
            return
        count = len(self.operations.mailstore.list())
        answer = QMessageBox.question(
            self,
            tr("settings.clear_mail"),
            tr("settings.clear_mail_confirm", count=count),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = self.operations.clear_mailstore()
        if removed < 0:
            QMessageBox.warning(
                self,
                tr("settings.clear_mail"),
                tr("settings.clear_mail_busy"),
            )
            return
        QMessageBox.information(
            self,
            tr("settings.clear_mail"),
            tr("settings.clear_mail_done", count=removed),
        )

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
        self.rigctld_path = PathField(
            self.config.rigctld_path,
            "rigctld.exe",
            detected=existing_rigctld(),
        )
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
        # A station used with more than one rig or cable re-entered nine fields
        # from memory every time it swapped. A profile is those fields under a
        # short name -- this page only, so picking one can never carry a
        # callsign, an audio device or a VARA port along with it.
        self.save_profile_button = QPushButton(
            dual("Save profile…", "Uložit profil…")
        )
        self.save_profile_button.setToolTip(
            dual(
                "Store the radio settings shown here under a short name.",
                "Uloží zde zobrazené nastavení rádia pod krátkým názvem.",
            )
        )
        self.save_profile_button.clicked.connect(self._save_radio_profile)
        self.radio_profile_picker = QComboBox()
        self.radio_profile_picker.setMinimumWidth(160)
        self.radio_profile_picker.setToolTip(
            dual(
                "Load a saved radio profile into the fields above. Nothing "
                "reaches the radio until Save or Apply.",
                "Načte uložený profil rádia do polí výše. Do rádia se nic "
                "nedostane, dokud nedáte Uložit nebo Použít.",
            )
        )
        self.radio_profile_picker.currentIndexChanged.connect(
            self._radio_profile_picked
        )
        self.delete_profile_button = QPushButton(tr("common.delete"))
        self.delete_profile_button.setToolTip(
            dual("Delete the selected profile.", "Odstraní vybraný profil.")
        )
        self.delete_profile_button.clicked.connect(self._delete_radio_profile)
        self._refresh_radio_profiles()

        test_row = QWidget()
        test_layout = QHBoxLayout(test_row)
        test_layout.setContentsMargins(0, 0, 0, 0)
        test_layout.setSpacing(6)
        test_layout.addWidget(self.ptt_test_button)
        test_layout.addWidget(self.save_profile_button)
        test_layout.addWidget(self.radio_profile_picker)
        test_layout.addWidget(self.delete_profile_button)
        test_layout.addStretch(1)
        form.addRow("", test_row)
        form.addRow("", self.ptt_status)

    # --- radio profiles --------------------------------------------------- #
    def _refresh_radio_profiles(self, selected: str = "") -> None:
        """Rebuild the picker, keeping a name selected when there is one."""
        names = self.config.radio_profile_names()
        self.radio_profile_picker.blockSignals(True)
        self.radio_profile_picker.clear()
        self.radio_profile_picker.addItem(
            dual("Saved profiles…", "Uložené profily…"), ""
        )
        for name in names:
            self.radio_profile_picker.addItem(name, name)
        index = self.radio_profile_picker.findData(selected) if selected else 0
        self.radio_profile_picker.setCurrentIndex(max(0, index))
        self.radio_profile_picker.blockSignals(False)
        self.delete_profile_button.setEnabled(bool(names))
        self.radio_profile_picker.setEnabled(bool(names))

    def _radio_form_values(self) -> dict:
        """The radio settings as the fields currently read them."""
        backend = self.radio_backend.currentData()
        hamlib = backend == "hamlib"
        return {
            "radio_backend": backend,
            "radio": self.radio_model.currentText() if hamlib else "",
            "rig_model": int(self.radio_model.currentData() or 0) if hamlib else 0,
            "cat_port": self.selected_cat_port(),
            "cat_baud": self.cat_baud.value(),
            "rigctld_host": self.rigctld_host.text().strip(),
            "rigctld_port": self.rigctld_port.value(),
            "rigctld_path": self.rigctld_path.text() or "rigctld",
            "ptt_type": self.ptt_type.currentData(),
            "ptt_line": self.ptt_line.currentText(),
            "vara_ptt_delay_ms": self.vara_ptt_delay.value(),
        }

    def _load_radio_form(self, values: dict) -> None:
        """Show a stored profile in the fields, leaving every other page alone."""
        backend = str(values.get("radio_backend", "none"))
        self.radio_backend.setCurrentIndex(
            max(0, self.radio_backend.findData(backend))
        )
        model = int(values.get("rig_model", 0) or 0)
        if model:
            index = self.radio_model.findData(model)
            if index < 0:
                # A radio picked from the full Hamlib list is not in the
                # curated combo; the profile carries its label for exactly this.
                self.radio_model.addItem(
                    str(values.get("radio") or model), model
                )
                index = self.radio_model.count() - 1
            self.radio_model.setCurrentIndex(index)
        else:
            self.radio_model.setCurrentIndex(0)
        port = port_device(str(values.get("cat_port", "")))
        labels = [
            self.cat_port.itemText(row) for row in range(self.cat_port.count())
        ]
        self.cat_port.setCurrentText(
            next((label for label in labels if port_device(label) == port), port)
        )
        self.cat_baud.setValue(int(values.get("cat_baud", 0) or 0))
        self.rigctld_host.setText(str(values.get("rigctld_host", "127.0.0.1")))
        self.rigctld_port.setValue(int(values.get("rigctld_port", 4532) or 4532))
        self.rigctld_path.setText(str(values.get("rigctld_path", "rigctld")))
        self.ptt_type.setCurrentIndex(
            max(0, self.ptt_type.findData(str(values.get("ptt_type", "RIG")).upper()))
        )
        self.ptt_line.setCurrentText(str(values.get("ptt_line", "RTS")))
        self.vara_ptt_delay.setValue(
            max(0, min(int(values.get("vara_ptt_delay_ms", 0) or 0), MAX_PTT_DELAY_MS))
        )

    def _save_radio_profile(self) -> None:
        suggestion = str(self.radio_profile_picker.currentData() or "")
        if not suggestion and self.radio_backend.currentData() == "hamlib":
            suggestion = self.radio_model.currentText()
        name, accepted = QInputDialog.getText(
            self,
            dual("Save radio profile", "Uložit profil rádia"),
            dual("Short name:", "Krátký název:"),
            text=radio_profile_name(str(suggestion)),
        )
        if not accepted:
            return
        key = radio_profile_name(name)
        if not key:
            self.ptt_status.setText(
                dual(
                    "A radio profile needs a name.",
                    "Profil rádia potřebuje název.",
                )
            )
            return
        known = key in self.config.radio_profiles
        self.config.radio_profiles[key] = dict(self._radio_form_values())
        # Saved on the spot: a profile the operator has just named must not
        # depend on them also pressing Save on the way out.
        self.config.save()
        self._refresh_radio_profiles(selected=key)
        self.ptt_status.setText(
            dual(
                f"Radio profile '{key}' {'replaced' if known else 'saved'}.",
                f"Profil rádia „{key}“ byl {'nahrazen' if known else 'uložen'}.",
            )
        )

    def _radio_profile_picked(self, _index: int) -> None:
        name = str(self.radio_profile_picker.currentData() or "")
        stored = self.config.radio_profiles.get(name)
        if not name or not isinstance(stored, dict):
            return
        self._load_radio_form(stored)
        self.ptt_status.setText(
            dual(
                f"Radio profile '{name}' loaded. Save or Apply to use it.",
                f"Profil rádia „{name}“ načten. Použijte Uložit nebo Použít.",
            )
        )

    def _delete_radio_profile(self) -> None:
        name = str(self.radio_profile_picker.currentData() or "")
        if not name or not self.config.delete_radio_profile(name):
            return
        self.config.save()
        self._refresh_radio_profiles()
        self.ptt_status.setText(
            dual(
                f"Radio profile '{name}' deleted.",
                f"Profil rádia „{name}“ byl odstraněn.",
            )
        )

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
                "Choose what carries the message payload. VARA P2P drives the "
                "vendor modem over TCP; Guardian OFDM VHF is an experimental "
                "built-in modem that uses the soundcard and Guardian's own "
                "keying. An empty executable field follows detection; the grey "
                "text is the path Guardian uses.",
                "Zvolte, co přenáší obsah zprávy. VARA P2P řídí modem "
                "dodavatele přes TCP; Guardian OFDM VHF je experimentální "
                "vestavěný modem, který používá zvukovou kartu a vlastní "
                "klíčování Guardianu. Prázdné pole s programem se řídí "
                "detekcí; šedý text je cesta, kterou Guardian používá.",
            ),
        )
        self.vara_mode = QComboBox()
        self.vara_mode.addItems(["FM", "HF"])
        self.vara_mode.setCurrentText(self.config.vara_mode)
        # VARA P2P stays item 0: it is the default transport and the only one
        # proven on air. The saved value is restored rather than assumed --
        # while there was one item the hard-set index hid the fact that the
        # dialog never read config.payload_backend back at all.
        self.payload_backend = QComboBox()
        self.payload_backend.addItem("Guardian VARA P2P", "vara_p2p")
        self.payload_backend.addItem(
            dual(
                "Guardian OFDM VHF (Experimental)",
                "Guardian OFDM VHF (Experimentální)",
            ),
            "ofdm_vhf",
        )
        self.payload_backend.setCurrentIndex(
            max(0, self.payload_backend.findData(self.config.payload_backend))
        )
        self.vara_host = QLineEdit(self.config.vara_host)
        self.vara_fm_cmd = _spin(1, 65_535, self.config.vara_fm_cmd_port)
        self.vara_fm_data = _spin(1, 65_535, self.config.vara_fm_data_port)
        self.vara_hf_cmd = _spin(1, 65_535, self.config.vara_hf_cmd_port)
        self.vara_hf_data = _spin(1, 65_535, self.config.vara_hf_data_port)
        self.vara_fm_path = PathField(
            self.config.vara_fm_path,
            "VARAFM.exe",
            detected=find_vara_fm(),
        )
        self.vara_hf_path = PathField(
            self.config.vara_hf_path,
            "VARA.exe",
            detected=find_vara_hf(),
        )
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
        self._build_ofdm_widgets()
        # Both stay visible for either payload: the VARA flavour and the
        # control-burst modem describe the control plane, which every transport
        # rides on, and the flavour still picks the band Guardian works in.
        form.addRow(dual("Active VARA mode", "Aktivní režim VARA"), self.vara_mode)
        form.addRow(dual("Payload workflow", "Způsob přenosu"), self.payload_backend)
        self._vara_only_rows: list[tuple[QLabel | None, QWidget]] = []
        for text, widget in (
            (dual("VARA host", "Adresa VARA"), self.vara_host),
            (dual("VARA FM command port", "Příkazový port VARA FM"), self.vara_fm_cmd),
            (dual("VARA FM data port", "Datový port VARA FM"), self.vara_fm_data),
            (dual("VARA FM executable", "Program VARA FM"), self.vara_fm_path),
            (dual("VARA HF command port", "Příkazový port VARA HF"), self.vara_hf_cmd),
            (dual("VARA HF data port", "Datový port VARA HF"), self.vara_hf_data),
            (dual("VARA HF executable", "Program VARA HF"), self.vara_hf_path),
        ):
            label = QLabel(text)
            form.addRow(label, widget)
            self._vara_only_rows.append((label, widget))
        self.vara_hf_bandwidth_label = QLabel(
            dual("VARA HF bandwidth", "Šířka pásma VARA HF")
        )
        form.addRow(self.vara_hf_bandwidth_label, self.vara_hf_bandwidth)
        self.vara_mode.currentTextChanged.connect(self._sync_bandwidth_row)
        self._ofdm_only_rows: list[tuple[QLabel | None, QWidget]] = []
        for text, widget in (
            (
                dual("OFDM waveform profile", "Profil vlnového průběhu OFDM"),
                self.ofdm_profile,
            ),
            (dual("OFDM modulation (MCS)", "Modulace OFDM (MCS)"), self.ofdm_mcs),
            (dual("Adaptive FEC", "Adaptivní FEC"), self.ofdm_adaptive_fec),
            (dual("Fixed FEC rate", "Pevná rychlost FEC"), self.ofdm_fec),
            (dual("Adaptive burst length", "Adaptivní délka dávky"),
             self.ofdm_adaptive_burst),
            (dual("Fixed burst target", "Pevná cílová dávka"),
             self.ofdm_burst_bytes),
            (dual("Minimum adaptive burst", "Nejmenší adaptivní dávka"),
             self.ofdm_min_burst_bytes),
            (dual("Maximum adaptive burst", "Největší adaptivní dávka"),
             self.ofdm_max_burst_bytes),
            (dual("Selective-ARQ sub-block", "Podblok selektivního ARQ"),
             self.ofdm_arq_block_bytes),
            (dual("Timeout scale", "Násobek časového limitu"),
             self.ofdm_timeout_multiplier),
            (dual("Legacy comparison mode", "Starší srovnávací režim"),
             self.ofdm_legacy_mode),
            (
                dual("Keying lead before transmit", "Předstih klíčování"),
                self.ofdm_tx_lead,
            ),
            (
                dual("Keying tail after transmit", "Doběh klíčování"),
                self.ofdm_tx_tail,
            ),
            (
                dual("Retransmissions per block", "Opakování jednoho bloku"),
                self.ofdm_max_retries,
            ),
            (
                dual("Waveform in use", "Použitý vlnový průběh"),
                self.ofdm_summary,
            ),
        ):
            label = QLabel(text)
            form.addRow(label, widget)
            self._ofdm_only_rows.append((label, widget))
        # Full width and no caption of its own: it is advice about the choice
        # above it, not another field.
        form.addRow(self.ofdm_profile_hint)
        self._ofdm_only_rows.append((None, self.ofdm_profile_hint))
        form.addRow(dual("Control-burst modem", "Modem řídicích rámců"), self.control_modem)

        form.addRow(self.vara_host_ptt)
        # Guardian keying VARA is a VARA-only arrangement: the OFDM modem always
        # keys the radio itself, so the checkbox would promise a choice there is
        # none of. No label of its own, hence the None.
        self._vara_only_rows.append((None, self.vara_host_ptt))
        self.payload_backend.currentIndexChanged.connect(self._sync_payload_rows)
        self._sync_payload_rows()

    def _ofdm_summary_text(self, waveform) -> str:
        """The resolved waveform in one paragraph, read-only by design."""
        low, high = waveform.occupied_band
        return dual(
            f"{waveform.name} (experimental) · sample rate "
            f"{waveform.sample_rate} Hz · occupied {low:.0f}–{high:.0f} Hz "
            f"({waveform.occupied_bandwidth:.0f} Hz) · FFT {waveform.fft_size} "
            f"· cyclic prefix {waveform.cp_length} · {waveform.num_carriers} "
            f"active carriers ({waveform.num_data_carriers} data + "
            f"{waveform.num_pilots} pilot) · spacing "
            f"{waveform.subcarrier_spacing:.1f} Hz · symbol "
            f"{waveform.symbol_duration * 1000:.1f} ms",
            f"{waveform.name} (experimentální) · vzorkování "
            f"{waveform.sample_rate} Hz · zabírá {low:.0f}–{high:.0f} Hz "
            f"({waveform.occupied_bandwidth:.0f} Hz) · FFT {waveform.fft_size} "
            f"· ochranný interval {waveform.cp_length} · {waveform.num_carriers} "
            f"aktivních nosných ({waveform.num_data_carriers} datových + "
            f"{waveform.num_pilots} pilotních) · rozestup "
            f"{waveform.subcarrier_spacing:.1f} Hz · symbol "
            f"{waveform.symbol_duration * 1000:.1f} ms",
        )

    def _build_ofdm_widgets(self) -> None:
        """Editable OFDM knobs plus a read-only view of the resolved waveform.

        The profile is chosen from the ladder in guardian/ofdm/config.py, in
        ascending occupied bandwidth -- that is the order the rungs are meant to
        be tried in, and it is what makes the choice answerable: how much audio
        bandwidth a given radio's receive path passes can only be found out by
        trying. Which is why the individual DSP numbers are still not fields.

        FFT size, cyclic prefix, carrier set and sample rate stay read-only:
        they belong to the profile as a reviewed, named, testable whole, and are
        never something an operator can drag out of a working waveform. What the
        picker offers is a set of such wholes; the summary below it says what the
        chosen one is.
        """
        self.ofdm_profile = QComboBox()
        # PROFILE_LADDER, not profile_names(): the ladder is ordered by occupied
        # bandwidth. Alphabetical order would list NARROW_1K2 between the WIDEs,
        # which reads as nonsense in a picker whose whole point is the sequence.
        for name in PROFILE_LADDER:
            entry = profile_or_default(name)
            self.ofdm_profile.addItem(profile_rung_label(entry), name)
        self.ofdm_profile.setCurrentIndex(
            max(0, self.ofdm_profile.findData(self.config.ofdm_profile))
        )
        self.ofdm_profile.currentIndexChanged.connect(self._sync_ofdm_summary)
        self.ofdm_profile.setToolTip(dual(
            "Both stations must use the same profile. Wider carries more per "
            "second but needs a stronger signal for it: the same transmit power "
            "spread over twice the carriers is 3 dB less on each one.",
            "Obě stanice musí použít stejný profil. Širší přenese více za "
            "sekundu, ale potřebuje k tomu silnější signál: stejný vysílací "
            "výkon rozložený na dvojnásobek nosných je na každé z nich o 3 dB "
            "slabší.",
        ))
        self.ofdm_profile_hint = QLabel()
        self.ofdm_profile_hint.setObjectName("Metadata")
        self.ofdm_profile_hint.setWordWrap(True)
        self.ofdm_mcs = QComboBox()
        for scheme in MCS_TABLE:
            self.ofdm_mcs.addItem(scheme.label, scheme.index)
        self.ofdm_mcs.setCurrentIndex(
            max(0, self.ofdm_mcs.findData(self.config.ofdm_mcs))
        )
        self.ofdm_adaptive_fec = QCheckBox(dual(
            "AUTO — learn from delivery results",
            "AUTO — učit se z výsledků doručení",
        ))
        self.ofdm_adaptive_fec.setChecked(self.config.ofdm_adaptive_fec)
        self.ofdm_fec = QComboBox()
        for spec in FEC_SPECS:
            self.ofdm_fec.addItem(f"FEC {spec.label}", spec.label)
        self.ofdm_fec.setCurrentIndex(
            max(0, self.ofdm_fec.findData(self.config.ofdm_fec))
        )
        self.ofdm_adaptive_burst = QCheckBox(dual(
            "AUTO — grow only after clean bursts",
            "AUTO — zvětšovat jen po čistých dávkách",
        ))
        self.ofdm_adaptive_burst.setChecked(self.config.ofdm_adaptive_burst)

        def burst_picker(value: int) -> QComboBox:
            picker = QComboBox()
            for size in BURST_LADDER:
                picker.addItem(f"{size} B" if size < 1024 else f"{size // 1024} KiB", size)
            picker.setCurrentIndex(max(0, picker.findData(value)))
            return picker

        self.ofdm_burst_bytes = burst_picker(self.config.ofdm_burst_bytes)
        self.ofdm_min_burst_bytes = burst_picker(self.config.ofdm_min_burst_bytes)
        self.ofdm_max_burst_bytes = burst_picker(self.config.ofdm_max_burst_bytes)
        self.ofdm_arq_block_bytes = QComboBox()
        for size in (256, 512, 1024):
            self.ofdm_arq_block_bytes.addItem(
                f"{size} B" if size < 1024 else "1 KiB", size
            )
        self.ofdm_arq_block_bytes.setCurrentIndex(max(
            0, self.ofdm_arq_block_bytes.findData(self.config.ofdm_arq_block_bytes)
        ))
        self.ofdm_timeout_multiplier = QDoubleSpinBox()
        self.ofdm_timeout_multiplier.setRange(0.5, 4.0)
        self.ofdm_timeout_multiplier.setSingleStep(0.1)
        self.ofdm_timeout_multiplier.setDecimals(1)
        self.ofdm_timeout_multiplier.setSuffix("×")
        self.ofdm_timeout_multiplier.setValue(self.config.ofdm_timeout_multiplier)
        self.ofdm_timeout_multiplier.setToolTip(dual(
            "Scales duration-derived ACK and receive deadlines. Leave at 1.0 "
            "unless radio turnaround measurements require more margin.",
            "Násobí časové limity odvozené z délky rámce. Hodnotu 1,0 měňte "
            "jen pokud měření přepínání rádia vyžaduje větší rezervu.",
        ))
        self.ofdm_legacy_mode = QCheckBox(dual(
            "Use version-1 512 B stop-and-wait for comparison/debugging",
            "Použít verzi 1 s 512 B a potvrzením každého bloku pro srovnání",
        ))
        self.ofdm_legacy_mode.setChecked(self.config.ofdm_legacy_mode)
        self.ofdm_tx_lead = _spin(0, 3_000, self.config.ofdm_tx_lead_ms)
        self.ofdm_tx_lead.setSuffix(dual(" ms", " ms"))
        self.ofdm_tx_lead.setToolTip(dual(
            "Silence held after the transmitter is keyed, before the waveform "
            "starts. A cheap handheld needs the whole lead or the preamble "
            "goes out while the PA is still coming up.",
            "Ticho po zaklíčování vysílače, než začne vlnový průběh. Levná "
            "ručka potřebuje celý předstih, jinak preambule odejde ještě "
            "během rozběhu koncového stupně.",
        ))
        self.ofdm_tx_tail = _spin(0, 2_000, self.config.ofdm_tx_tail_ms)
        self.ofdm_tx_tail.setSuffix(dual(" ms", " ms"))
        self.ofdm_max_retries = _spin(0, 20, self.config.ofdm_max_retries)
        self.ofdm_summary = QLabel()
        self.ofdm_summary.setObjectName("Metadata")
        self.ofdm_summary.setWordWrap(True)
        self.ofdm_adaptive_fec.toggled.connect(self._sync_ofdm_controls)
        self.ofdm_adaptive_burst.toggled.connect(self._sync_ofdm_controls)
        self.ofdm_legacy_mode.toggled.connect(self._sync_ofdm_controls)
        for picker in (
            self.ofdm_fec, self.ofdm_burst_bytes, self.ofdm_min_burst_bytes,
            self.ofdm_max_burst_bytes, self.ofdm_arq_block_bytes,
        ):
            picker.currentIndexChanged.connect(self._sync_ofdm_summary)
        self._sync_ofdm_controls()

    def _sync_ofdm_controls(self) -> None:
        legacy = self.ofdm_legacy_mode.isChecked()
        self.ofdm_adaptive_fec.setEnabled(not legacy)
        self.ofdm_fec.setEnabled(not legacy and not self.ofdm_adaptive_fec.isChecked())
        self.ofdm_adaptive_burst.setEnabled(not legacy)
        self.ofdm_burst_bytes.setEnabled(
            not legacy and not self.ofdm_adaptive_burst.isChecked()
        )
        self.ofdm_min_burst_bytes.setEnabled(
            not legacy and self.ofdm_adaptive_burst.isChecked()
        )
        self.ofdm_max_burst_bytes.setEnabled(
            not legacy and self.ofdm_adaptive_burst.isChecked()
        )
        self.ofdm_arq_block_bytes.setEnabled(not legacy)
        self._sync_ofdm_summary()

    def _sync_ofdm_summary(self) -> None:
        """Restate the waveform whenever the profile picker moves.

        Without this the summary would keep describing whatever was saved, which
        is worse than having no summary: it would contradict the picker sitting
        directly above it.
        """
        waveform = profile_or_default(str(self.ofdm_profile.currentData()))
        if self.ofdm_legacy_mode.isChecked():
            mode = dual(
                "Protocol v1 compatibility: 512 B stop-and-wait, FEC 1/2.",
                "Kompatibilita protokolu v1: 512 B, jednotlivá potvrzení, FEC 1/2.",
            )
        else:
            fec = ("AUTO" if self.ofdm_adaptive_fec.isChecked()
                   else f"fixed {self.ofdm_fec.currentData()}")
            burst = (f"AUTO {self.ofdm_min_burst_bytes.currentData()}–"
                     f"{self.ofdm_max_burst_bytes.currentData()} B"
                     if self.ofdm_adaptive_burst.isChecked()
                     else f"fixed {self.ofdm_burst_bytes.currentData()} B")
            mode = dual(
                f"Protocol v2 selective repeat · FEC {fec} · burst {burst} · "
                f"ARQ {self.ofdm_arq_block_bytes.currentData()} B.",
                f"Protokol v2 se selektivním opakováním · FEC {fec} · dávka "
                f"{burst} · ARQ {self.ofdm_arq_block_bytes.currentData()} B.",
            )
        self.ofdm_summary.setText(f"{mode}\n{self._ofdm_summary_text(waveform)}")
        # A rung that needs a faster sound card is a failure the operator can
        # anticipate at the moment of choosing rather than debug afterwards.
        if waveform.sample_rate != 48000:
            self.ofdm_profile_hint.setText(dual(
                f"{waveform.name} needs a sound card that will open at "
                f"{waveform.sample_rate} Hz. If yours will not, the ladder "
                "stops one rung lower. No rung has been measured on the air; "
                "both stations must be set to the same one.",
                f"{waveform.name} vyžaduje zvukovou kartu, která se otevře na "
                f"{waveform.sample_rate} Hz. Pokud to vaše neumí, škála končí o "
                "jeden stupeň níž. Žádný stupeň nebyl změřen na pásmu; obě "
                "stanice musí být nastaveny na tentýž.",
            ))
        else:
            self.ofdm_profile_hint.setText(dual(
                "No rung has been measured on the air — they exist to be tried, "
                "starting narrow and widening while the link still holds. Both "
                "stations must be set to the same one.",
                "Žádný stupeň nebyl změřen na pásmu — existují proto, aby se "
                "vyzkoušely: začněte úzkým a rozšiřujte, dokud spoj drží. Obě "
                "stanice musí být nastaveny na tentýž.",
            ))

    def _sync_payload_rows(self) -> None:
        """Show only the rows the selected payload transport actually uses.

        A station on OFDM VHF never launches VARA, so leaving four TCP ports and
        two executable pickers on the page invites the operator to maintain
        settings that reach nothing.
        """
        ofdm = self.payload_backend.currentData() == "ofdm_vhf"
        for label, widget in self._vara_only_rows:
            widget.setVisible(not ofdm)
            if label is not None:
                label.setVisible(not ofdm)
        for label, widget in self._ofdm_only_rows:
            widget.setVisible(ofdm)
            if label is not None:
                label.setVisible(ofdm)
        self._sync_bandwidth_row(self.vara_mode.currentText())

    def _sync_bandwidth_row(self, mode: str) -> None:
        """Bandwidth is a VARA HF command; hide it when the station is on FM."""
        visible = (
            mode.upper() == "HF"
            and self.payload_backend.currentData() != "ofdm_vhf"
        )
        self.vara_hf_bandwidth.setVisible(visible)
        self.vara_hf_bandwidth_label.setVisible(visible)

    def _build_payload_options(self) -> None:
        form = self._page(
            dual("Compression & identification", "Komprese a identifikace"),
            dual(
                "Choose one lossless compression layer. VARA FILES belongs to "
                "the vendor modem; Guardian compression works identically over "
                "VARA and OFDM and keeps the smallest safely decodable candidate. "
                "Encryption is VARA's commercial/non-amateur AES mode.",
                "Zvolte jednu bezeztrátovou kompresní vrstvu. VARA FILES patří "
                "modemu dodavatele; komprese Guardian funguje shodně přes VARA i "
                "OFDM a ponechá nejmenší bezpečně rozbalitelnou variantu. "
                "Šifrování je komerční/neamatérský režim AES modemu VARA.",
            ),
        )
        self.vara_file_compression = QCheckBox(dual(
            "Use native VARA FILES compression",
            "Použít nativní kompresi VARA FILES",
        ))
        self.vara_file_compression.setChecked(self.config.vara_file_compression)
        self.vara_file_compression.setToolTip(dual(
            "Switches VARA from its permanent COMPRESSION TEXT baseline to "
            "COMPRESSION FILES for Guardian bundles.",
            "Přepne VARA ze stálého základu COMPRESSION TEXT na COMPRESSION "
            "FILES pro balíčky Guardianu.",
        ))
        self.guardian_compression = QCheckBox(dual(
            "Use adaptive Guardian compression (VARA and OFDM)",
            "Použít adaptivní kompresi Guardian (VARA i OFDM)",
        ))
        self.guardian_compression.setChecked(self.config.guardian_compression)
        self.guardian_compression.setToolTip(dual(
            "Losslessly compares stored, DEFLATE, BZIP2, LZMA, Guardian's "
            "per-entry mix, ZPAQ, PAQ8PX and LPAQ8. High-ratio codecs must pass "
            "a timed verification decode; no LLM, cloud or lossy conversion.",
            "Bezeztrátově porovná ZIP varianty bez komprese, DEFLATE, BZIP2 a "
            "LZMA, vlastní smíšenou strategii, ZPAQ, PAQ8PX a LPAQ8. Vysoce účinné "
            "kodeky musí projít časově omezeným ověřovacím rozbalením; bez LLM, "
            "cloudu a ztrátového převodu.",
        ))

        self.vara_encryption = QCheckBox(dual(
            "Enable VARA AES-256 encryption",
            "Zapnout šifrování VARA AES-256",
        ))
        self.vara_encryption.setChecked(self.config.vara_encryption)
        self.vara_encryption_password = QLineEdit(self.config.vara_encryption_password)
        self.vara_encryption_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.vara_encryption_password.setMaxLength(32)
        self.vara_encryption_password.setPlaceholderText(
            dual("1–32 letters or digits", "1–32 písmen nebo číslic")
        )
        self.vara_encryption_password.setToolTip(dual(
            "VARA stores the same fixed key in its own INI file. Both stations "
            "must use the same key.",
            "VARA ukládá stejný pevný klíč do svého INI souboru. Obě stanice "
            "musí použít shodný klíč.",
        ))
        encryption_warning = QLabel(dual(
            "Use encryption only on services and frequencies where it is "
            "authorised. VARA restricts this feature to non-amateur/commercial use.",
            "Šifrování používejte pouze ve službách a na kmitočtech, kde je "
            "povoleno. VARA tuto funkci omezuje na neamatérské/komerční použití.",
        ))
        encryption_warning.setObjectName("Metadata")
        encryption_warning.setWordWrap(True)

        self.morse_id_after_ack = QCheckBox(dual(
            "After the final ACK, send both callsigns in Morse at 50 WPM",
            "Po posledním ACK odvysílat obě značky Morse rychlostí 50 WPM",
        ))
        self.morse_id_after_ack.setChecked(self.config.morse_id_after_ack)
        self.morse_id_after_ack.setToolTip(dual(
            "Only the final destination transmits once, after its RECEIVED and "
            "DELIVERED control frames: SENDER DE RECEIVER.",
            "Pouze cílová stanice odvysílá jednou po rámcích RECEIVED a "
            "DELIVERED: ODESÍLATEL DE PŘÍJEMCE.",
        ))

        form.addRow(self.vara_file_compression)
        form.addRow(self.guardian_compression)
        form.addRow(self.vara_encryption)
        form.addRow(dual("VARA fixed encryption key", "Pevný šifrovací klíč VARA"),
                    self.vara_encryption_password)
        form.addRow(encryption_warning)
        form.addRow(self.morse_id_after_ack)

        self.vara_file_compression.toggled.connect(self._native_compression_toggled)
        self.guardian_compression.toggled.connect(self._guardian_compression_toggled)
        self.vara_encryption.toggled.connect(
            self.vara_encryption_password.setEnabled
        )
        if self.vara_file_compression.isChecked():
            self._native_compression_toggled(True)
        elif self.guardian_compression.isChecked():
            self._guardian_compression_toggled(True)
        self.vara_encryption_password.setEnabled(self.vara_encryption.isChecked())

    def _native_compression_toggled(self, checked: bool) -> None:
        if checked:
            self.guardian_compression.setChecked(False)
        self.guardian_compression.setEnabled(not checked)

    def _guardian_compression_toggled(self, checked: bool) -> None:
        if checked:
            self.vara_file_compression.setChecked(False)
        self.vara_file_compression.setEnabled(not checked)

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
        self.separate_working_channels = QCheckBox(
            tr("settings.separate_working_channels")
        )
        self.separate_working_channels.setChecked(
            self.config.separate_working_channels
        )
        self.separate_working_channels.setToolTip(
            tr("settings.separate_working_channels_hint")
        )
        self.separate_working_channels.toggled.connect(
            lambda enabled: self.auto_qsy.setChecked(True) if enabled else None
        )
        self.beacon_enabled = QCheckBox(
            dual("Transmit presence beacons", "Vysílat majáky přítomnosti")
        )
        self.beacon_enabled.setChecked(self.config.beacon_enabled)
        self.beacon_interval = _spin(15, 86_400, int(self.config.beacon_interval))
        self.scan_dwell = _spin(1, 300, int(self.config.scan_dwell))
        # Bounds for the multi-hop discovery plane. They are set once for the
        # station and belong with the rest of its network behaviour; Network →
        # Route discovery keeps only what an operator touches while working.
        self.discovery_forward = QCheckBox(tr("network.discovery_forward"))
        self.discovery_forward.setChecked(self.config.discovery_forward)
        self.discovery_ttl = _spin(2, 8, self.config.discovery_ttl)
        self.discovery_lifetime = _spin(
            1, 1440, max(1, int(self.config.discovery_route_lifetime / 60))
        )
        self.discovery_lifetime.setSuffix(dual(" min", " min"))
        self.discovery_budget = _spin(1, 120, self.config.discovery_frame_budget)
        self.discovery_budget.setSuffix(tr("network.discovery_frames_minute_suffix"))
        self.discovery_allowlist = UppercaseLineEdit(
            ", ".join(self.config.discovery_allowlist)
        )
        self.discovery_denylist = UppercaseLineEdit(
            ", ".join(self.config.discovery_denylist)
        )
        form.addRow(dual("Default hop limit (TTL)", "Výchozí limit skoků (TTL)"), self.default_ttl)
        form.addRow(self.auto_route)
        form.addRow(self.auto_relay)
        form.addRow(self.auto_deliver)
        form.addRow(self.auto_qsy)
        form.addRow(self.separate_working_channels)
        form.addRow(self.beacon_enabled)
        form.addRow(dual("Beacon interval (seconds)", "Interval majáku (sekundy)"), self.beacon_interval)
        form.addRow(dual("Channel scan dwell (seconds)", "Doba poslechu kanálu (sekundy)"), self.scan_dwell)
        form.addRow(self.discovery_forward)
        form.addRow(tr("network.discovery_ttl"), self.discovery_ttl)
        form.addRow(tr("network.discovery_lifetime"), self.discovery_lifetime)
        form.addRow(tr("network.discovery_budget"), self.discovery_budget)
        form.addRow(tr("network.discovery_allowlist"), self.discovery_allowlist)
        form.addRow(tr("network.discovery_denylist"), self.discovery_denylist)

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
        checked_paths = [("rigctld", self.rigctld_path)]
        # Only complain about a VARA executable the station is going to launch.
        # The rows are hidden under OFDM VHF, so a stale path left over from a
        # VARA setup would have refused every save with an error pointing at a
        # field the operator cannot see.
        if self.payload_backend.currentData() == "vara_p2p":
            checked_paths += [
                ("VARA FM", self.vara_fm_path),
                ("VARA HF", self.vara_hf_path),
            ]
        if self.payload_backend.currentData() == "ofdm_vhf":
            arq = int(self.ofdm_arq_block_bytes.currentData())
            fixed = int(self.ofdm_burst_bytes.currentData())
            minimum = int(self.ofdm_min_burst_bytes.currentData())
            maximum = int(self.ofdm_max_burst_bytes.currentData())
            if minimum > maximum:
                errors.append(dual(
                    "Minimum OFDM burst cannot exceed the maximum.",
                    "Nejmenší dávka OFDM nesmí překročit největší.",
                ))
            if fixed < arq or minimum < arq:
                errors.append(dual(
                    "An OFDM burst must be large enough to hold one ARQ sub-block.",
                    "Dávka OFDM musí pojmout alespoň jeden podblok ARQ.",
                ))
        password = self.vara_encryption_password.text().strip()
        if self.vara_encryption.isChecked() and not re.fullmatch(r"[A-Za-z0-9]{1,32}", password):
            errors.append(dual(
                "VARA encryption requires a 1–32 character key containing only letters and digits.",
                "Šifrování VARA vyžaduje klíč o 1–32 znacích tvořený pouze písmeny a číslicemi.",
            ))
        if self.vara_file_compression.isChecked() and self.guardian_compression.isChecked():
            errors.append(dual(
                "Choose VARA FILES compression or Guardian compression, not both.",
                "Zvolte kompresi VARA FILES, nebo kompresi Guardian, nikoli obě.",
            ))
        for label, field in checked_paths:
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
        cfg.notify_incoming = self.notify_incoming.isChecked()
        cfg.notify_sound = self.notify_sound.isChecked()
        # The same reading of the radio page a profile stores, so what gets
        # saved under a name and what reaches the station cannot drift apart.
        for field_name, value in self._radio_form_values().items():
            setattr(cfg, field_name, value)
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
        cfg.ofdm_profile = str(self.ofdm_profile.currentData())
        cfg.ofdm_mcs = int(self.ofdm_mcs.currentData())
        cfg.ofdm_tx_lead_ms = self.ofdm_tx_lead.value()
        cfg.ofdm_tx_tail_ms = self.ofdm_tx_tail.value()
        cfg.ofdm_max_retries = self.ofdm_max_retries.value()
        cfg.ofdm_adaptive_fec = self.ofdm_adaptive_fec.isChecked()
        cfg.ofdm_fec = str(self.ofdm_fec.currentData())
        cfg.ofdm_adaptive_burst = self.ofdm_adaptive_burst.isChecked()
        cfg.ofdm_burst_bytes = int(self.ofdm_burst_bytes.currentData())
        cfg.ofdm_min_burst_bytes = int(self.ofdm_min_burst_bytes.currentData())
        cfg.ofdm_max_burst_bytes = int(self.ofdm_max_burst_bytes.currentData())
        cfg.ofdm_arq_block_bytes = int(self.ofdm_arq_block_bytes.currentData())
        cfg.ofdm_timeout_multiplier = self.ofdm_timeout_multiplier.value()
        cfg.ofdm_legacy_mode = self.ofdm_legacy_mode.isChecked()
        cfg.control_modem = self.control_modem.currentData()
        cfg.vara_hf_bandwidth = self.vara_hf_bandwidth.currentData()
        cfg.vara_host_ptt = self.vara_host_ptt.isChecked()
        cfg.vara_file_compression = self.vara_file_compression.isChecked()
        cfg.vara_encryption = self.vara_encryption.isChecked()
        cfg.vara_encryption_password = self.vara_encryption_password.text().strip()
        cfg.guardian_compression = self.guardian_compression.isChecked()
        cfg.morse_id_after_ack = self.morse_id_after_ack.isChecked()
        cfg.apply_vara_mode(self.vara_mode.currentText())
        cfg.default_ttl = self.default_ttl.value()
        cfg.auto_route = self.auto_route.isChecked()
        cfg.auto_relay = self.auto_relay.isChecked()
        cfg.auto_deliver = self.auto_deliver.isChecked()
        cfg.auto_qsy = self.auto_qsy.isChecked()
        cfg.separate_working_channels = self.separate_working_channels.isChecked()
        cfg.beacon_enabled = self.beacon_enabled.isChecked()
        cfg.beacon_interval = float(self.beacon_interval.value())
        cfg.scan_dwell = float(self.scan_dwell.value())
        cfg.discovery_forward = self.discovery_forward.isChecked()
        cfg.discovery_ttl = self.discovery_ttl.value()
        cfg.discovery_route_lifetime = float(self.discovery_lifetime.value() * 60)
        cfg.discovery_frame_budget = self.discovery_budget.value()
        cfg.discovery_allowlist = callsign_list(self.discovery_allowlist.text())
        cfg.discovery_denylist = callsign_list(self.discovery_denylist.text())
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
