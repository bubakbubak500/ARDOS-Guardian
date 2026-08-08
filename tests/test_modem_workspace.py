"""The Modem test workspace: measuring the modem from inside the application.

The operator's requirement was that everything `tools/ofdm_bench.py` can do is
reachable in the application, with no console and no scripts. These tests hold
the workspace to that by driving the real engine -- on BENCH, with parameters
small enough to be quick -- rather than by mocking it, because a mocked bench
would prove only that the UI can render a fixture.

Two claims matter more than the rest and are tested on their own: a figure the
receiver never measured must read as "unavailable" and never as a zero, and a
block delivered with the wrong bytes must be impossible to overlook.
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu

from guardian.i18n import Language, TRANSLATIONS, set_language, tr
from guardian.ofdm import MCS_TABLE
from guardian.ofdm import bench
from guardian.ofdm.config import PROFILE_LADDER, profile_or_default
from guardian.ofdm.metrics import LinkMetrics
from guardian.qt.modem_workspace import SWEEP_TASK, ModemWorkspace
from guardian.qt.runtime import ShellRuntime
from guardian.qt.shell import GuardianMainWindow
from guardian.qt.theme import ThemePreference


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _workspace(profile: str = "BENCH") -> tuple[ShellRuntime, ModemWorkspace]:
    _application()
    runtime = ShellRuntime()
    runtime.config.ofdm_profile = profile
    runtime.config.ofdm_mcs = 1
    return runtime, ModemWorkspace(runtime)


def _pump(workspace: ModemWorkspace) -> None:
    """One turn of the shell's poll: drain the pool, then show the progress.

    Exactly what `GuardianMainWindow._refresh` does every 500 ms, so the tests
    drive the workspace through the same path the application does.
    """
    workspace.runtime.drain_workers()
    workspace.refresh()


def _wait(workspace: ModemWorkspace, timeout: float = 300.0) -> None:
    """Poll until the measurement lands."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _pump(workspace)
        if workspace._running is None:
            return
        time.sleep(0.01)
    _pump(workspace)
    raise AssertionError("the measurement never finished")


def _unavailable() -> str:
    return TRANSLATIONS["record.unavailable"][0]


# -- picking a waveform ------------------------------------------------------ #

def test_the_picker_offers_the_ladder_in_bandwidth_order() -> None:
    runtime, workspace = _workspace()
    try:
        offered = [
            workspace.profile_picker.itemData(index)
            for index in range(workspace.profile_picker.count())
        ]
        # Read the order from the ladder rather than naming it here: the picker
        # must offer what the modem defines, in the order it is meant to be
        # tried in, and never an alphabetical copy of it.
        assert offered == list(PROFILE_LADDER)
        widths = [
            profile_or_default(name).occupied_bandwidth for name in offered
        ]
        assert widths == sorted(widths)
        assert [
            workspace.mcs_picker.itemData(index)
            for index in range(workspace.mcs_picker.count())
        ] == [scheme.index for scheme in MCS_TABLE]
    finally:
        runtime.close()


