import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from guardian.i18n import Language, set_language
from guardian.qt.update_dialog import UpdateDialog
from guardian.services import TaskResult
from guardian.updates import UpdateInfo


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


class Runtime:
    def __init__(self) -> None:
        self.completed = None
        self.progress = None
        self.pending_result = None

    def download_update(self, _info, completed, progress) -> bool:
        self.completed = completed
        self.progress = progress
        return True

    def drain_workers(self) -> None:
        if self.pending_result is not None:
            result, self.pending_result = self.pending_result, None
            self.completed(result)


def test_update_dialog_shows_progress_and_prompts_without_closing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    application = _application()
    set_language(Language.ENGLISH)
    runtime = Runtime()
    info = UpdateInfo(
        "0.3.0",
        "https://github.com/example/project/releases/download/v0.3.0/setup.exe",
        "a" * 64,
    )
    prompts = []

    def question(_parent, title, _message):
        prompts.append(title)
        return (
            QMessageBox.StandardButton.Yes
            if len(prompts) == 1
            else QMessageBox.StandardButton.No
        )

    monkeypatch.setattr(QMessageBox, "question", question)
    dialog = UpdateDialog(runtime, info)
    dialog.show()
    application.processEvents()
    try:
        dialog._download()

        assert dialog.isVisible()
        assert dialog.progress.isVisibleTo(dialog)
        assert not dialog.close_button.isEnabled()
        runtime.progress(20_000_000, 40_000_000)
        application.processEvents()
        assert dialog.progress.value() == 50

        installer = tmp_path / "Guardian-0.3.0.exe"
        runtime.pending_result = TaskResult(
            "update-download",
            value=installer,
        )
        dialog.worker_timer.timeout.emit()

        assert dialog.isVisible()
        assert dialog.progress.value() == 100
        assert dialog.close_button.isEnabled()
        assert prompts == ["Download update", "Install update"]
    finally:
        dialog.close()
