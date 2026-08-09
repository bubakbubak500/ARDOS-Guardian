"""The capture result dialog: what a recording was, and what was in it.

The dialog exists so an operator standing at the radio learns two things before
packing up -- whether the WAV file is usable at all, and whether a burst was
actually in it. These tests hold it to both, including the case that matters most
in a report: a figure the receiver never measured must read as "unavailable" and
never as a zero somebody could believe.
"""

from __future__ import annotations

import os
import time
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtWidgets import QApplication

from guardian.i18n import Language, TRANSLATIONS, set_language
from guardian.modem.recorder import RecordingSummary
from guardian.ofdm import BENCH, PhyHeader, build_burst
from guardian.ofdm.channel import Channel, ChannelSpec
from guardian.ofdm.config import profile_or_default
from guardian.ofdm.framing import OfdmFrameType
from guardian.qt.capture_dialog import (
    CaptureResultDialog,
    analyse_capture,
    read_wav,
)
from guardian.qt.runtime import ShellRuntime


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _write_wav(path, samples, rate: int = BENCH.sample_rate) -> None:
    """Write floats as mono 16-bit PCM, exactly as `WavRecorder` does."""
    data = (np.clip(np.asarray(samples, dtype=np.float64), -1.0, 1.0) * 32767)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(data.astype("<i2").tobytes())


def _decodable_capture(path) -> bytes:
    """A real burst, buried in silence and noise the way a capture holds one."""
    payload = np.random.default_rng(11).integers(
        0, 256, 256, dtype=np.uint8
    ).tobytes()
    header = PhyHeader(OfdmFrameType.DATA, msg_id=42, mcs=1,
                       payload_len=len(payload))
    burst = build_burst(BENCH, header, payload)
    spec = ChannelSpec(snr_db=24.0, gain=0.5, delay=4_000, trailing=4_000)
    _write_wav(path, Channel(BENCH, spec, seed=7)(burst))
    return payload


def _noise_capture(path, seconds: float = 0.5) -> None:
    """A recording of a channel with nothing on it."""
    count = int(BENCH.sample_rate * seconds)
    _write_wav(path, np.random.default_rng(3).normal(0.0, 0.02, count))


def _summary(path, samples: int = 24_000, **overrides) -> RecordingSummary:
    fields = dict(
        path=path,
        sample_rate=BENCH.sample_rate,
        samples=samples,
        rms=0.05,
        peak=0.4,
        clipped_samples=0,
        dropped_blocks=0,
    )
    fields.update(overrides)
    return RecordingSummary(**fields)


def _drain_until(runtime, done, timeout: float = 30.0) -> None:
    """Stand in for the UI poll: drain the worker pool until the task lands."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runtime.drain_workers()
        if done():
            return
        time.sleep(0.01)
    runtime.drain_workers()


def _run_analysis(dialog, runtime) -> None:
    """Click Analyse and let the worker pool's completion reach the UI thread."""
    dialog.analyse()
    _drain_until(runtime, lambda: not dialog._analysing)
    assert dialog.analysis is not None, dialog.analysis_status.text()


def test_the_wav_reader_round_trips_what_the_recorder_writes(tmp_path) -> None:
    # The reader is written out here rather than imported from tools/, so it has
    # to be held to the same scaling the recorder uses.
    path = tmp_path / "capture.wav"
    wanted = np.array([0.0, 0.5, -0.5, 0.999], dtype=np.float64)
    _write_wav(path, wanted, rate=8_000)
    data, rate = read_wav(path)
    assert rate == 8_000
    assert np.allclose(data, wanted, atol=1e-4)


def test_the_dialog_reports_every_level_and_the_verdict_it_earned(tmp_path) -> None:
    _application()
    runtime = ShellRuntime()
    path = tmp_path / "capture-good.wav"
    _noise_capture(path)
    dialog = CaptureResultDialog(runtime, _summary(path), None)
    try:
        assert str(path) in dialog.path_label.text()
        assert dialog.fields["duration"].text() == "0.50 s"
        assert dialog.fields["sample_rate"].text() == f"{BENCH.sample_rate} Hz"
        assert "dBFS" in dialog.fields["peak"].text()
        assert "dBFS" in dialog.fields["rms"].text()
        assert "dB" in dialog.fields["crest"].text()
        assert dialog.fields["clipped"].text() == "0"
        # A clean capture has no dropped blocks, and a permanent "0" row is a
        # row the eye stops reading.
        assert "dropped" not in dialog.fields
        assert dialog.verdict.text() == _summary(path).verdict()
        assert dialog.verdict.property("statusRole") == "success"
    finally:
        dialog.close()
        runtime.close()