def test_the_facts_panel_restates_the_whole_waveform_when_the_selection_changes(
) -> None:
    runtime, workspace = _workspace()
    try:
        facts = bench.describe(profile_or_default("BENCH"), 1)
        assert workspace.facts == facts
        fields = workspace.facts_fields
        assert f"{facts.occupied:.0f}" in fields["band"].text()
        assert str(facts.fft_size) in fields["fft"].text()
        assert str(facts.cp_length) in fields["fft"].text()
        assert str(facts.carriers) in fields["carriers"].text()
        assert str(facts.data_carriers) in fields["carriers"].text()
        assert str(facts.pilots) in fields["carriers"].text()
        assert str(facts.header_symbols) in fields["header"].text()
        assert f"{facts.phy_rate:.0f}" in fields["phy_rate"].text()
        assert f"{facts.full_block_seconds:.2f}" in fields["airtime"].text()
        assert f"{facts.mcs_index}" in fields["mcs"].text()
        # Sample rate is not bandwidth, and the panel has to say so in the one
        # place the two numbers sit near each other.
        assert str(facts.sample_rate) in fields["sample_rate"].text()
        assert "not the bandwidth" in fields["sample_rate"].text()

        # Moving either picker rewrites the whole panel.
        before = fields["band"].text()
        workspace.profile_picker.setCurrentIndex(
            workspace.profile_picker.findData("WIDE_10K")
        )
        assert workspace.facts.profile == "WIDE_10K"
        assert fields["band"].text() != before
        wide = bench.describe(profile_or_default("WIDE_10K"), 1)
        assert f"{wide.occupied:.0f}" in fields["band"].text()

        rate_before = fields["phy_rate"].text()
        workspace.mcs_picker.setCurrentIndex(workspace.mcs_picker.findData(3))
        assert workspace.facts.mcs_index == 3
        assert fields["phy_rate"].text() != rate_before
    finally:
        runtime.close()


def test_the_rung_that_needs_a_faster_sound_card_says_so_where_it_is_chosen(
) -> None:
    # A card that will not open at 96 kHz is a failure an operator should be
    # able to anticipate at the moment of choosing, not one to debug afterwards.
    runtime, workspace = _workspace()
    try:
        assert workspace.facts_note.property("statusRole") == "info"
        assert "measured on a real radio" in workspace.facts_note.text()

        workspace.profile_picker.setCurrentIndex(
            workspace.profile_picker.findData("WIDE_40K")
        )
        note = workspace.facts_note.text()
        assert workspace.facts_note.property("statusRole") == "warning"
        assert "96000" in note
        assert "sound card" in note
        # Every rung is honest about being untried, the fast one included.
        assert "measured on the air" in note
        labels = [
            workspace.profile_picker.itemText(index)
            for index in range(workspace.profile_picker.count())
        ]
        assert all("untried on air" in label for label in labels)
        assert any("96 kHz sound card" in label for label in labels)
    finally:
        runtime.close()


# -- one burst --------------------------------------------------------------- #

def test_one_burst_reports_measured_against_applied_and_every_metric() -> None:
    runtime, workspace = _workspace()
    try:
        workspace.burst_snr.setValue(20.0)
        workspace.burst_payload.setValue(256)
        workspace.start_burst()
        _wait(workspace)

        fields = workspace.burst_fields
        assert workspace.burst_status.property("statusRole") == "success"
        assert fields["payload"].text() == "256 B"
        # Both SNRs, because quoting only the wideband figure would flatter the
        # modem and quoting only the in-band one hides what the audio really is.
        assert "20.0 dB in band" in fields["applied"].text()
        assert "whole audio band" in fields["applied"].text()
        assert "dB" in fields["measured"].text()
        assert fields["measured"].text() != _unavailable()
        assert "%" in fields["evm"].text()
        assert "Hz" in fields["cfo"].text()
        assert float(fields["sync"].text()) > 0.5
        assert "dB" in fields["spread"].text()
        assert "transmitted" in fields["crest"].text()
        assert "received" in fields["crest"].text()
        assert "e-" in fields["ber"].text()
        assert "samples" in fields["airtime"].text()
        assert "ok" in fields["frame"].text()
        # The channel is the simulator with everything switched on, and the
        # report names it rather than implying a clean handover.
        assert "SNR" in fields["channel"].text()
        assert "multipath" in fields["channel"].text()
    finally:
        runtime.close()


