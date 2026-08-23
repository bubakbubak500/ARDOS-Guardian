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
import threading
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox

from guardian.i18n import Language, TRANSLATIONS, set_language, tr
from guardian.ofdm import MCS_TABLE
from guardian.ofdm import bench
from guardian.ofdm.config import PROFILE_LADDER, profile_or_default
from guardian.ofdm.metrics import LinkMetrics
from guardian.qt.modem_workspace import (
    SWEEP_TASK,
    TRANSMIT_TASK,
    ModemWorkspace,
)
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


def _answers(monkeypatch, button: QMessageBox.StandardButton) -> list[tuple]:
    """Answer the transmit confirmation, and keep what it said.

    Recorded as (title, text, default button): the wording matters as much as
    the answer, because this is the dialog standing between a click and a keyed
    transmitter.
    """
    asked: list[tuple] = []

    def question(*args):
        asked.append((args[1], args[2], args[-1]))
        return button

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    return asked


def _stub_transmit(runtime: ShellRuntime, monkeypatch, aired: float | None = 4.0,
                   hold: threading.Event | None = None) -> list[dict]:
    """Stand in for `Operations.transmit_test_burst`.

    The real one keys a PTT line and opens a sound device. What these tests are
    about is the button, the confirmation and the report, so the transmission
    itself is a recorded call -- `hold` lets one be caught mid-air.
    """
    calls: list[dict] = []

    def transmit(*, profile_name=None, mcs_index=None, payload_bytes=512,
                 repeats=3, on_log=None, fec=None):
        calls.append({
            "profile_name": profile_name, "mcs_index": mcs_index,
            "payload_bytes": payload_bytes, "repeats": repeats,
            "fec": fec,
        })
        if hold is not None:
            assert hold.wait(60.0), "the transmission was never released"
        return aired

    monkeypatch.setattr(runtime.operations, "transmit_test_burst", transmit)
    return calls


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

        # The developer regression page is deliberately absent from the
        # operator-facing tab bar.
        assert workspace.tabs.indexOf(workspace.sweep_page) == -1
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

def test_live_recording_uses_and_keeps_the_profile_selected_in_modem_test(
    tmp_path, monkeypatch
) -> None:
    runtime, workspace = _workspace("WIDE_10K")
    path = tmp_path / "selected-profile.wav"
    state = {"active": False, "rates": [], "decoded": []}

    def start(*, sample_rate, exclusive=False):
        state["active"] = True
        state["rates"].append(sample_rate)
        state["exclusive"] = exclusive
        return path

    def stop():
        state["active"] = False
        return SimpleNamespace(path=path)

    monkeypatch.setattr(runtime.operations, "recording_active",
                        lambda: state["active"])
    monkeypatch.setattr(runtime.operations, "recording_seconds", lambda: 0.0)
    monkeypatch.setattr(runtime.operations, "recording_level", lambda: 0.0)
    monkeypatch.setattr(runtime.operations, "start_recording", start)
    monkeypatch.setattr(runtime.operations, "stop_recording", stop)
    monkeypatch.setattr(
        bench, "decode_capture",
        lambda profile, source: state["decoded"].append((profile.name, source)),
    )
    monkeypatch.setattr(
        workspace, "_submit",
        lambda task, work, status, render: (work(), True)[1],
    )
    try:
        selected = workspace.selected_profile()
        workspace.record_button.click()
        assert state["rates"] == [selected.sample_rate]
        assert state["exclusive"]
        assert not workspace.profile_picker.isEnabled()

        workspace.record_button.click()
        assert state["decoded"] == [(selected.name, path)]
    finally:
        runtime.close()

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


# -- putting it on the air --------------------------------------------------- #