def test_an_unusable_capture_is_styled_as_a_warning_and_names_dropped_blocks(
    tmp_path,
) -> None:
    _application()
    runtime = ShellRuntime()
    path = tmp_path / "capture-clipped.wav"
    _noise_capture(path)
    summary = _summary(path, peak=1.0, clipped_samples=412, dropped_blocks=3)
    dialog = CaptureResultDialog(runtime, summary, None)
    try:
        assert not summary.usable
        assert dialog.verdict.property("statusRole") == "warning"
        assert "clipping" in dialog.verdict.text()
        assert dialog.fields["clipped"].text() == "412"
        assert dialog.fields["dropped"].text() == "3"
    finally:
        dialog.close()
        runtime.close()


def test_levels_that_were_never_measured_read_as_unavailable(tmp_path) -> None:
    # peak_dbfs and rms_dbfs floor at a 1e-9 guard, so an empty capture would
    # otherwise report a confident -180 dBFS and a NaN crest factor.
    _application()
    runtime = ShellRuntime()
    path = tmp_path / "capture-empty.wav"
    _write_wav(path, np.zeros(0))
    dialog = CaptureResultDialog(
        runtime, _summary(path, samples=0, rms=0.0, peak=0.0), None
    )
    try:
        unavailable = TRANSLATIONS["record.unavailable"][0]
        assert dialog.fields["peak"].text() == unavailable
        assert dialog.fields["rms"].text() == unavailable
        assert dialog.fields["crest"].text() == unavailable
        assert "-180" not in dialog.fields["peak"].text()
        assert "nan" not in dialog.fields["crest"].text().lower()
    finally:
        dialog.close()
        runtime.close()


def test_analysing_a_real_capture_reports_what_the_modem_decoded(tmp_path) -> None:
    _application()
    runtime = ShellRuntime()
    runtime.config.ofdm_profile = "BENCH"
    path = tmp_path / "capture-burst.wav"
    _decodable_capture(path)
    dialog = CaptureResultDialog(runtime, _summary(path, samples=48_000), None)
    try:
        # Nothing is claimed before the operator asks for it.
        assert not dialog.analysis_fields["snr"].isVisibleTo(dialog)
        _run_analysis(dialog, runtime)
        assert dialog.analysis.decoded.ok
        assert dialog.analysis.sample_rate == BENCH.sample_rate
        assert dialog.analysis_fields["burst"].text() == "found"
        assert "dB" in dialog.analysis_fields["snr"].text()
        assert "%" in dialog.analysis_fields["evm"].text()
        assert "Hz" in dialog.analysis_fields["cfo"].text()
        assert float(dialog.analysis_fields["sync"].text()) > 0.5
        header = dialog.analysis.decoded.header
        assert header is not None
        assert dialog.analysis_fields["header"].text() == header.summary()
        assert "id=42" in dialog.analysis_fields["header"].text()
        assert dialog.analysis_status.property("statusRole") == "success"
        assert dialog.analyse_button.isEnabled()
    finally:
        dialog.close()
        runtime.close()


def test_analysing_noise_says_no_burst_rather_than_inventing_numbers(
    tmp_path,
) -> None:
    _application()
    runtime = ShellRuntime()
    runtime.config.ofdm_profile = "BENCH"
    path = tmp_path / "capture-noise.wav"
    _noise_capture(path)
    dialog = CaptureResultDialog(runtime, _summary(path), None)
    try:
        _run_analysis(dialog, runtime)
        assert not dialog.analysis.decoded.ok
        unavailable = TRANSLATIONS["record.unavailable"][0]
        if dialog.analysis.burst_found:
            # A detector false alarm in noise is legitimate; what must not
            # happen is a decoded frame or a silent zero in place of a figure.
            assert dialog.analysis.decoded.header is None
        else:
            assert dialog.analysis_fields["burst"].text() == "none found"
            assert dialog.analysis_fields["snr"].text() == unavailable
            assert dialog.analysis_fields["sync"].text() == unavailable
            assert dialog.analysis_fields["cfo"].text() == unavailable
        # Either way the failure is named, and never as a dash or a zero.
        assert dialog.analysis_fields["evm"].text() == unavailable
        assert dialog.analysis_fields["header"].text() not in ("", "0", "—")
        assert dialog.analysis_status.property("statusRole") == "warning"
    finally:
        dialog.close()
        runtime.close()