def test_metrics_the_receiver_never_measured_read_as_unavailable() -> None:
    # Driven directly with an empty LinkMetrics: a burst the receiver could not
    # measure must not report a confident 0.0 dB, 0 % or 0.00 confidence.
    runtime, workspace = _workspace()
    try:
        facts = bench.describe(profile_or_default("BENCH"), 1)
        workspace._render_burst(bench.BurstResult(
            facts=facts,
            channel="nothing at all",
            payload_bytes=0,
            applied_snr_db=-5.0,
            wideband_offset_db=-10.0,
            samples=0,
            seconds=0.0,
            tx_rms=0.0,
            tx_crest_db=float("nan"),
            metrics=LinkMetrics(error="no burst detected"),
            identical=False,
            uncoded_ber=0.5,
        ))
        fields = workspace.burst_fields
        unavailable = _unavailable()
        assert fields["measured"].text() == unavailable
        assert fields["evm"].text() == unavailable
        assert fields["cfo"].text() == unavailable
        assert fields["sync"].text() == unavailable
        assert fields["spread"].text() == unavailable
        # A NaN crest factor is as much of a non-measurement as a None.
        assert unavailable in fields["crest"].text()
        assert "nan" not in fields["crest"].text().lower()
        for key in ("measured", "evm", "cfo", "sync", "spread"):
            assert fields[key].text() not in ("0", "0.0", "0.00", "0.0 dB", "—")
        assert workspace.burst_status.property("statusRole") == "warning"
        assert "no burst detected" in fields["frame"].text()
    finally:
        runtime.close()


# -- a whole transfer -------------------------------------------------------- #

def test_a_transfer_reports_retries_error_rate_throughput_and_its_log() -> None:
    runtime, workspace = _workspace()
    try:
        workspace.transfer_snr.setValue(20.0)
        workspace.transfer_payload.setValue(1_024)
        workspace.start_transfer()
        _wait(workspace)

        fields = workspace.transfer_fields
        assert workspace.transfer_status.property("statusRole") == "success"
        assert fields["blocks"].text().startswith("2 ×")
        assert fields["acked"].text() == "2 / 2"
        assert fields["retries"].text().isdigit()
        assert "%" in fields["per"].text()
        assert "dB" in fields["measured"].text()
        assert "%" in fields["evm"].text()
        assert fields["airtime"].text().endswith(" s")
        assert "bit/s" in fields["throughput"].text()
        # The keying lead the station is actually configured with, not a figure
        # invented for the measurement.
        assert str(runtime.config.ofdm_tx_lead_ms) in fields["turnaround"].text()
        # The log arrived through on_log while it ran, not afterwards.
        assert workspace.transfer_log.toPlainText().strip()
    finally:
        runtime.close()


def test_the_transfer_log_streams_in_before_the_transfer_finishes() -> None:
    runtime, workspace = _workspace()
    try:
        workspace.transfer_snr.setValue(20.0)
        workspace.transfer_payload.setValue(2_048)
        workspace.start_transfer()
        streamed = False
        deadline = time.monotonic() + 300.0
        while time.monotonic() < deadline:
            _pump(workspace)
            if workspace.transfer_log.toPlainText().strip():
                streamed = streamed or workspace._running is not None
            if workspace._running is None:
                break
            time.sleep(0.01)
        assert workspace._running is None
        assert streamed, "no log line reached the view while the transfer ran"
    finally:
        runtime.close()


# -- decode rate against SNR ------------------------------------------------- #

def test_the_sweep_table_fills_from_each_point_and_names_the_cliff() -> None:
    runtime, workspace = _workspace()
    try:
        workspace.sweep_runs.setValue(1)
        workspace.start_sweep(points=(20.0, 4.0))
        _wait(workspace)

        table = workspace.sweep_table
        assert table.rowCount() == 2
        assert table.item(0, 0).text() == "20.0"
        assert table.item(0, 1).text() == "1 / 1"
        assert table.item(0, 6).text() == tr("modem.reliable")
        assert table.item(1, 0).text() == "4.0"
        assert table.item(1, 6).text() == tr("modem.unreliable")
        # Every point measured an SNR, so no cell may read "unavailable".
        assert table.item(0, 3).text() != _unavailable()
        assert workspace.sweep_status.property("statusRole") == "success"
        summary = workspace.sweep_summary.text()
        assert "20.0 dB" in summary
        assert "4.0 dB" in summary
        assert not workspace.sweep_alarm.isHidden()
        assert workspace.sweep_alarm.property("statusRole") == "success"
    finally:
        runtime.close()


