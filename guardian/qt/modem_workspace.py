"""Operator view for the production SC-FTN payload profile.

The configured bandwidth is changed in Settings.  This page shows the
resolved PHY geometry and policy values used by the active backend.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..config import SC_FTN_BANDWIDTHS, SC_FTN_WAVEFORM
from ..ofdm.automatic import automatic_g2_policy
from ..waveforms.config import profile_for
from ..i18n import dual, tr


class ModemWorkspace(QWidget):
    """Show the six audited SC-FTN bandwidth rungs and resolved geometry."""

    bandwidth_changed = Signal(str)

    def __init__(self, runtime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self._updating = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(8)

        title = QLabel(dual("SC-FTN modem", "Modem SC-FTN"))
        title.setObjectName("PanelHeader")
        outer.addWidget(title)
        intro = QLabel(
            dual(
                "The SC-FTN width is changed in Settings. This page shows the "
                "resolved PHY geometry and AUTO policy for the active path.",
                "Šířka SC-FTN se mění v Nastavení. Tato stránka zobrazuje "
                "výslednou geometrii PHY a politiku AUTO aktivní cesty.",
            )
        )
        intro.setObjectName("Metadata")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        selection = QFrame()
        selection.setObjectName("WorkspacePanel")
        row = QHBoxLayout(selection)
        row.setContentsMargins(10, 8, 10, 8)
        row.addWidget(QLabel(dual("Configured SC-FTN bandwidth", "Nastavená šířka SC-FTN")))
        self.bandwidth_picker = QComboBox()
        for width in SC_FTN_BANDWIDTHS:
            self.bandwidth_picker.addItem(self._width_label(width), width)
        self.bandwidth_picker.setCurrentIndex(
            max(
                0,
                self.bandwidth_picker.findData(
                    str(getattr(self.runtime.config, "g2_bandwidth", "2K7"))
                    .strip()
                    .upper()
                ),
            )
        )
        # A passive workspace must never advertise a width that the active
        # payload backend has not been rebuilt for. Change the width in Settings
        # so Operations can apply it atomically before the next session.
        self.bandwidth_picker.setEnabled(False)
        self.bandwidth_picker.setToolTip(
            dual(
                "Read-only while the active backend is running. Change the "
                "SC-FTN width in Settings, then Apply.",
                "Pouze pro čtení, dokud běží backend. Šířku SC-FTN změňte v "
                "Nastavení a potvrďte Použít.",
            )
        )
        self.bandwidth_picker.currentIndexChanged.connect(self._bandwidth_selected)
        row.addWidget(self.bandwidth_picker, 1)
        row.addWidget(QLabel(dual("Family: SC-FTN", "Rodina: SC-FTN")))
        outer.addWidget(selection)

        facts = QFrame()
        facts.setObjectName("WorkspacePanel")
        fact_layout = QVBoxLayout(facts)
        fact_layout.setContentsMargins(10, 8, 10, 8)
        heading = QLabel(dual("Resolved profile and policy", "Výsledný profil a politika"))
        heading.setObjectName("SectionLabel")
        fact_layout.addWidget(heading)
        self.fields: dict[str, QLabel] = {}
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        for key, label in (
            ("profile", dual("Profile", "Profil")),
            ("occupied", dual("Occupied band", "Zabírané pásmo")),
            ("sample_rate", dual("Audio sample rate", "Vzorkování zvuku")),
            ("symbol", dual("Symbol rate / FTN", "Symbolová rychlost / FTN")),
            ("equalizer", dual("Receiver", "Přijímač")),
            ("policy", dual("AUTO policy", "Politika AUTO")),
            ("geometry", dual("Applied geometry", "Použitá geometrie")),
        ):
            value = QLabel()
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.fields[key] = value
            form.addRow(QLabel(label), value)
        fact_layout.addLayout(form)
        self.policy_status = QLabel()
        self.policy_status.setObjectName("Metadata")
        self.policy_status.setWordWrap(True)
        fact_layout.addWidget(self.policy_status)
        outer.addWidget(facts, 1)
        outer.addStretch(1)
        self.refresh()

    @staticmethod
    def _width_label(width: str) -> str:
        values = {
            "1K2": "1K2 · ~1.2 kHz",
            "2K7": "2K7 · ~2.7 kHz",
            "4K5": "4K5 · ~4.5 kHz",
            "5K": "5K · ~5.0 kHz",
            "10K": "10K · ~10 kHz",
            "20K": "20K · ~18.8 kHz",
        }
        return values.get(width, width)

    def _bandwidth_selected(self, _index: int) -> None:
        if self._updating:
            return
        # Keep this slot harmless for programmatic test/preview changes too.
        # The disabled picker is informational; SettingsDialog is the one path
        # that updates config and lets Operations rebuild the backend.
        width = str(self.bandwidth_picker.currentData() or "2K7")
        self.bandwidth_changed.emit(width)
        self.refresh()

    def _policy(self):
        config = self.runtime.config
        return automatic_g2_policy(
            SC_FTN_WAVEFORM,
            str(getattr(config, "g2_bandwidth", "2K7")),
            radio_backend=str(getattr(config, "radio_backend", "")),
            radio_model=str(getattr(config, "radio", "")),
        )

    @staticmethod
    def effective_profile(policy, width: str):
        """Return the geometry the payload backend will actually use.

        ``profile_for`` is the family catalogue.  AUTO may apply a measured
        geometry override (the qualified 2K7 path is the important case), so
        displaying the catalogue object directly would make the operator page
        disagree with :class:`OfdmVhfBackend` and its airtime accounting.
        """
        profile = profile_for(SC_FTN_WAVEFORM, width)
        overrides = {
            name: value
            for name, value in (
                ("center_hz", policy.center_hz),
                ("nyquist_symbol_rate", policy.nyquist_symbol_rate),
                ("symbol_rate", policy.symbol_rate),
            )
            if value is not None
        }
        return replace(
            profile,
            bootstrap_modulation=policy.bootstrap_modulation,
            data_acquisition_lead_seconds=policy.acquisition_lead_seconds,
            reference_metric_blocks=policy.reference_metric_blocks,
            **overrides,
        )

    def refresh(self) -> None:
        """Recompute all displayed facts from current config and policy."""
        config = self.runtime.config
        width = str(getattr(config, "g2_bandwidth", "2K7")).strip().upper()
        if width not in SC_FTN_BANDWIDTHS:
            width = "2K7"
        self._updating = True
        try:
            index = self.bandwidth_picker.findData(width)
            if index >= 0 and index != self.bandwidth_picker.currentIndex():
                self.bandwidth_picker.setCurrentIndex(index)
        finally:
            self._updating = False

        policy = self._policy()
        profile = self.effective_profile(policy, width)
        low, high = profile.occupied_band
        self.fields["profile"].setText(profile.name)
        self.fields["occupied"].setText(
            f"{low:.0f}–{high:.0f} Hz ({profile.occupied_bandwidth:.0f} Hz)"
        )
        self.fields["sample_rate"].setText(f"{profile.sample_rate} Hz")
        self.fields["symbol"].setText(
            f"{profile.symbol_rate:.1f} sym/s · τ={profile.ftn_tau:.2f} · "
            f"RRC α={profile.rolloff:.3f}"
        )
        self.fields["equalizer"].setText(
            f"{profile.equalizer_mode.upper()} {profile.equalizer_taps}-tap · "
            f"{profile.equalizer_iterations} iter · "
            f"clock tracking {'on' if profile.symbol_clock_tracking else 'off'}"
        )
        self.fields["policy"].setText(policy.summary())
        geometry = (
            f"center {policy.center_hz:.1f} Hz · Nyquist symbol "
            f"{policy.nyquist_symbol_rate:.1f} · symbol {policy.symbol_rate:.1f}"
            if policy.center_hz is not None
            else "profile geometry"
        )
        self.fields["geometry"].setText(geometry)
        self.policy_status.setText(
            dual(
                "MCS, FEC, burst length and retry rescue are negotiated per "
                "direction. Change the width in Settings; the resolved values "
                "below are the ones used by the payload backend.",
                "MCS, FEC, délka dávky a záchranné opakování se vyjednávají pro "
                "každý směr. Šířku změňte v Nastavení; níže jsou hodnoty použité "
                "payload backendem.",
            )
        )


__all__ = ["ModemWorkspace"]
