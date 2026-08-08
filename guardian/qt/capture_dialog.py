"""What a recorded WAV capture contains, and what the modem makes of it.

A capture is only worth carrying away if it is neither silent nor clipped, and
the one place that judgement is cheap is at the radio -- while the other station
is still on the air and a second attempt costs nothing. So this dialog answers
two questions in that order: is the file any good, and was there actually a burst
in it. The first is the recorder's own arithmetic and is free; the second is a
full decode and is offered as a button, because it costs real time.

The file is closed and on disk before this opens. Nothing here can lose it, and
closing the dialog is never destructive -- which is why it is not modal.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..i18n import dual, tr
from ..modem.recorder import RecordingSummary
from ..ofdm import DecodedBurst, decode_burst
from ..ofdm.bench import read_wav as _read_wav
from ..ofdm.config import profile_or_default
from ..services import TaskResult
from .measurements import measurement as _measurement, repolish as _repolish

#: The worker-pool task name, so two clicks cannot start two decodes.
ANALYSIS_TASK = "capture-analysis"


def read_wav(path: Path | str) -> tuple[np.ndarray, int]:
    """Read mono 16-bit PCM back to floats in -1..1; channel 0 of a stereo file.

    One line of delegation: `guardian.ofdm.bench` holds the single implementation
    that the modem, the bench tool and this dialog all read files with, so the
    scaling cannot drift between what the recorder writes and what any of them
    reads. The wrapper exists only to say the one thing that reader can refuse in
    the operator's own language.
    """
    try:
        return _read_wav(path)
    except ValueError as exc:
        raise ValueError(
            dual(
                f"{Path(path).name} is not the 16-bit PCM Guardian reads: {exc}",
                f"{Path(path).name} není 16bitové PCM, které Guardian čte: {exc}",
            )
        ) from exc


@dataclass(frozen=True)
class CaptureAnalysis:
    """One decode attempt over a whole capture file."""

    path: Path
    sample_rate: int
    samples: int
    profile_name: str
    decoded: DecodedBurst

    @property
    def burst_found(self) -> bool:
        """Did the detector find anything worth synchronising on?

        `decode_burst` fills `sync_confidence` for every candidate it managed to
        synchronise on and leaves it None only when the detector found nothing at
        all, so this is the honest reading of "was there a burst in the file" --
        as opposed to `ok`, which also demands that the frame decoded.
        """
        return self.decoded.metrics.sync_confidence is not None


def analyse_capture(path: Path | str, profile) -> CaptureAnalysis:
    """Read a capture and try to decode a burst out of it. Runs off the UI thread.

    The capture's own sample rate is reported next to the profile's, because a
    mismatch is the one failure that looks exactly like a dead channel: the
    demodulator finds nothing, and nothing about the numbers says why.
    """
    samples, rate = read_wav(path)
    return CaptureAnalysis(
        path=Path(path),
        sample_rate=rate,
        samples=len(samples),
        profile_name=profile.name,
        decoded=decode_burst(profile, samples),
    )


class CaptureResultDialog(QDialog):
    """The capture's levels, its verdict, and an optional decode of it."""

    def __init__(self, runtime, summary: RecordingSummary, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.summary = summary
        self.analysis: CaptureAnalysis | None = None
        self._analysing = False
        # The worker pool's completions run when somebody drains it. The shell
        # poll does that twice a second, but this dialog must also work when it
        # is opened from a window whose timer is not running -- as in tests, and
        # as in any future caller. Its own timer costs nothing and stops itself.
        self.worker_timer = QTimer(self)
        self.worker_timer.setInterval(100)
        self.worker_timer.timeout.connect(self.runtime.drain_workers)

        self.setWindowTitle(tr("record.result_title"))
        self.setMinimumWidth(560)
        outer = QVBoxLayout(self)

        heading = QLabel(tr("record.result_title"))
        heading.setObjectName("PanelHeader")
        outer.addWidget(heading)

        self.verdict = QLabel(summary.verdict())
        self.verdict.setWordWrap(True)
        self.verdict.setProperty(
            "statusRole", "success" if summary.usable else "warning"
        )
        _repolish(self.verdict)
        outer.addWidget(self.verdict)

        self.path_label = QLabel(str(summary.path))
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        outer.addWidget(self.path_label)

        open_row = QHBoxLayout()
        self.open_folder_button = QPushButton(tr("record.open_folder"))
        self.open_folder_button.clicked.connect(self.open_folder)
        open_row.addWidget(self.open_folder_button)
        open_row.addStretch()
        outer.addLayout(open_row)

        self.fields: dict[str, QLabel] = {}
        measurements = QFormLayout()
        measurements.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        rows = [
            ("duration", dual("Duration", "Délka"),
             f"{summary.duration_seconds:.2f} s"),
            ("sample_rate", dual("Sample rate", "Vzorkovací kmitočet"),
             f"{summary.sample_rate} Hz"),
            ("peak", dual("Peak level", "Špičková úroveň"),
             _measurement(
                 summary.peak_dbfs if summary.peak > 0.0 else None,
                 "{value:.1f} dBFS",
             )),
            ("rms", dual("RMS level", "Efektivní úroveň"),
             _measurement(
                 summary.rms_dbfs if summary.rms > 0.0 else None,
                 "{value:.1f} dBFS",
             )),
            ("crest", dual("Crest factor", "Činitel výkyvu"),
             _measurement(summary.crest_factor_db, "{value:.1f} dB")),
            ("clipped", dual("Clipped samples", "Přebuzené vzorky"),
             str(summary.clipped_samples)),
        ]
        # Dropped blocks are gaps in the file that look exactly like a channel
        # that faded, so they are named when they happened and left out when
        # they did not -- a permanent "0" trains the eye to stop reading it.
        if summary.dropped_blocks:
            rows.append((
                "dropped",
                dual("Dropped blocks", "Zahozené bloky"),
                str(summary.dropped_blocks),
            ))
        for key, caption, text in rows:
            value = QLabel(text)
            value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self.fields[key] = value
            measurements.addRow(QLabel(caption), value)
        outer.addLayout(measurements)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        outer.addWidget(divider)

        analysis_heading = QLabel(dual(
            "What the modem found", "Co modem v záznamu našel"
        ))
        analysis_heading.setObjectName("SectionLabel")
        outer.addWidget(analysis_heading)

        self.analyse_button = QPushButton(tr("record.analyse"))
        self.analyse_button.setObjectName("primaryAction")
        self.analyse_button.clicked.connect(self.analyse)
        analyse_row = QHBoxLayout()
        analyse_row.addWidget(self.analyse_button)
        analyse_row.addStretch()
        outer.addLayout(analyse_row)

        self.analysis_status = QLabel(tr("record.analyse_hint"))
        self.analysis_status.setWordWrap(True)
        outer.addWidget(self.analysis_status)

        self.analysis_fields: dict[str, QLabel] = {}
        self.analysis_form = QFormLayout()
        self.analysis_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        analysis_rows = (
            ("burst", dual("Burst", "Vysílání")),
            ("sync", dual("Sync confidence", "Spolehlivost synchronizace")),
            ("snr", dual("Measured SNR", "Měřený odstup signál/šum")),
            ("evm", dual("EVM", "Chyba vektoru (EVM)")),
            ("cfo", dual("Frequency offset", "Kmitočtová odchylka")),
            ("header", dual("Frame", "Rámec")),
        )
        self.analysis_captions: dict[str, QLabel] = {}
        for key, caption in analysis_rows:
            label = QLabel(caption)
            value = QLabel()
            value.setWordWrap(True)
            value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self.analysis_captions[key] = label
            self.analysis_fields[key] = value
            label.setVisible(False)
            value.setVisible(False)
            self.analysis_form.addRow(label, value)
        outer.addLayout(self.analysis_form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(
            tr("common.close")
        )
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    # -- the file ------------------------------------------------------------

    def open_folder(self) -> None:
        """Show the capture's folder in the desktop file manager."""
        QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(Path(self.summary.path).parent))
        )

    # -- the decode ----------------------------------------------------------

    def analyse(self) -> None:
        """Decode the capture on a worker thread and report what came back."""
        if self._analysing:
            return
        profile = profile_or_default(self.runtime.config.ofdm_profile)
        path = Path(self.summary.path)
        self._analysing = True
        self.analyse_button.setEnabled(False)
        self.analysis_status.setText(tr("record.analysing"))
        self.analysis_status.setProperty("statusRole", "info")
        _repolish(self.analysis_status)
        submitted = self.runtime.operations.workers.submit(
            ANALYSIS_TASK,
            lambda: analyse_capture(path, profile),
            self._analysis_completed,
        )
        if submitted:
            self.worker_timer.start()
        else:
            self._analysing = False
            self.analyse_button.setEnabled(True)
            self.analysis_status.setText(tr("record.analyse_busy"))
            self.analysis_status.setProperty("statusRole", "warning")
            _repolish(self.analysis_status)

    def _analysis_completed(self, result: TaskResult) -> None:
        self._analysing = False
        self.worker_timer.stop()
        self.analyse_button.setEnabled(True)
        if result.error is not None:
            self.analysis_status.setText(
                dual(
                    f"The capture could not be decoded: {result.error}",
                    f"Záznam se nepodařilo dekódovat: {result.error}",
                )
            )
            self.analysis_status.setProperty("statusRole", "warning")
            _repolish(self.analysis_status)
            return
        self.show_analysis(result.value)

    def show_analysis(self, analysis: CaptureAnalysis) -> None:
        """Render a finished decode. Separated so it can be driven directly."""
        self.analysis = analysis
        metrics = analysis.decoded.metrics
        header = analysis.decoded.header
        found = analysis.burst_found
        self.analysis_fields["burst"].setText(
            dual("found", "nalezeno") if found
            else dual("none found", "nenalezeno")
        )
        self.analysis_fields["sync"].setText(
            _measurement(metrics.sync_confidence, "{value:.2f}")
        )
        self.analysis_fields["snr"].setText(
            _measurement(metrics.snr_db, "{value:.1f} dB")
        )
        self.analysis_fields["evm"].setText(
            _measurement(
                metrics.evm_rms * 100.0 if metrics.evm_rms is not None else None,
                "{value:.1f} %",
            )
        )
        self.analysis_fields["cfo"].setText(
            _measurement(metrics.cfo_hz, "{value:+.1f} Hz")
        )
        if header is not None:
            self.analysis_fields["header"].setText(header.summary())
        else:
            # No header means no frame, and the reason is the useful part. It is
            # the decoder's own English; wrapping it in a translated sentence
            # would only bury it.
            self.analysis_fields["header"].setText(
                _measurement(metrics.error, "{value}")
            )
        for caption in self.analysis_captions.values():
            caption.setVisible(True)
        for value in self.analysis_fields.values():
            value.setVisible(True)
        if analysis.decoded.ok:
            self.analysis_status.setText(
                dual(
                    f"A frame decoded from this capture with the "
                    f"{analysis.profile_name} profile.",
                    f"Z tohoto záznamu byl dekódován rámec profilem "
                    f"{analysis.profile_name}.",
                )
            )
            self.analysis_status.setProperty("statusRole", "success")
        elif found:
            self.analysis_status.setText(
                dual(
                    f"A burst was found but did not decode with the "
                    f"{analysis.profile_name} profile.",
                    f"Vysílání bylo nalezeno, ale profilem "
                    f"{analysis.profile_name} se nedekódovalo.",
                )
            )
            self.analysis_status.setProperty("statusRole", "warning")
        else:
            self.analysis_status.setText(
                dual(
                    f"No burst was detected. The capture is "
                    f"{analysis.sample_rate} Hz; the {analysis.profile_name} "
                    "profile expects the rate set in Station settings.",
                    f"Žádné vysílání nebylo nalezeno. Záznam má "
                    f"{analysis.sample_rate} Hz; profil {analysis.profile_name} "
                    "očekává kmitočet nastavený v nastavení stanice.",
                )
            )
            self.analysis_status.setProperty("statusRole", "warning")
        _repolish(self.analysis_status)

    def reject(self) -> None:
        self.worker_timer.stop()
        super().reject()