def test_cancelling_a_sweep_stops_it_early_and_keeps_what_was_measured() -> None:
    runtime, workspace = _workspace()
    try:
        ladder = (24.0, 22.0, 20.0, 18.0, 16.0, 14.0, 12.0, 10.0)
        workspace.sweep_runs.setValue(1)
        workspace.start_sweep(points=ladder)
        assert workspace.sweep_cancel.isEnabled()
        deadline = time.monotonic() + 300.0
        while time.monotonic() < deadline:
            _pump(workspace)
            if workspace.sweep_table.rowCount() >= 1:
                workspace.cancel_sweep()
            if workspace._running is None:
                break
            time.sleep(0.01)
        assert workspace._running is None
        assert 0 < workspace.sweep_table.rowCount() < len(ladder)
        assert workspace.sweep_status.property("statusRole") == "warning"
        assert "Cancelled" in workspace.sweep_status.text()
        # What was measured before the stop is still on screen.
        assert workspace.sweep_table.item(0, 1).text() == "1 / 1"
        assert not workspace.sweep_cancel.isEnabled()
    finally:
        runtime.close()


def test_a_block_delivered_with_the_wrong_bytes_is_an_unmissable_alarm() -> None:
    # The one result that matters more than any rate. It cannot be produced on
    # demand, so the render is driven directly -- which is the whole reason the
    # render is a method taking the engine's own dataclass.
    runtime, workspace = _workspace()
    try:
        facts = bench.describe(profile_or_default("BENCH"), 1)
        result = bench.SweepResult(facts=facts, runs=4, points=(
            bench.SweepPoint(applied_snr_db=20.0, runs=4, decoded=4,
                             wrong_bytes=0, measured_snr_db=19.8,
                             measured_evm=0.13, uncoded_ber=1e-20),
            bench.SweepPoint(applied_snr_db=4.0, runs=4, decoded=1,
                             wrong_bytes=2, measured_snr_db=3.9,
                             measured_evm=0.84, uncoded_ber=5e-2),
        ))
        assert result.wrong_byte_deliveries == 2
        workspace._render_sweep(result)

        # Raised to the front, so it cannot be sitting behind another tab.
        assert workspace.tabs.currentWidget() is workspace.sweep_page
        assert workspace.sweep_alarm.isVisibleTo(workspace)
        assert workspace.sweep_alarm.property("statusRole") == "danger"
        assert "2" in workspace.sweep_alarm.text()
        assert "SERIOUS" in workspace.sweep_alarm.text()
        # The status line above the table is taken over too, so the alarm is
        # not something a full table can push out of sight.
        assert workspace.sweep_status.property("statusRole") == "danger"
        # And the offending row is marked, so it is clear which point produced it.
        assert workspace.sweep_table.item(0, 2).text() == "0"
        assert "⚠" in workspace.sweep_table.item(1, 2).text()
        assert "2" in workspace.sweep_table.item(1, 2).text()
    finally:
        runtime.close()


def test_a_clean_sweep_says_no_block_was_ever_delivered_corrupted() -> None:
    runtime, workspace = _workspace()
    try:
        facts = bench.describe(profile_or_default("BENCH"), 1)
        workspace._render_sweep(bench.SweepResult(facts=facts, runs=1, points=(
            bench.SweepPoint(applied_snr_db=4.0, runs=1, decoded=0,
                             wrong_bytes=0, measured_snr_db=None,
                             measured_evm=None, uncoded_ber=5e-2),
        )))
        assert workspace.sweep_alarm.property("statusRole") == "success"
        assert "No block" in workspace.sweep_alarm.text()
        # A point that decoded nothing measured nothing, and says so.
        assert workspace.sweep_table.item(0, 3).text() == _unavailable()
        assert workspace.sweep_table.item(0, 4).text() == _unavailable()
    finally:
        runtime.close()


