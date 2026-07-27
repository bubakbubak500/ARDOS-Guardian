import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from guardian.message import Folder, MessageStore
from guardian.qt.mail_workspace import ComposeDialog, MailWorkspace
from guardian.qt.network_workspace import NetworkWorkspace
from guardian.qt.runtime import ShellRuntime
from guardian.routing import RouteTable


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_compose_queues_real_bundle_and_mail_workspace_reads_it(tmp_path) -> None:
    _application()
    runtime = ShellRuntime()
    runtime.mailstore = MessageStore(tmp_path / "mail")
    runtime.config.callsign = "OK7PS"
    dialog = ComposeDialog(runtime)
    workspace = MailWorkspace(runtime)
    try:
        dialog.destination.setText("OK1AAA")
        dialog.subject.setText("Readiness")
        dialog.body.setPlainText("Station ready.")
        dialog._queue()

        messages = runtime.mailstore.list(Folder.OUTBOX)
        assert len(messages) == 1
        saved = runtime.mailstore.get(messages[0]["msg_id"])
        assert saved is not None
        assert saved.final_dest == "OK1AAA"
        assert saved.body == "Station ready."

        workspace.folders.setCurrentRow(1)
        workspace.refresh()
        assert workspace.messages.rowCount() == 1
    finally:
        dialog.close()
        workspace.close()
        runtime.close()


def test_network_workspace_persists_normalised_route(tmp_path) -> None:
    _application()
    runtime = ShellRuntime()
    runtime.routes = RouteTable()
    workspace = NetworkWorkspace(runtime)
    try:
        workspace.destination.setText("ok1ccc")
        workspace.preferred.setText("ok1ddd")
        workspace.frequency.setValue(145_500_000)
        workspace.mode.setCurrentIndex(workspace.mode.findData("FM"))
        workspace._save_route()

        route = runtime.routes.lookup("OK1CCC")
        assert route is not None
        assert route.preferred == "OK1DDD"
        assert route.freq_hz == 145_500_000
        assert route.mode == "FM"
        assert workspace.routes_table.rowCount() == 1
    finally:
        workspace.close()
        runtime.close()


def test_network_workspace_allows_direct_route_and_formats_operator_inputs() -> None:
    _application()
    runtime = ShellRuntime()
    runtime.routes = RouteTable()
    workspace = NetworkWorkspace(runtime)
    try:
        workspace.destination.setText("ok1aaa")
        workspace.frequency.setValue(144_520_000)
        workspace._save_route()

        route = runtime.routes.lookup("OK1AAA")
        assert route is not None
        assert route.preferred == ""
        assert workspace.destination.text() == "OK1AAA"
        assert workspace.frequency.text() == "144.5200 MHz"
        assert workspace.routes_table.item(0, 3).text() == "144.5200 MHz"
    finally:
        workspace.close()
        runtime.close()