def test_cancelling_the_confirmation_puts_nothing_on_the_air(monkeypatch) -> None:
    # The whole point of the dialog. If Cancel could still key a transmitter
    # nothing else about this feature would matter.
    runtime, workspace = _workspace()
    asked = _answers(monkeypatch, QMessageBox.StandardButton.Cancel)
    calls = _stub_transmit(runtime, monkeypatch)
    try:
        workspace.file_repeats.setValue(2)
        workspace.file_payload.setValue(256)
        workspace.start_transmit()
        assert calls == []
        assert workspace._running is None
        assert workspace.transmit_button.isEnabled()

        # And it asked properly: named the transmission, roughly how long, the
        # waveform, where the radio is, which device -- and defaulted to Cancel.
        assert len(asked) == 1
        title, text, default = asked[0]
        assert title == "Transmit into the radio"
        assert default == QMessageBox.StandardButton.Cancel
        assert "key the transmitter" in text
        assert "BENCH" in text
        assert "MCS1" in text
        assert "2 burst(s) of 256 B" in text
        assert "Frequency:" in text
        assert "Transmit device:" in text
        assert "Identify with your callsign" in text
        assert "experimental waveform" in text
        # The estimate is in the sentence about how long it will transmit for.
        expected = workspace.transmit_seconds(256, 2)
        assert expected > 2.0
        assert f"about {expected:.0f} s" in text
    finally:
        runtime.close()


def test_the_confirmation_names_the_frequency_and_device_guardian_knows(
    monkeypatch
) -> None:
    runtime, workspace = _workspace()
    # A no-CAT station: the frequency Guardian knows is the one the operator
    # typed, and it is still worth quoting back before keying.
    runtime.config.radio_backend = "hamlib"
    runtime.config.rig_model = 1
    runtime.config.manual_frequency_hz = 145_500_000
    runtime.config.audio_output = "USB Audio CODEC"
    asked = _answers(monkeypatch, QMessageBox.StandardButton.Cancel)
    _stub_transmit(runtime, monkeypatch)
    try:
        workspace.start_transmit()
        text = asked[0][1]
        assert "145.5000 MHz" in text
        assert "USB Audio CODEC" in text
    finally:
        runtime.close()


def test_a_station_with_no_frequency_or_device_says_so_rather_than_zero(
    monkeypatch
) -> None:
    runtime, workspace = _workspace()
    runtime.config.audio_output = ""
    monkeypatch.setattr(runtime.operations, "current_frequency", lambda: None)
    asked = _answers(monkeypatch, QMessageBox.StandardButton.Cancel)
    _stub_transmit(runtime, monkeypatch)
    try:
        workspace.start_transmit()
        text = asked[0][1]
        assert tr("modem.transmit_frequency_unknown") in text
        assert tr("modem.transmit_device_unset") in text
        assert "0.0000 MHz" not in text
    finally:
        runtime.close()


def test_confirming_transmits_the_waveform_selected_here_not_the_saved_one(
    monkeypatch
) -> None:
    # An operator measuring a rung has selected it in this workspace and has no
    # reason to have saved it as the station's own profile.
    runtime, workspace = _workspace()
    _answers(monkeypatch, QMessageBox.StandardButton.Ok)
    calls = _stub_transmit(runtime, monkeypatch, aired=6.25)
    try:
        workspace.profile_picker.setCurrentIndex(
            workspace.profile_picker.findData("WIDE_10K")
        )
        workspace.mcs_picker.setCurrentIndex(workspace.mcs_picker.findData(3))
        workspace.file_repeats.setValue(2)
        workspace.file_payload.setValue(256)
        workspace.start_transmit()
        assert workspace._running == TRANSMIT_TASK
        _wait(workspace)

        assert runtime.config.ofdm_profile == "BENCH"
        assert runtime.config.ofdm_mcs == 1
        assert calls == [{
            "profile_name": "WIDE_10K", "mcs_index": 3,
            "payload_bytes": 256, "repeats": 2,
            "fec": workspace.selected_fec(),
        }]
        assert workspace.transmit_status.property("statusRole") == "success"
        assert "6.2 s aired" in workspace.transmit_status.text()
    finally:
        runtime.close()


def test_the_page_says_the_radio_is_live_while_it_transmits(monkeypatch) -> None:
    # The one action where the useful thing is not a number at the end.
    runtime, workspace = _workspace()
    _answers(monkeypatch, QMessageBox.StandardButton.Ok)
    release = threading.Event()
    _stub_transmit(runtime, monkeypatch, aired=3.5, hold=release)
    try:
        workspace.file_repeats.setValue(1)
        workspace.file_payload.setValue(256)
        workspace.start_transmit()
        assert workspace.transmit_status.property("statusRole") == "danger"
        live = workspace.transmit_status.text()
        assert "ON THE AIR" in live
        assert "keyed" in live
        expected = workspace.transmit_seconds(256, 1)
        assert f"about {expected:.0f} s" in live

        # And the elapsed figure moves with the ordinary shell poll.
        time.sleep(0.2)
        _pump(workspace)
        assert workspace.transmit_status.text() != live

        release.set()
        _wait(workspace)
        assert "3.5 s aired" in workspace.transmit_status.text()
        assert "ON THE AIR" not in workspace.transmit_status.text()
    finally:
        release.set()
        runtime.close()


