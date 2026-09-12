"""Headless checks for the SC-FTN side-by-side G1 Qt surface."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMenu

from guardian.config import StationConfig
from guardian.i18n import tr
from guardian.ofdm.automatic import automatic_g2_policy
from guardian.payload.ofdm_vhf import OfdmVhfBackend
from guardian.qt.modem_workspace import ModemWorkspace
from guardian.qt.settings_dialog import SettingsDialog
from guardian.qt.shell import GuardianMainWindow
from guardian.qt.station_lab_workspace import StationLabWorkspace
from guardian.qt.theme import ThemePreference
from guardian.qt.transfer_progress import transfer_state
from guardian.qt.runtime import ShellRuntime
from guardian.session.orchestrator import SessionState
from guardian.station_lab import CalibrationState


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_modem_workspace_displays_backend_overridden_2k7_geometry() -> None:
    _application()
    config = StationConfig(g2_bandwidth="2K7")
    runtime = SimpleNamespace(config=config)
    workspace = ModemWorkspace(runtime)
    backend = OfdmVhfBackend(
        g2_waveform="sc_ftn",
        g2_bandwidth="2K7",
        radio_backend=config.radio_backend,
        radio_model=config.radio,
        pipe_factory=lambda _profile: None,
    )
    try:
        policy = automatic_g2_policy("sc_ftn", "2K7")
        assert workspace.effective_profile(policy, "2K7") == backend.profile
        assert "2859 Hz" in workspace.fields["occupied"].text()
        assert "2823.5" in workspace.fields["symbol"].text()
        assert "AUTO" in workspace.fields["policy"].text()
    finally:
        workspace.close()


def test_settings_exposes_sc_widths_guardian_profiles_and_xz_compression(
    monkeypatch,
) -> None:
    _application()
    config = StationConfig(payload_backend="ofdm_vhf")
    monkeypatch.setattr(config, "save", lambda: None)
    dialog = SettingsDialog(config, ThemePreference.SYSTEM)
    try:
        assert dialog.payload_backend.count() == 2
        assert dialog.g2_waveform.count() == 1
        assert dialog.g2_bandwidth.count() == 6
        assert "AUTO" in dialog.g2_summary.text()
        assert dialog.radio_backend.findData("guardian_k5") >= 0
        assert dialog.radio_backend.findData("guardian_k61") >= 0

        dialog.guardian_aggressive_compression.setChecked(True)
        assert dialog.guardian_aggressive_compression.isChecked()
        for removed in (
            "vara_file_compression", "guardian_compression", "vara_host_ptt",
            "auto_route", "auto_relay", "auto_deliver", "auto_qsy", "discovery_forward",
        ):
            assert not hasattr(dialog, removed)

        dialog.radio_backend.setCurrentIndex(dialog.radio_backend.findData("guardian_k5"))
        dialog.guardian_radio_model.setText("Quansheng UV-K5")
        dialog.cat_port.setCurrentText("COM7")
        dialog.g2_bandwidth.setCurrentIndex(dialog.g2_bandwidth.findData("2K7"))
        assert dialog.apply()
        assert config.g2_waveform == "sc_ftn"
        assert config.g2_bandwidth == "2K7"
        assert config.ofdm_min_burst_bytes == 512
        assert config.ofdm_arq_block_bytes == 256
        assert config.guardian_aggressive_compression
        assert not config.vara_file_compression
        assert not config.guardian_compression
        for name in ("vara_host_ptt", "auto_route", "auto_relay", "auto_deliver", "auto_qsy", "discovery_forward"):
            assert getattr(config, name) is True
        assert config.guardian_ptt_mode == "AIOC"
    finally:
        dialog.close()


def test_settings_apply_is_atomic_when_payload_is_busy(monkeypatch) -> None:
    _application()
    config = StationConfig(callsign="OK1AAA")
    monkeypatch.setattr(config, "save", lambda: None)
    operations = SimpleNamespace(
        payload_active=lambda: True,
        payload_handoff_pending=lambda: False,
        station_lab=SimpleNamespace(state="idle", pending_offer=False),
    )
    dialog = SettingsDialog(config, ThemePreference.SYSTEM, operations=operations)
    try:
        dialog.callsign.setText("OK2NEW")
        assert not dialog.apply()
        assert config.callsign == "OK1AAA"
        assert "payload transfer" in dialog.error.text().lower()
    finally:
        dialog.close()


def test_settings_apply_is_atomic_during_profile_negotiation(monkeypatch) -> None:
    _application()
    config = StationConfig(callsign="OK1AAA")
    monkeypatch.setattr(config, "save", lambda: None)
    profile_session = SimpleNamespace(state=SessionState.NEGOTIATING_PROFILE)
    operations = SimpleNamespace(
        payload_active=lambda: False,
        payload_handoff_pending=lambda: False,
        network_settings_busy=lambda: any(
            item.state is SessionState.NEGOTIATING_PROFILE
            for item in (profile_session,)
        ),
        station_lab=SimpleNamespace(state="idle", pending_offer=False),
    )
    dialog = SettingsDialog(config, ThemePreference.SYSTEM, operations=operations)
    try:
        dialog.callsign.setText("OK2NEG")
        assert not dialog.apply()
        assert config.callsign == "OK1AAA"
        assert "network session" in dialog.error.text().lower()
    finally:
        dialog.close()


def test_transfer_sc_status_does_not_reuse_stale_vara_route() -> None:
    vara = SimpleNamespace(
        transfer_source="OK1OLD",
        transfer_destination="OK2OLD",
        transfer_via="RELAYOLD",
    )
    snapshot = SimpleNamespace(vara=vara)
    status = SimpleNamespace(
        state="transmitting",
        total_bytes=1024,
        tx_bytes=512,
        rx_bytes=0,
        direction="send",
        profile="SC_FTN_2K7",
        mcs=1,
        fec="LDPC-1/2",
    )
    state = transfer_state(snapshot, True, status)
    assert state.transport == "sc_ftn"
    assert state.source == ""
    assert state.destination == ""
    assert state.via == ""


def test_station_lab_workspace_maps_progress_and_offer_state() -> None:
    _application()
    status = SimpleNamespace(
        state=CalibrationState.MEASURING.value,
        peer="OK2XYZ",
        session_id=55,
        progress=3,
        total=30,
        current="Burst 3/30",
        message="measuring",
        error="",
        pending_offer=False,
        report=None,
        report_json="",
        report_csv="",
    )
    operations = SimpleNamespace(station_lab=status)
    workspace = StationLabWorkspace(SimpleNamespace(
        config=StationConfig(payload_backend="ofdm_vhf"), operations=operations,
    ))
    try:
        assert workspace.progress.value() == 3
        assert workspace.progress.maximum() == 30
        assert "OK2XYZ" in workspace.state_label.text()
        assert workspace.cancel_button.isEnabled()
    finally:
        workspace.close()


def test_shell_separates_protocol_and_render_clocks(tmp_path) -> None:
    _application()
    settings = QSettings(str(tmp_path / "shell.ini"), QSettings.Format.IniFormat)
    runtime = ShellRuntime()
    window = GuardianMainWindow(runtime, settings)
    calls: list[str] = []
    runtime.drain_workers = lambda: calls.append("drain")
    runtime.tick = lambda: calls.append("tick")
    try:
        assert window.protocol_timer.interval() == 100
        assert window.refresh_timer.interval() == 500
        menus = {menu.title(): menu for menu in window.menuBar().findChildren(QMenu)}
        view = menus[tr("menu.view")]
        tools = menus[tr("menu.tools")]
        assert window.workspace_actions["modem"] not in view.actions()
        diagnostics = menus[tr("menu.diagnostics")]
        assert window.workspace_actions["modem"] in diagnostics.actions()
        assert not {tr("menu.radio_toggle"), tr("menu.vara_toggle"), tr("menu.control_toggle")} & {action.text() for action in tools.actions()}
        runtime.config.payload_backend = "vara_p2p"
        window._refresh()
        assert window.workspace_actions["station_lab"].isEnabled()
        window.workspace_actions["station_lab"].trigger()
        assert window.workspace_stack.currentWidget() is window.workspace_names["station_lab"]
        assert not window.workspace_names["station_lab"].start_button.isEnabled()
        runtime.config.payload_backend = "ofdm_vhf"
        window._refresh()
        assert window.workspace_actions["station_lab"].isEnabled()
        window._show_workspace("station_lab")
        assert window.workspace_stack.currentWidget() is window.workspace_names["station_lab"]
        runtime.config.radio_backend = "guardian_k5"
        window._refresh()
        assert window.hamlib_status.isHidden()
        runtime.config.radio_backend = "hamlib"
        window._refresh()
        assert not window.hamlib_status.isHidden()
        calls.clear()
        window._refresh()
        assert calls == []
        window._protocol_tick()
        assert calls == ["drain", "tick"]
    finally:
        window.close()
        runtime.close()


def test_autotune_workspace_blocks_stale_actions_after_switch_to_vara():
    _application()
    calls = []
    config = StationConfig(payload_backend="ofdm_vhf")
    status = SimpleNamespace(state="idle", pending_offer=False)
    operations = SimpleNamespace(
        station_lab=status,
        start_station_calibration=lambda *args: calls.append("start"),
        accept_station_calibration=lambda: calls.append("accept"),
    )
    workspace = StationLabWorkspace(SimpleNamespace(config=config, operations=operations))
    try:
        assert workspace.start_button.isEnabled()
        assert workspace.availability_hint.isHidden()
        workspace.peer.setText("OK1ABC")
        config.payload_backend = "vara_p2p"
        workspace.refresh()
        assert not workspace.start_button.isEnabled()
        assert not workspace.accept.isEnabled()
        assert not workspace.reject.isEnabled()
        assert not workspace.cancel_button.isEnabled()
        assert not workspace.report_button.isEnabled()
        assert not workspace.availability_hint.isHidden()
        workspace._start()
        assert not workspace.accept_offer()
        assert not calls
    finally:
        workspace.close()