def test_a_capture_that_cannot_be_read_is_reported_not_raised(tmp_path) -> None:
    _application()
    runtime = ShellRuntime()
    path = tmp_path / "capture-missing.wav"
    dialog = CaptureResultDialog(runtime, _summary(path), None)
    try:
        dialog.analyse()
        _drain_until(runtime, lambda: not dialog._analysing)
        assert dialog.analysis is None
        assert dialog.analysis_status.property("statusRole") == "warning"
        assert "could not be decoded" in dialog.analysis_status.text()
        # The button comes back: a failed read is not a dead dialog.
        assert dialog.analyse_button.isEnabled()
    finally:
        dialog.close()
        runtime.close()


def test_opening_the_folder_asks_the_desktop_for_the_captures_directory(
    tmp_path, monkeypatch
) -> None:
    _application()
    runtime = ShellRuntime()
    path = tmp_path / "captures" / "capture-20260808-101500.wav"
    path.parent.mkdir(parents=True)
    _noise_capture(path)
    asked: list[str] = []
    monkeypatch.setattr(
        "guardian.qt.capture_dialog.QDesktopServices.openUrl",
        staticmethod(lambda url: asked.append(url.toLocalFile()) or True),
    )
    dialog = CaptureResultDialog(runtime, _summary(path), None)
    try:
        dialog.open_folder()
        assert asked == [str(path.parent).replace("\\", "/")]
    finally:
        dialog.close()
        runtime.close()


def test_analysis_helper_names_the_profile_it_used(tmp_path) -> None:
    path = tmp_path / "capture-profile.wav"
    _decodable_capture(path)
    analysis = analyse_capture(path, profile_or_default("BENCH"))
    assert analysis.profile_name == "BENCH"
    assert analysis.sample_rate == BENCH.sample_rate
    assert analysis.samples > 0
    assert analysis.burst_found
    assert analysis.decoded.ok


def test_the_dialog_is_bilingual(tmp_path) -> None:
    keys = [key for key in TRANSLATIONS if key.startswith("record.")]
    # Named individually as well as swept, so a key that is deleted rather than
    # renamed fails here instead of silently reducing the sweep to nothing.
    for key in (
        "record.start",
        "record.stop",
        "record.idle",
        "record.live",
        "record.live_clipping",
        "record.live_silent",
        "record.start_failed",
        "record.result_title",
        "record.open_folder",
        "record.analyse",
        "record.analyse_hint",
        "record.analysing",
        "record.analyse_busy",
        "record.unavailable",
    ):
        assert key in keys, key
    for key in keys:
        english, czech = TRANSLATIONS[key]
        assert english and czech and english != czech, key

    _application()
    set_language(Language.CZECH)
    runtime = ShellRuntime()
    path = tmp_path / "capture-czech.wav"
    _noise_capture(path)
    dialog = CaptureResultDialog(runtime, _summary(path), None)
    try:
        assert dialog.windowTitle() == "Zaznamenaný záznam"
        assert dialog.open_folder_button.text() == "Otevřít umístění souboru"
        assert dialog.analyse_button.text() == "Analyzovat záznam"
        captions = {
            label.text() for label in dialog.analysis_captions.values()
        }
        assert "Odstup linky (co modem dostal)" in captions
        assert "Odstup jen vůči šumu" in captions
        assert "Co tento odstup unese" in captions
        assert "Kmitočtová odchylka" in captions
        dialog_fields = {
            label.text()
            for label in dialog.findChildren(type(dialog.path_label))
        }
        assert "Špičková úroveň" in dialog_fields
        assert "Činitel výkyvu" in dialog_fields
    finally:
        dialog.close()
        runtime.close()
        set_language(Language.ENGLISH)