def test_a_transmission_that_never_happened_is_never_reported_as_success(
    monkeypatch
) -> None:
    # `transmit_test_burst` returns None for a payload transfer holding the
    # codec, an unresolved TX device or a PortAudio refusal, and has already
    # logged which. The one unacceptable outcome is a page claiming airtime.
    runtime, workspace = _workspace()
    _answers(monkeypatch, QMessageBox.StandardButton.Ok)
    calls = _stub_transmit(runtime, monkeypatch, aired=None)
    try:
        workspace.start_transmit()
        _wait(workspace)
        assert len(calls) == 1
        text = workspace.transmit_status.text()
        assert workspace.transmit_status.property("statusRole") == "warning"
        assert "Nothing was transmitted" in text
        # Pointed at the Log, where the backend put the real reason.
        assert "Log" in text
        assert "aired" not in text
        # No invented reason: the backend logged the real one.
        assert "device" not in text
        assert workspace.transmit_button.isEnabled()
    finally:
        runtime.close()


def test_a_second_transmission_is_refused_without_asking_again(monkeypatch) -> None:
    runtime, workspace = _workspace()
    asked = _answers(monkeypatch, QMessageBox.StandardButton.Ok)
    release = threading.Event()
    calls = _stub_transmit(runtime, monkeypatch, aired=2.0, hold=release)
    try:
        workspace.start_transmit()
        assert workspace._running == TRANSMIT_TASK
        assert not workspace.transmit_button.isEnabled()
        # Reached past the disabled button, the second click neither transmits
        # nor puts up a dialog authorising something that cannot start.
        workspace.start_transmit()
        assert len(calls) == 1
        assert len(asked) == 1
        assert workspace.transmit_status.text() == tr("modem.busy")
        assert workspace.transmit_status.property("statusRole") == "warning"
        release.set()
        _wait(workspace)
        assert len(calls) == 1
    finally:
        release.set()
        runtime.close()


def test_a_transmission_that_raises_is_reported_and_stops_counting(
    monkeypatch
) -> None:
    runtime, workspace = _workspace()
    _answers(monkeypatch, QMessageBox.StandardButton.Ok)

    def explode(**kwargs):
        raise OSError("PortAudio would not open the device")

    monkeypatch.setattr(runtime.operations, "transmit_test_burst", explode)
    try:
        workspace.start_transmit()
        _wait(workspace)
        assert workspace.transmit_status.property("statusRole") == "warning"
        assert "PortAudio" in workspace.transmit_status.text()
        # Nothing left claiming to be on the air.
        assert workspace._transmit_started is None
        workspace.refresh()
        assert "ON THE AIR" not in workspace.transmit_status.text()
        assert workspace.transmit_button.isEnabled()
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
                       workspace.transmit_button, workspace.open_button):
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
                       workspace.transmit_button, workspace.open_button):
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
        menus = {
            menu.title().replace("&", ""): [
                entry.text().replace("&", "") for entry in menu.actions()
            ]
            for menu in window.menuBar().findChildren(QMenu)
        }
        assert menus["View"].count("Modem test") == 1
        assert "Modem test" not in menus["Tools"]
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
        "modem.transmit",
        "modem.transmit_hint",
        "modem.transmit_title",
        "modem.transmit_confirm",
        "modem.transmit_live",
        "modem.transmit_done",
        "modem.transmit_none",
        "modem.transmit_frequency_unknown",
        "modem.transmit_device_unset",
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
        assert workspace.transmit_button.text() == "Vyslat do rádia…"
        assert "nezkoušený" in workspace.profile_picker.currentText()
        assert "to není šířka pásma" in (
            workspace.facts_fields["sample_rate"].text()
        )
        assert "datových" in workspace.facts_fields["carriers"].text()
        assert "pilotních" in workspace.facts_fields["carriers"].text()
        assert workspace.tabs.count() == 1
        assert workspace.tabs.tabText(0) == "Soubory"
        assert all("přenos" not in workspace.tabs.tabText(index).lower()
                   for index in range(workspace.tabs.count()))
        assert all("odstup" not in workspace.tabs.tabText(index).lower()
                   for index in range(workspace.tabs.count()))
    finally:
        runtime.close()
        set_language(Language.ENGLISH)


