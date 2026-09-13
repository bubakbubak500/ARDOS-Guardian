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
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import (
    SC_FTN_BANDWIDTHS,
    SC_FTN_WAVEFORM,
    StationConfig,
    radio_profile_name,
)
from ..ofdm.automatic import automatic_g2_policy
from ..waveforms.config import profile_for
from ..i18n import Language, dual, language, set_language, tr
from ..install.dependencies import find_vara_fm, find_vara_hf
from ..install.hamlib_installer import existing_rigctld
from ..modem.audio import match_device_name, scan_audio_devices
from ..protocol import MAX_PTT_DELAY_MS, PTT_DELAY_STEP_MS
from ..radio.presets import CURATED, load_hamlib_models
from ..radio.usb_serial import list_serial_ports, port_device
from .theme import ThemePreference
from .inputs import UppercaseLineEdit, callsign_list
from .window_geometry import fit_dialog_to_screen

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
        # The previous fixed floor put the Save/Cancel row below the work area
        # on a 1366x768 monitor at 125% scaling.  Each tab owns its vertical
        # scroll area and this readable floor is capped to the actual monitor.
        self._tab_scrolls: list[QScrollArea] = []

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
        fit_dialog_to_screen(
            self,
            preferred_size=(960, 650),
            minimum_size=(680, 420),
        )

    def _page(self, title: str, description: str) -> QFormLayout:
        page = QWidget()
        page.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Preferred,
        )
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
        scroll = QScrollArea()
        scroll.setObjectName("SettingsPageScroll")
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        scroll.setWidget(page)
        self._tab_scrolls.append(scroll)
        self.tabs.addTab(scroll, title)
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
        self.radio_backend.addItem(
            "Guardian K5FW / AIOC UART", "guardian_k5"
        )
        self.radio_backend.addItem(
            "Guardian K61FW / AIOC UART", "guardian_k61"
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
        self.guardian_radio_model = QLineEdit(self.config.radio)
        self.guardian_radio_model.setPlaceholderText(
            dual("K5 / K61 radio model", "Model rádia K5 / K61")
        )
        self.vox_radio_model = QLineEdit(self.config.radio)
        self.vox_radio_model.setPlaceholderText(
            dual("VOX radio model (optional)", "Model rádia VOX (volitelné)")
        )
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
        self.guardian_ptt_mode = QComboBox()
        self.guardian_ptt_mode.addItems(["AIOC", "RTS", "DTR"])
        self.guardian_ptt_mode.setCurrentText(
            str(getattr(self.config, "guardian_ptt_mode", "AIOC") or "AIOC").upper()
        )
        self.guardian_ptt_mode.setToolTip(
            dual(
                "AIOC uses the safe DTR && !RTS sequence. RTS and DTR are "
                "available for interfaces wired as a single active-high line.",
                "AIOC používá bezpečnou sekvenci DTR && !RTS. RTS a DTR jsou "
                "pro rozhraní zapojená jako jediná aktivní linka.",
            )
        )
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
        self.guardian_radio_model_label = QLabel(
            dual("Guardian radio model", "Model rádia Guardian")
        )
        form.addRow(self.guardian_radio_model_label, self.guardian_radio_model)
        self.vox_radio_model_label = QLabel(
            dual("VOX radio model", "Model rádia VOX")
        )
        form.addRow(self.vox_radio_model_label, self.vox_radio_model)
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
        self.guardian_ptt_mode_label = QLabel(
            dual("Guardian PTT wiring", "Zapojení PTT Guardian")
        )
        form.addRow(self.guardian_ptt_mode_label, self.guardian_ptt_mode)

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

        self.ofdm_tx_lead = _spin(
            0, 3_000, int(getattr(self.config, "ofdm_tx_lead_ms", 60) or 0)
        )
        self.ofdm_tx_tail = _spin(
            0, 3_000, int(getattr(self.config, "ofdm_tx_tail_ms", 60) or 0)
        )
        self.ofdm_min_burst_bytes = _spin(
            256,
            16_384,
            int(getattr(self.config, "ofdm_min_burst_bytes", 2_048) or 2_048),
        )
        self.ofdm_arq_block_bytes = _spin(
            128,
            8_192,
            int(getattr(self.config, "ofdm_arq_block_bytes", 2_048) or 2_048),
        )
        form.addRow(
            dual("SC-FTN TX lead (ms)", "Náběh TX SC-FTN (ms)"),
            self.ofdm_tx_lead,
        )
        form.addRow(
            dual("SC-FTN TX tail (ms)", "Doběh TX SC-FTN (ms)"),
            self.ofdm_tx_tail,
        )
        form.addRow(
            dual("SC-FTN minimum burst (B)", "Minimum dávky SC-FTN (B)"),
            self.ofdm_min_burst_bytes,
        )
        form.addRow(
            dual("SC-FTN ARQ block (B)", "Blok ARQ SC-FTN (B)"),
            self.ofdm_arq_block_bytes,
        )
        self.radio_backend.currentIndexChanged.connect(
            self._sync_radio_backend_rows
        )
        self._sync_radio_backend_rows(self.radio_backend.currentIndex())

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

    def _sync_radio_backend_rows(self, _index: int = -1) -> None:
        """Show the model and PTT fields owned by the selected radio path."""
        backend = self.radio_backend.currentData()
        guardian = backend in {"guardian_k5", "guardian_k61"}
        vox = backend == "vox"
        self.guardian_radio_model.setVisible(guardian)
        self.guardian_radio_model_label.setVisible(guardian)
        self.vox_radio_model.setVisible(vox)
        self.vox_radio_model_label.setVisible(vox)
        self.guardian_ptt_mode.setVisible(guardian)
        self.guardian_ptt_mode_label.setVisible(guardian)

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
        if hamlib:
            radio = self.radio_model.currentText().strip()
        elif backend in {"guardian_k5", "guardian_k61"}:
            radio = self.guardian_radio_model.text().strip()
        elif backend == "vox":
            radio = self.vox_radio_model.text().strip()
        else:
            radio = ""
        return {
            "radio_backend": backend,
            "radio": radio,
            "rig_model": int(self.radio_model.currentData() or 0) if hamlib else 0,
            "cat_port": self.selected_cat_port(),
            "cat_baud": self.cat_baud.value(),
            "rigctld_host": self.rigctld_host.text().strip(),
            "rigctld_port": self.rigctld_port.value(),
            "rigctld_path": self.rigctld_path.text() or "rigctld",
            "ptt_type": self.ptt_type.currentData(),
            "ptt_line": self.ptt_line.currentText(),
            "guardian_ptt_mode": self.guardian_ptt_mode.currentText(),
            "vara_ptt_delay_ms": self.vara_ptt_delay.value(),
            "ofdm_tx_lead_ms": self.ofdm_tx_lead.value(),
            "ofdm_tx_tail_ms": self.ofdm_tx_tail.value(),
            "ofdm_min_burst_bytes": self.ofdm_min_burst_bytes.value(),
            "ofdm_arq_block_bytes": self.ofdm_arq_block_bytes.value(),
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
        self.guardian_radio_model.setText(str(values.get("radio", "") or ""))
        self.vox_radio_model.setText(str(values.get("radio", "") or ""))
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
        self.guardian_ptt_mode.setCurrentText(
            str(values.get("guardian_ptt_mode", "AIOC") or "AIOC").upper()
        )
        self.vara_ptt_delay.setValue(
            max(0, min(int(values.get("vara_ptt_delay_ms", 0) or 0), MAX_PTT_DELAY_MS))
        )
        self.ofdm_tx_lead.setValue(
            max(0, min(int(values.get("ofdm_tx_lead_ms", 60) or 0), 3_000))
        )
        self.ofdm_tx_tail.setValue(
            max(0, min(int(values.get("ofdm_tx_tail_ms", 60) or 0), 3_000))
        )
        self.ofdm_min_burst_bytes.setValue(
            max(256, min(int(values.get("ofdm_min_burst_bytes", 2_048) or 2_048), 16_384))
        )
        self.ofdm_arq_block_bytes.setValue(
            max(128, min(int(values.get("ofdm_arq_block_bytes", 2_048) or 2_048), 8_192))
        )
        self._sync_radio_backend_rows(self.radio_backend.currentIndex())

    def _save_radio_profile(self) -> None:
        locked = self._settings_locked_reason()
        if locked is not None:
            self.ptt_status.setText(locked)
            return
        suggestion = str(self.radio_profile_picker.currentData() or "")
        if not suggestion:
            values = self._radio_form_values()
            suggestion = str(values.get("radio") or self.radio_backend.currentData() or "")
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
        backend = self.radio_backend.currentData()
        current_radio = (
            self.radio_model.currentText().strip()
            if backend == "hamlib"
            else self.guardian_radio_model.text().strip()
            if backend in {"guardian_k5", "guardian_k61"}
            else self.vox_radio_model.text().strip()
            if backend == "vox"
            else ""
        )
        return (
            backend != self.config.radio_backend
            or (
                backend != "hamlib"
                and current_radio != str(self.config.radio or "").strip()
            )
            or int(self.radio_model.currentData() or 0) != self.config.rig_model
            or self.selected_cat_port() != self.config.cat_port
            or self.cat_baud.value() != self.config.cat_baud
            or self.rigctld_host.text().strip() != self.config.rigctld_host
            or self.rigctld_port.value() != self.config.rigctld_port
            or self.rigctld_path.text() != self.config.rigctld_path
            or self.ptt_line.currentText() != self.config.ptt_line
            or self.guardian_ptt_mode.currentText()
            != (getattr(self.config, "guardian_ptt_mode", "AIOC") or "AIOC")
            or self.ptt_type.currentData() != (self.config.ptt_type or "RIG").upper()
            or self.vara_ptt_delay.value() != int(self.config.vara_ptt_delay_ms or 0)
            or self.ofdm_tx_lead.value() != int(self.config.ofdm_tx_lead_ms or 0)
            or self.ofdm_tx_tail.value() != int(self.config.ofdm_tx_tail_ms or 0)
            or self.ofdm_min_burst_bytes.value()
            != int(self.config.ofdm_min_burst_bytes or 0)
            or self.ofdm_arq_block_bytes.value()
            != int(self.config.ofdm_arq_block_bytes or 0)
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
                "Select the active VARA flavor and its ports. An empty "
                "executable field follows detection; the grey text is the path "
                "Guardian uses.",
                "Zvolte variantu VARA a její porty. Prázdné pole s programem "
                "se řídí detekcí; šedý text je cesta, kterou Guardian používá.",
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
        self.payload_backend.addItem("Guardian SC-FTN", "ofdm_vhf")
        self.payload_backend.setCurrentIndex(
            max(0, self.payload_backend.findData(self.config.payload_backend))
        )
        self.g2_waveform = QComboBox()
        self.g2_waveform.addItem("SC-FTN", SC_FTN_WAVEFORM)
        self.g2_waveform.setCurrentIndex(0)
        self.g2_waveform.setEnabled(False)
        self.g2_bandwidth = QComboBox()
        for width in SC_FTN_BANDWIDTHS:
            self.g2_bandwidth.addItem(width, width)
        self.g2_bandwidth.setCurrentIndex(
            max(0, self.g2_bandwidth.findData(
                str(getattr(self.config, "g2_bandwidth", "2K7")).upper()
            ))
        )
        self.g2_summary = QLabel()
        self.g2_summary.setObjectName("Metadata")
        self.g2_summary.setWordWrap(True)
        # Short aliases keep the SC naming usable by the diagnostics and UI
        # tests without adding a second set of configuration controls.
        self.sc_bandwidth = self.g2_bandwidth
        self.sc_summary = self.g2_summary
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
        form.addRow(dual("Active VARA mode", "Aktivní režim VARA"), self.vara_mode)
        form.addRow(dual("Payload workflow", "Způsob přenosu"), self.payload_backend)
        form.addRow(dual("Guardian waveform", "Vlna Guardian"), self.g2_waveform)
        form.addRow(dual("SC-FTN bandwidth", "Šířka pásma SC-FTN"), self.g2_bandwidth)
        form.addRow(dual("Resolved SC-FTN policy", "Výsledná politika SC-FTN"), self.g2_summary)
        self._modem_form = form
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
        self.payload_backend.currentIndexChanged.connect(
            lambda _index: self._sync_bandwidth_row(self.vara_mode.currentText())
        )
        self.g2_bandwidth.currentIndexChanged.connect(self._sync_sc_summary)
        self.radio_backend.currentIndexChanged.connect(
            lambda _index: self._sync_sc_summary()
        )
        self.radio_model.currentIndexChanged.connect(
            lambda _index: self._sync_sc_summary()
        )
        self.guardian_radio_model.textChanged.connect(self._sync_sc_summary)
        self._sync_bandwidth_row(self.vara_mode.currentText())
        self._sync_sc_summary()
        form.addRow(dual("Control-burst modem", "Modem řídicích rámců"), self.control_modem)


    def _sync_bandwidth_row(self, mode: str) -> None:
        """Show only the selected transport's waveform and bandwidth rows."""
        sc_selected = self.payload_backend.currentData() == "ofdm_vhf"
        for widget in (self.g2_waveform, self.g2_bandwidth, self.g2_summary):
            self._modem_form.setRowVisible(widget, sc_selected)
        visible = (
            mode.upper() == "HF"
            and self.payload_backend.currentData() == "vara_p2p"
        )
        self.vara_hf_bandwidth.setVisible(visible)
        self.vara_hf_bandwidth_label.setVisible(visible)

    def _sync_sc_summary(self, _index: int = -1) -> None:
        """Show the exact policy selected by the current SC-FTN path."""
        width = str(self.g2_bandwidth.currentData() or "2K7").upper()
        values = self._radio_form_values()
        try:
            policy = automatic_g2_policy(
                SC_FTN_WAVEFORM,
                width,
                radio_backend=str(values.get("radio_backend") or ""),
                radio_model=str(values.get("radio") or ""),
            )
            profile = profile_for(SC_FTN_WAVEFORM, width)
            geometry = {
                name: value
                for name, value in (
                    ("center_hz", policy.center_hz),
                    ("nyquist_symbol_rate", policy.nyquist_symbol_rate),
                    ("symbol_rate", policy.symbol_rate),
                )
                if value is not None
            }
            from dataclasses import replace

            profile = replace(
                profile,
                bootstrap_modulation=policy.bootstrap_modulation,
                data_acquisition_lead_seconds=policy.acquisition_lead_seconds,
                reference_metric_blocks=policy.reference_metric_blocks,
                **geometry,
            )
            self.g2_summary.setText(
                f"AUTO · {policy.summary()}\n"
                f"{profile.name}: {profile.occupied_bandwidth:.0f} Hz, "
                f"center {profile.center_hz:.1f} Hz, "
                f"symbol {profile.symbol_rate:.1f} sym/s"
            )
        except ValueError as exc:
            self.g2_summary.setText(str(exc))

    def _build_payload_options(self) -> None:
        form = self._page(
            dual("Compression & identification", "Komprese a identifikace"),
            dual(
                "Guardian XZ compresses the payload and repacks images losslessly.",
                "Guardian XZ komprimuje obsah a bezeztrátově přebaluje obrázky.",
            ),
        )
        self.guardian_aggressive_compression = QCheckBox(dual(
            "Use aggressive Guardian compression (XZ)",
            "Použít agresivní kompresi Guardian (XZ)",
        ))
        self.guardian_aggressive_compression.setChecked(
            bool(getattr(self.config, "guardian_aggressive_compression", False))
        )
        self.guardian_aggressive_compression.setToolTip(dual(
            "Runs the optional XZ path for payloads where size matters more than "
            "CPU time. Images are repacked losslessly.",
            "Použije volitelnou cestu XZ tam, kde je důležitější velikost než čas "
            "CPU. Obrázky se přebalují bezeztrátově.",
        ))
        self.morse_id_after_ack = QCheckBox(dual(
            "After the final ACK, send both callsigns in Morse at 40 WPM",
            "Po posledním ACK odvysílat obě značky Morse rychlostí 40 WPM",
        ))
        self.morse_id_after_ack.setChecked(self.config.morse_id_after_ack)
        self.morse_id_after_ack.setToolTip(dual(
            "Only the final destination transmits once: SENDER DE RECEIVER.",
            "Pouze cílová stanice odvysílá jednou: ODESÍLATEL DE PŘÍJEMCE.",
        ))
        form.addRow(self.guardian_aggressive_compression)
        form.addRow(self.morse_id_after_ack)

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
        self.separate_working_channels = QCheckBox(
            tr("settings.separate_working_channels")
        )
        self.separate_working_channels.setChecked(
            self.config.separate_working_channels
        )
        self.separate_working_channels.setToolTip(
            tr("settings.separate_working_channels_hint")
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
        form.addRow(self.separate_working_channels)
        form.addRow(self.beacon_enabled)
        form.addRow(dual("Beacon interval (seconds)", "Interval majáku (sekundy)"), self.beacon_interval)
        form.addRow(dual("Channel scan dwell (seconds)", "Doba poslechu kanálu (sekundy)"), self.scan_dwell)
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

    def _settings_locked_reason(self) -> str | None:
        """Return why applying a new station profile must wait.

        The dialog edits a live ``StationConfig`` object.  Check the runtime
        before touching it so a later Operations guard cannot leave half of a
        profile applied while an AutoTune run, payload transfer, or radio
        handoff still owns the path.
        """
        operations = self.operations
        if operations is None:
            return None
        payload_active = getattr(operations, "payload_active", None)
        if callable(payload_active) and payload_active():
            return dual(
                "Settings cannot be applied while a payload transfer is active.",
                "Nastavení nelze použít, dokud probíhá přenos dat.",
            )
        handoff_pending = getattr(operations, "payload_handoff_pending", None)
        if callable(handoff_pending) and handoff_pending():
            return dual(
                "Settings cannot be applied while the radio handoff is active.",
                "Nastavení nelze použít během předávání rádia.",
            )
        status = getattr(operations, "station_lab", None)
        state = str(getattr(status, "state", "") or "").strip().lower()
        active_states = {
            "offering",
            "waiting_approval",
            "preparing",
            "measuring",
            "waiting_report",
        }
        if state in active_states or bool(getattr(status, "pending_offer", False)):
            return dual(
                "Settings cannot be applied while Station Lab AutoTune is active.",
                "Nastavení nelze použít během AutoTune ve Station Lab.",
            )
        # A profile handshake owns the current backend before Operations sets
        # its payload-active event.  Use the explicit Operations contract when
        # the live runtime supplies it; lightweight preview/test doubles may
        # omit the method and are already covered by the guards above.
        network_busy = getattr(operations, "network_settings_busy", None)
        if callable(network_busy) and network_busy():
            return dual(
                "Settings cannot be applied while a network session is active.",
                "Nastavení nelze použít během aktivní síťové relace.",
            )
        return None

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
        if self.radio_backend.currentData() in {"guardian_k5", "guardian_k61", "vox"}:
            if not self.selected_cat_port():
                errors.append(
                    dual(
                        "Choose the serial port used by the selected PTT/radio path.",
                        "Vyberte sériový port pro zvolenou cestu rádia/PTT.",
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
        locked = self._settings_locked_reason()
        if locked is not None:
            self.error.setText(locked)
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
        cfg.g2_waveform = SC_FTN_WAVEFORM
        cfg.g2_bandwidth = str(self.g2_bandwidth.currentData() or "2K7").upper()
        cfg.g2_adaptive_mcs = True
        if cfg.payload_backend == "ofdm_vhf":
            policy = automatic_g2_policy(
                cfg.g2_waveform,
                cfg.g2_bandwidth,
                radio_backend=cfg.radio_backend,
                radio_model=cfg.radio,
            )
            cfg.g2_mcs = policy.maximum_mcs
            # Keep the historical ofdm_* settings populated with the selected
            # policy so older payload/link callers see the same negotiated values.
            cfg.ofdm_profile = policy.profile_name
            cfg.ofdm_mcs = policy.maximum_mcs
            cfg.ofdm_max_retries = policy.maximum_retries + policy.rescue_retries
            cfg.ofdm_adaptive_fec = True
            cfg.ofdm_modern_ldpc = True
            cfg.ofdm_fec = policy.fec_label
            cfg.ofdm_adaptive_burst = True
            cfg.ofdm_burst_bytes = policy.initial_burst_bytes
            cfg.ofdm_min_burst_bytes = policy.minimum_burst_bytes
            cfg.ofdm_max_burst_bytes = policy.maximum_burst_bytes
            cfg.ofdm_arq_block_bytes = policy.arq_block_bytes
            cfg.ofdm_timeout_multiplier = 1.0
            cfg.ofdm_legacy_mode = False
            cfg.ofdm_train_bursts = 1
            cfg.ofdm_adaptive_train = False
            cfg.ofdm_superframe = True
            cfg.ofdm_train_gap_ms = 30
            cfg.ofdm_max_train_seconds = policy.maximum_train_seconds
            # The burst controls are radio-profile fields, but AUTO derives
            # their safe values. Keep the page truthful after Apply.
            self.ofdm_min_burst_bytes.setValue(policy.minimum_burst_bytes)
            self.ofdm_arq_block_bytes.setValue(policy.arq_block_bytes)
        cfg.control_modem = self.control_modem.currentData()
        cfg.vara_hf_bandwidth = self.vara_hf_bandwidth.currentData()
        cfg.vara_host_ptt = True
        cfg.vara_file_compression = False
        cfg.guardian_compression = False
        cfg.guardian_aggressive_compression = (
            self.guardian_aggressive_compression.isChecked()
        )
        cfg.morse_id_after_ack = self.morse_id_after_ack.isChecked()
        cfg.apply_vara_mode(self.vara_mode.currentText())
        cfg.default_ttl = self.default_ttl.value()
        cfg.auto_route = True
        cfg.auto_relay = True
        cfg.auto_deliver = True
        cfg.auto_qsy = True
        cfg.separate_working_channels = self.separate_working_channels.isChecked()
        cfg.beacon_enabled = self.beacon_enabled.isChecked()
        cfg.beacon_interval = float(self.beacon_interval.value())
        cfg.scan_dwell = float(self.scan_dwell.value())
        cfg.discovery_forward = True
        cfg.discovery_ttl = self.discovery_ttl.value()
        cfg.discovery_route_lifetime = float(self.discovery_lifetime.value() * 60)
        cfg.discovery_frame_budget = self.discovery_budget.value()
        cfg.discovery_allowlist = callsign_list(self.discovery_allowlist.text())
        cfg.discovery_denylist = callsign_list(self.discovery_denylist.text())
        cfg.appearance = self.selected_theme.value.title()
        cfg.enforce_production_policy()
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