# -- files ------------------------------------------------------------------- #

def test_saving_a_transmit_test_file_writes_a_clean_decodable_waveform(
    tmp_path, monkeypatch
) -> None:
    runtime, workspace = _workspace()
    target = tmp_path / "txtest.wav"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *args, **kwargs: (str(target), "")),
    )
    try:
        workspace.file_repeats.setValue(2)
        workspace.file_payload.setValue(256)
        workspace.file_gap.setValue(0.5)
        workspace.save_test_file()
        _wait(workspace)

        assert target.is_file()
        assert workspace.file_status.property("statusRole") == "success"
        assert str(target) in workspace.file_status.text()
        assert "48000 Hz" in workspace.file_status.text()
        # This is the file that goes on the air, so it has to be the waveform
        # with no channel applied -- which means it decodes perfectly.
        decoded = bench.decode_capture(profile_or_default("BENCH"), target)
        assert decoded.passed
        assert decoded.header is not None
        assert decoded.header.block_count == 2
    finally:
        runtime.close()


def test_opening_a_wav_recorded_at_another_rate_says_so(tmp_path, monkeypatch) -> None:
    # The one failure that looks exactly like a dead channel: the demodulator
    # finds nothing and no number explains why.
    runtime, workspace = _workspace()
    source = tmp_path / "wrong-rate.wav"
    bench.write_wav(source, [0.0, 0.1, -0.1] * 1_000, 8_000)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(source), "")),
    )
    try:
        workspace.open_capture()
        _wait(workspace)
        assert workspace.capture_status.property("statusRole") == "warning"
        assert "8000" in workspace.capture_status.text()
        assert "48000" in workspace.capture_status.text()
        assert workspace.capture_fields["frame"].text() != _unavailable()
    finally:
        runtime.close()


def test_a_wav_that_holds_a_burst_decodes_and_reports_its_frame(
    tmp_path, monkeypatch
) -> None:
    runtime, workspace = _workspace()
    source = tmp_path / "burst.wav"
    bench.make_test_burst(profile_or_default("BENCH"), 1, payload_bytes=128,
                          repeats=1, gap_seconds=0.5, wav_path=source)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(source), "")),
    )
    try:
        workspace.open_capture()
        _wait(workspace)
        assert workspace.capture_status.property("statusRole") == "success"
        assert "BENCH" in workspace.capture_status.text()
        fields = workspace.capture_fields
        assert str(source) == fields["path"].text()
        assert "48000 Hz in the file" in fields["rate"].text()
        assert "samples" in fields["length"].text()
        assert "RMS" in fields["levels"].text()
        assert fields["levels"].text().count(_unavailable()) == 0
        assert "DATA" in fields["frame"].text()
        assert "dB" in fields["snr"].text()
    finally:
        runtime.close()


# -- one at a time ----------------------------------------------------------- #

def test_only_one_measurement_runs_at_a_time_and_the_buttons_say_so() -> None:
    runtime, workspace = _workspace()
    try:
        workspace.sweep_runs.setValue(1)
        workspace.start_sweep(points=(24.0, 22.0, 20.0, 18.0, 16.0, 14.0))
        assert workspace._running == SWEEP_TASK
        assert runtime.operations.workers.is_active(SWEEP_TASK)
        for button in (workspace.burst_button, workspace.transfer_button,
                       workspace.sweep_button, workspace.save_button,
                       workspace.open_button):
            assert not button.isEnabled()
        # Even reached past the buttons, a second measurement is refused rather
        # than queued behind the first.
        workspace.start_burst()
        assert workspace.burst_status.text() == tr("modem.busy")
        assert workspace.burst_status.property("statusRole") == "warning"
        workspace.cancel_sweep()
        _wait(workspace)
        for button in (workspace.burst_button, workspace.transfer_button,
                       workspace.sweep_button, workspace.save_button,
                       workspace.open_button):
            assert button.isEnabled()
        assert not workspace.sweep_cancel.isEnabled()
    finally:
        runtime.close()