def test_modem_lab_exposes_all_g2_waveform_families() -> None:
    runtime, workspace = _workspace()
    try:
        offered = {
            workspace.family_picker.itemData(index)
            for index in range(workspace.family_picker.count())
        }
        assert offered == {"ofdm", "sc_hs", "sc_ftn", "sc_fde_ftn", "sefdm"}

        workspace.family_picker.setCurrentIndex(
            workspace.family_picker.findData("sc_ftn")
        )
        workspace.profile_picker.setCurrentIndex(
            workspace.profile_picker.findData("SC_FTN_2K7")
        )
        assert workspace.selected_profile().name == "SC_FTN_2K7"
        assert workspace.facts.profile == "SC_FTN_2K7"
        assert "τ=0.90" in workspace.facts_fields["spacing"].text()
        assert workspace.mcs_picker.findData(5) >= 0  # 16-APSK
        assert workspace.mcs_picker.findData(6) >= 0  # 32-APSK
        assert workspace.mcs_picker.findData(11) >= 0  # 128-APSK
        assert workspace.profile_picker.count() == 5

        workspace.family_picker.setCurrentIndex(
            workspace.family_picker.findData("sc_fde_ftn")
        )
        workspace.profile_picker.setCurrentIndex(
            workspace.profile_picker.findData("SC_FDE_FTN_20K")
        )
        assert workspace.selected_profile().name == "SC_FDE_FTN_20K"
        assert "RRC" in workspace.facts_fields["fft"].text()

        workspace.family_picker.setCurrentIndex(
            workspace.family_picker.findData("sefdm")
        )
        workspace.profile_picker.setCurrentIndex(
            workspace.profile_picker.findData("SEFDM_2K7")
        )
        assert workspace.selected_profile().name == "SEFDM_2K7"
        assert "α=0.985" in workspace.facts_fields["spacing"].text()
        assert workspace.selected_mcs() == 6
        assert "32-APSK" in workspace.facts_note.text()
    finally:
        runtime.close()


def test_the_transmit_confirmation_and_report_read_in_czech(monkeypatch) -> None:
    # This feature exists because a Czech-speaking operator asked how he was
    # supposed to play the file into the radio. The dialog that keys his
    # transmitter has to read as well in Czech as in English.
    _application()
    set_language(Language.CZECH)
    runtime = ShellRuntime()
    runtime.config.ofdm_profile = "BENCH"
    runtime.config.ofdm_mcs = 1
    runtime.config.audio_output = "USB Audio CODEC"
    workspace = ModemWorkspace(runtime)
    asked = _answers(monkeypatch, QMessageBox.StandardButton.Cancel)
    calls = _stub_transmit(runtime, monkeypatch, aired=7.0)
    try:
        workspace.file_repeats.setValue(3)
        workspace.file_payload.setValue(512)
        workspace.start_transmit()
        assert calls == []
        title, text, default = asked[0]
        assert title == "Vyslat do rádia"
        assert default == QMessageBox.StandardButton.Cancel
        assert "sepne vysílač" in text
        assert "dávek: 3 po 512 B" in text
        assert "Kmitočet:" in text
        assert "Vysílací zařízení: USB Audio CODEC" in text
        assert "volací značkou" in text
        assert "experimentální vlnový průběh" in text

        # And the reports, both outcomes, driven directly.
        workspace._transmit_started = time.monotonic()
        workspace._transmit_expected = 12.0
        workspace._show_transmit_progress()
        assert "VYSÍLÁ SE" in workspace.transmit_status.text()
        assert "Vysílač je sepnutý" in workspace.transmit_status.text()

        workspace._render_transmitted(7.0)
        assert "Odvysíláno: 7.0 s" in workspace.transmit_status.text()
        assert workspace.transmit_status.property("statusRole") == "success"

        workspace._render_transmitted(None)
        assert "Nic nebylo vysláno" in workspace.transmit_status.text()
        assert workspace.transmit_status.property("statusRole") == "warning"
    finally:
        runtime.close()
        set_language(Language.ENGLISH)
