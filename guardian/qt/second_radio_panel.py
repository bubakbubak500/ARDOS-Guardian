"""Independent controls and live channel status for the optional second radio."""
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..i18n import dual
from .inputs import FrequencySpinBox


class SecondRadioPanel(QFrame):
    def __init__(self, runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        row = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.radio_button = QPushButton()
        self.vara_button = QPushButton()
        self.control_button = QPushButton()
        self.radio_button.clicked.connect(lambda: self._toggle("radio"))
        self.vara_button.clicked.connect(lambda: self._toggle("vara"))
        self.control_button.clicked.connect(lambda: self._toggle("control"))
        for button in (self.radio_button, self.vara_button, self.control_button):
            row.addWidget(button)
        self.frequency = FrequencySpinBox()
        self.frequency.setKeyboardTracking(False)
        self.frequency.editingFinished.connect(self._frequency_changed)
        row.addWidget(self.frequency)
        row.addStretch()
        layout.addLayout(row)
        self.refresh()

    def operations(self):
        coordinator = getattr(self.runtime, "radio_coordinator", None)
        return coordinator.radios[1] if coordinator and len(coordinator.radios) > 1 else None

    def refresh(self):
        radio = self.operations()
        self.setVisible(radio is not None)
        if radio is None:
            return
        snapshot = radio.snapshots.read()
        frequency = radio.current_frequency()
        frequency_text = f"{frequency / 1_000_000:.4f} MHz" if frequency else "— MHz"
        self.status.setText(dual("Radio 2", "Rádio 2") + f" · {radio.config.radio or radio.config.radio_backend}"
                            + f" · {frequency_text} · VARA {radio.config.vara_mode} "
                            + f"{radio.config.vara_host}:{radio.config.vara_cmd_port}/{radio.config.vara_data_port}"
                            + (f" · {snapshot.radio.error}" if snapshot.radio.error else "")
                            + (f" · {radio.vara.state.error}" if radio.vara.state.error else ""))
        self.radio_button.setText(dual("Disconnect radio 2", "Odpojit rádio 2") if snapshot.radio.connected
                                  else dual("Connect radio 2", "Připojit rádio 2"))
        self.vara_button.setText(dual("Disconnect VARA 2", "Odpojit VARA 2") if radio.vara.connected
                                 else dual("Connect VARA 2", "Připojit VARA 2"))
        self.vara_button.setEnabled(radio.config.payload_backend != "ardop")
        self.vara_button.setToolTip(dual(
            "ARDOP uses its own modem; VARA is unavailable for this backend.",
            "ARDOP používá vlastní modem; VARA není pro tento přenos dostupná.",
        ) if radio.config.payload_backend == "ardop" else "")
        self.control_button.setText(dual("Stop control 2", "Zastavit řízení 2") if radio.audio_transport is not None
                                    else dual("Start control 2", "Spustit řízení 2"))
        self.frequency.setVisible(not radio.has_frequency_control())
        self.frequency.setEnabled(not radio.network_settings_busy())
        if not self.frequency.hasFocus():
            self.frequency.setValue(int(radio.config.manual_frequency_hz or 0))

    def _frequency_changed(self):
        radio = self.operations()
        if radio and not radio.network_settings_busy():
            radio.set_manual_frequency(self.frequency.value())

    def _toggle(self, component):
        radio = self.operations()
        if radio is None:
            return
        if component == "radio":
            (radio.disconnect_radio if radio.snapshots.read().radio.connected else radio.connect_radio)()
        elif component == "vara":
            (radio.disconnect_vara if radio.vara.connected else radio.connect_vara)()
        else:
            (radio.stop_control_channel if radio.audio_transport is not None else radio.start_control_channel)()
        self.refresh()