def test_a_failed_measurement_is_reported_and_leaves_the_workspace_usable(
    tmp_path, monkeypatch
) -> None:
    runtime, workspace = _workspace()
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *args, **kwargs: (
            str(tmp_path / "nope" / "tx.wav"), ""
        )),
    )
    monkeypatch.setattr(
        "guardian.qt.modem_workspace.bench.make_test_burst",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk is full")),
    )
    try:
        workspace.save_test_file()
        _wait(workspace)
        assert workspace.file_status.property("statusRole") == "warning"
        assert "disk is full" in workspace.file_status.text()
        assert workspace.save_button.isEnabled()
    finally:
        runtime.close()


# -- reachable from the application, and bilingual --------------------------- #

def test_the_shell_offers_the_modem_test_as_a_workspace(tmp_path) -> None:
    # The requirement in the operator's words: part of the app, no PowerShell.
    _application()
    set_language(Language.ENGLISH)
    settings = QSettings(
        str(tmp_path / "modem-shell.ini"), QSettings.Format.IniFormat
    )
    settings.setValue("ui/theme", ThemePreference.LIGHT.value)
    runtime = ShellRuntime()
    window = GuardianMainWindow(runtime, settings)
    try:
        workspace = window.workspace_names["modem"]
        assert isinstance(workspace, ModemWorkspace)
        assert "modem" in window.workspace_actions
        window._show_workspace("modem")
        assert window.workspace_stack.currentWidget() is workspace
        assert window.workspace_actions["modem"].isChecked()
        assert "Modem test" in window.statusBar().currentMessage()
        # Listed in Tools as well, where an operator looks for something that
        # measures the station.
        menu_entries = {
            entry.text()
            for menu in window.menuBar().findChildren(QMenu)
            for entry in menu.actions()
        }
        assert "Modem test" in menu_entries
    finally:
        window.close()
        runtime.close()


def test_the_workspace_is_bilingual() -> None:
    keys = [key for key in TRANSLATIONS if key.startswith("modem.")]
    for key in (
        "modem.title",
        "modem.intro",
        "modem.profile",
        "modem.mcs",
        "modem.facts",
        "modem.untried",
        "modem.needs_fast_card",
        "modem.run_burst",
        "modem.run_transfer",
        "modem.run_sweep",
        "modem.cancel",
        "modem.busy",
        "modem.task_failed",
        "modem.wrong_bytes_alarm",
        "modem.wrong_bytes_none",
        "modem.save_tx",
        "modem.open_wav",
    ):
        assert key in keys, key
    for key in keys + ["menu.modem"]:
        english, czech = TRANSLATIONS[key]
        assert english and czech and english != czech, key

    _application()
    set_language(Language.CZECH)
    runtime = ShellRuntime()
    runtime.config.ofdm_profile = "BENCH"
    workspace = ModemWorkspace(runtime)
    try:
        assert workspace.burst_button.text() == "Změřit jedno vysílání"
        assert workspace.sweep_button.text() == "Spustit rozmítání"
        assert workspace.sweep_cancel.text() == "Přerušit"
        assert workspace.save_button.text() == "Uložit soubor pro vysílání…"
        assert "nezkoušený" in workspace.profile_picker.currentText()
        assert "to není šířka pásma" in (
            workspace.facts_fields["sample_rate"].text()
        )
        assert "datových" in workspace.facts_fields["carriers"].text()
        assert "pilotních" in workspace.facts_fields["carriers"].text()
        assert workspace.tabs.tabText(2) == "Úspěšnost podle odstupu"
    finally:
        runtime.close()
        set_language(Language.ENGLISH)
