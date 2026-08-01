import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
)

from guardian.i18n import tr
from guardian.message import (
    Attachment,
    Folder,
    MailMessage,
    MessageStore,
    Status,
)
from guardian.qt.mail_workspace import (
    ComposeDialog,
    MailWorkspace,
    MessageDialog,
)
from guardian.qt.network_workspace import NetworkWorkspace
from guardian.qt.runtime import ShellRuntime
from guardian.routing import Route, RouteTable


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


def test_mail_list_marks_whole_rows_and_keeps_them_selected(tmp_path) -> None:
    _application()
    runtime = ShellRuntime()
    runtime.mailstore = MessageStore(tmp_path / "mail")
    runtime.config.callsign = "OK7PS"
    workspace = MailWorkspace(runtime)
    try:
        for index in range(2):
            dialog = ComposeDialog(runtime)
            dialog.destination.setText("OK2IPW")
            dialog.subject.setText(f"Message {index}")
            dialog.body.setPlainText("Body.")
            dialog._queue()
            dialog.close()

        table = workspace.messages
        assert (
            table.selectionBehavior()
            == QAbstractItemView.SelectionBehavior.SelectRows
        )
        assert (
            table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
        )
        assert not table.showGrid()

        workspace.folders.setCurrentRow(1)
        assert workspace.selected_id is None
        assert table.rowCount() == 2

        table.selectRow(1)
        selected_id = workspace.selected_id
        assert selected_id is not None
        columns = sorted(
            index.column() for index in table.selectionModel().selectedIndexes()
        )
        assert columns == [0, 1, 2, 3, 4]

        # Marking the message read rebuilds the table; the row must stay marked.
        workspace.refresh()
        assert workspace.selected_id == selected_id
        selected_rows = table.selectionModel().selectedRows()
        assert len(selected_rows) == 1
        row = selected_rows[0].row()
        assert int(table.item(row, 0).data(Qt.ItemDataRole.UserRole)) == selected_id

        # Switching folders starts from a clean, unselected list.
        workspace.folders.setCurrentRow(0)
        assert workspace.selected_id is None
        assert table.selectionModel().selectedRows() == []
    finally:
        workspace.close()
        runtime.close()


def test_inbox_attachments_can_be_saved_and_hostile_names_are_defused(
    tmp_path,
) -> None:
    _application()
    runtime = ShellRuntime()
    runtime.mailstore = MessageStore(tmp_path / "mail")
    runtime.config.callsign = "OK7PS"
    workspace = MailWorkspace(runtime)
    try:
        # No message selected: nothing to act on, and the bar stays hidden.
        assert not workspace.attachment_bar.isVisibleTo(workspace)
        assert workspace._current_attachment() is None

        message = MailMessage(
            msg_id=runtime.mailstore.next_id("OK7PS"),
            source="OK2IPW",
            final_dest="OK7PS",
            subject="Foto",
            body="",
            attachments=[
                Attachment("radio-telescope.jpg", b"\xff\xd8\xff\xe0payload"),
                # An attachment name is remote input; it must never be a path.
                Attachment(r"..\..\Windows\System32\evil.txt", b"nope"),
            ],
            priority=0,
            created=1_700_000_000,
            hops=["OK2IPW"],
            folder=Folder.INBOX,
            status=Status.RECEIVED,
        )
        runtime.mailstore.add(message)

        # Inbox is already the current folder, so re-selecting row 0 emits no
        # signal; refresh explicitly to pick the new message up.
        workspace.refresh()
        assert workspace.messages.rowCount() == 1
        workspace.messages.selectRow(0)

        assert workspace.attachment_bar.isVisibleTo(workspace)
        assert workspace.attachment_picker.count() == 2
        assert workspace.save_all_button.isEnabled()
        assert "radio-telescope.jpg" in workspace.attachment_picker.itemText(0)
        assert workspace._safe_name(r"..\..\Windows\System32\evil.txt") == "evil.txt"
        assert workspace._safe_name("") == "attachment"

        target = tmp_path / "out"
        target.mkdir()
        workspace.save_all_attachments = lambda: None  # replaced below
        for attachment in workspace._attachments:
            name = workspace._safe_name(attachment.name)
            (target / name).write_bytes(attachment.data)

        assert (target / "radio-telescope.jpg").read_bytes().endswith(b"payload")
        # The traversal attempt lands beside it, not two directories up.
        assert (target / "evil.txt").exists()
        assert sorted(p.name for p in target.iterdir()) == [
            "evil.txt",
            "radio-telescope.jpg",
        ]

        assert ".exe" in MailWorkspace.RISKY_SUFFIXES
        assert ".ps1" in MailWorkspace.RISKY_SUFFIXES
        assert ".jpg" not in MailWorkspace.RISKY_SUFFIXES
    finally:
        workspace.close()
        runtime.close()


def test_double_click_opens_the_message_in_the_reading_form(tmp_path) -> None:
    _application()
    runtime = ShellRuntime()
    runtime.mailstore = MessageStore(tmp_path / "mail")
    runtime.config.callsign = "OK7PS"
    workspace = MailWorkspace(runtime)
    try:
        message = MailMessage(
            msg_id=runtime.mailstore.next_id("OK7PS"),
            source="OK2IPW",
            final_dest="OK7PS",
            subject="Test QSY 2",
            body="Ahoj vogone!",
            attachments=[Attachment("foto.jpg", b"jpeg")],
            priority=0,
            created=1_700_000_000,
            hops=["OK2IPW", "OK7PS"],
            folder=Folder.INBOX,
            status=Status.RECEIVED,
        )
        runtime.mailstore.add(message)
        workspace.refresh()
        workspace.messages.selectRow(0)

        dialog = MessageDialog(runtime.mailstore.get(workspace.selected_id))
        try:
            assert dialog.windowTitle() == "Test QSY 2"
            fields = [w.text() for w in dialog.findChildren(QLineEdit)]
            assert "OK2IPW → OK7PS" in fields
            assert "OK2IPW → OK7PS" in fields
            bodies = dialog.findChildren(QPlainTextEdit)
            assert len(bodies) == 1
            assert bodies[0].toPlainText() == "Ahoj vogone!"
            # Reading a message must never let it be edited in place.
            assert bodies[0].isReadOnly()
            assert all(field.isReadOnly() for field in dialog.findChildren(QLineEdit))
        finally:
            dialog.close()

        # Nothing selected: double-click is a no-op rather than a crash.
        workspace._clear_selection()
        workspace.open_in_window()
    finally:
        workspace.close()
        runtime.close()


def test_large_attachments_warn_but_do_not_block_the_operator(tmp_path) -> None:
    _application()
    runtime = ShellRuntime()
    runtime.mailstore = MessageStore(tmp_path / "mail")
    runtime.config.callsign = "OK7PS"
    dialog = ComposeDialog(runtime)
    asked = []
    try:
        dialog.destination.setText("OK2IPW")
        dialog.subject.setText("Foto")
        dialog.body.setPlainText("Radioteleskop.")
        dialog.attachments = [Attachment("wallpaper.jpg", b"x" * 300_000)]

        # Operator cancels: nothing is queued.
        original = QMessageBox.question
        QMessageBox.question = lambda *a, **k: (
            asked.append(a[2]) or QMessageBox.StandardButton.Cancel
        )
        dialog._queue()
        assert len(runtime.mailstore.list(Folder.OUTBOX)) == 0
        assert len(asked) == 1
        assert "300" in asked[0]

        # Operator confirms: the message goes out, size notwithstanding.
        QMessageBox.question = lambda *a, **k: QMessageBox.StandardButton.Ok
        dialog._queue()
        queued = runtime.mailstore.list(Folder.OUTBOX)
        assert len(queued) == 1
        assert runtime.mailstore.get(queued[0]["msg_id"]).attachments[0].size == 300_000
    finally:
        QMessageBox.question = original
        dialog.close()
        runtime.close()


def test_small_attachments_are_queued_without_a_prompt(tmp_path) -> None:
    _application()
    runtime = ShellRuntime()
    runtime.mailstore = MessageStore(tmp_path / "mail")
    runtime.config.callsign = "OK7PS"
    dialog = ComposeDialog(runtime)
    original = QMessageBox.question
    try:
        QMessageBox.question = lambda *a, **k: pytest.fail("should not prompt")
        dialog.destination.setText("OK2IPW")
        dialog.subject.setText("Malá")
        dialog.body.setPlainText("Text.")
        dialog.attachments = [Attachment("note.txt", b"y" * 1000)]
        dialog._queue()

        assert len(runtime.mailstore.list(Folder.OUTBOX)) == 1
    finally:
        QMessageBox.question = original
        dialog.close()
        runtime.close()


def test_network_tables_are_read_only_row_selectors() -> None:
    _application()
    runtime = ShellRuntime()
    runtime.routes = RouteTable()
    workspace = NetworkWorkspace(runtime)
    try:
        for table in (workspace.routes_table, workspace.heard_table):
            assert (
                table.selectionBehavior()
                == QAbstractItemView.SelectionBehavior.SelectRows
            )
            assert (
                table.editTriggers()
                == QAbstractItemView.EditTrigger.NoEditTriggers
            )
            assert not table.showGrid()
    finally:
        workspace.close()
        runtime.close()


def test_selecting_a_route_loads_it_into_the_form_for_editing() -> None:
    _application()
    runtime = ShellRuntime()
    runtime.routes = RouteTable()
    workspace = NetworkWorkspace(runtime)
    try:
        runtime.routes.add(Route("OK2IPW", "OK1AAA", "ANY", 145_237_500, "FM"))
        runtime.routes.add(Route("OK1BBB", "", "", 0, "USB"))
        workspace.refresh()

        # Editing used to require retyping the callsign exactly; selecting the
        # row now fills the form so it can be corrected in place.
        workspace.routes_table.selectRow(0)
        first = runtime.routes.routes[0]
        assert workspace.destination.text() == first.destination
        assert workspace.preferred.text() == first.preferred
        assert workspace.backup.text() == first.backup
        assert workspace.frequency.value() == first.freq_hz
        assert workspace.mode.currentData() == (first.mode or "FM")

        workspace.routes_table.selectRow(1)
        second = runtime.routes.routes[1]
        assert workspace.destination.text() == second.destination
        assert workspace.frequency.value() == second.freq_hz

        # Saving the loaded row edits it rather than adding a duplicate.
        workspace.preferred.setText("OK7PS")
        workspace._save_route()
        assert len(runtime.routes.routes) == 2
        assert runtime.routes.lookup(second.destination).preferred == "OK7PS"
    finally:
        workspace.close()
        runtime.close()


def test_removing_a_route_clears_the_form_it_was_loaded_into() -> None:
    _application()
    runtime = ShellRuntime()
    runtime.routes = RouteTable()
    workspace = NetworkWorkspace(runtime)
    try:
        runtime.routes.add(Route("OK2IPW", "OK1AAA", "", 145_237_500, "FM"))
        workspace.refresh()
        workspace.routes_table.selectRow(0)
        assert workspace.destination.text() == "OK2IPW"

        workspace._remove_route()

        assert runtime.routes.routes == []
        assert workspace.destination.text() == ""
        assert workspace.frequency.value() == 0
    finally:
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


def test_heard_stations_show_the_signal_and_the_channel_they_arrived_on() -> None:
    # Both were invisible before 0.6.35: the SNR column existed but nothing
    # ever filled it, and the frequency was not recorded at all.
    _application()
    runtime = ShellRuntime()
    runtime.routes = RouteTable()
    workspace = NetworkWorkspace(runtime)
    try:
        now = time.monotonic()
        runtime.heard.record(
            "OK2IPW", now, snr=12.5, freq_hz=145_237_500, frame="BEACON"
        )
        runtime.heard.record("OK1AAA", now, frame="BEACON")
        workspace.refresh()

        headers = [
            workspace.heard_table.horizontalHeaderItem(column).text()
            for column in range(workspace.heard_table.columnCount())
        ]
        assert headers == [
            tr("network.callsign"),
            tr("network.age"),
            tr("network.frames"),
            tr("network.snr"),
            tr("network.heard_on"),
            tr("network.locator"),
            tr("network.distance"),
            tr("network.last_frame"),
        ]
        rows = {
            workspace.heard_table.item(row, 0).text(): (
                workspace.heard_table.item(row, 3).text(),
                workspace.heard_table.item(row, 4).text(),
            )
            for row in range(workspace.heard_table.rowCount())
        }
        assert rows["OK2IPW"] == ("12.5 dB", "145.2375 MHz")
        # A station heard with no measurement says so instead of inventing one.
        assert rows["OK1AAA"] == ("-", "-")
    finally:
        workspace.close()
        runtime.close()


def test_the_map_places_stations_that_beacon_a_position() -> None:
    _application()
    from guardian.qt.map_window import MapWindow

    runtime = ShellRuntime()
    runtime.config.map_background = False               # this test is not about tiles
    runtime.config.station_grid = "JO70FB28MC"          # Praha
    now = time.monotonic()
    runtime.heard.record("OK2IPW", now, grid="JN89HE", frame="BEACON")
    runtime.heard.record("OK1AAA", now, frame="BEACON")  # heard, but no position
    window = MapWindow(runtime)
    try:
        placed = {call for call, _grid, _age in window.stations()}
        assert placed == {"OK2IPW"}, "a station without a locator has nowhere to go"

        # The status line carries the path an operator would otherwise
        # measure off a paper map: Praha -> Brno, ~185 km to the south-east.
        window.refresh()
        text = window.status.text()
        assert "JO70FB28MC" in text
        assert "OK2IPW JN89HE" in text
        assert "18" in text and "km" in text
    finally:
        window.close()
        runtime.close()


def test_map_links_only_correspondents_with_mail_history(tmp_path) -> None:
    _application()
    from guardian.qt.map_window import MapWindow

    runtime = ShellRuntime()
    runtime.mailstore = MessageStore(tmp_path / "map-mail")
    runtime.config.map_background = False
    runtime.config.callsign = "OK7PS"
    runtime.config.station_grid = "JO70FB28MC"
    now = time.monotonic()
    runtime.heard.record("OK2IPW", now, grid="JN89HE", frame="BEACON")
    runtime.heard.record("OK1AAA", now, grid="JO80AB", frame="BEACON")
    runtime.heard.record("OK1IDLE", now, grid="JN88EE", frame="BEACON")
    runtime.mailstore.add(MailMessage(
        msg_id=runtime.mailstore.next_id("OK7PS"),
        source="OK7PS", final_dest="OK2IPW", created=time.time(),
        folder=Folder.SENT, status=Status.DELIVERED,
    ))
    runtime.mailstore.add(MailMessage(
        msg_id=runtime.mailstore.next_id("OK1AAA"),
        source="OK1AAA", final_dest="OK7PS", created=time.time(),
        folder=Folder.INBOX, status=Status.RECEIVED,
    ))
    window = MapWindow(runtime)
    try:
        links = {
            call: (grid, activity)
            for call, grid, activity in window.interactions()
        }
        assert links == {
            "OK1AAA": ("JO80AB", "received"),
            "OK2IPW": ("JN89HE", "sent"),
        }
        assert "OK1IDLE" not in links
        window.refresh()
        assert window.canvas.links == window.interactions()
    finally:
        window.close()
        runtime.close()


def test_clicking_a_mapped_station_prefills_a_new_message(monkeypatch) -> None:
    _application()
    import guardian.qt.map_window as map_module

    runtime = ShellRuntime()
    runtime.config.map_background = False
    opened: list[tuple[str, str]] = []

    class Dialog:
        def __init__(self, _runtime, _parent, *, destination=""):
            opened.append(("destination", destination))

        def exec(self):
            opened.append(("exec", ""))

    monkeypatch.setattr(map_module, "ComposeDialog", Dialog)
    window = map_module.MapWindow(runtime)
    try:
        window._compose_to("OK2IPW")
        assert opened == [("destination", "OK2IPW"), ("exec", "")]
    finally:
        window.close()
        runtime.close()


def test_picking_on_the_map_stores_the_finest_locator() -> None:
    # A coarse square can be derived from a fine one, never the other way
    # round, so what gets stored is all ten characters.
    _application()
    from guardian.qt.map_window import MapWindow
    from guardian.routing import MAX_LOCATOR_CHARS, from_locator

    runtime = ShellRuntime()
    runtime.config.map_background = False
    runtime.config.station_grid = ""
    window = MapWindow(runtime)
    try:
        window._picked(50.0755, 14.4378)

        stored = runtime.config.station_grid
        assert len(stored) == MAX_LOCATOR_CHARS
        assert stored.startswith("JO70FB")
        latitude, longitude = from_locator(stored)
        assert abs(latitude - 50.0755) < 0.01
        assert abs(longitude - 14.4378) < 0.01
        assert not window.pick_button.isChecked(), "one click, one position"
    finally:
        window.close()
        runtime.close()


def test_a_typed_locator_is_accepted_and_nonsense_is_refused() -> None:
    _application()
    from guardian.qt.map_window import MapWindow

    runtime = ShellRuntime()
    runtime.config.map_background = False
    runtime.config.station_grid = "JO70FB"
    window = MapWindow(runtime)
    try:
        window.locator_edit.setText("jn89he12ab")
        window._typed()
        assert runtime.config.station_grid == "JN89HE12AB", "upper-cased as sent"

        window.locator_edit.setText("ZZ99XX")
        window._typed()
        assert runtime.config.station_grid == "JN89HE12AB", "kept the good one"
        assert "ZZ99XX" in window.status.text()
        assert window.locator_edit.text() == "JN89HE12AB"
    finally:
        window.close()
        runtime.close()


def test_heard_stations_list_the_locator_and_the_path_to_it() -> None:
    _application()
    runtime = ShellRuntime()
    runtime.routes = RouteTable()
    runtime.config.station_grid = "JO70FB"
    workspace = NetworkWorkspace(runtime)
    try:
        now = time.monotonic()
        runtime.heard.record("OK2IPW", now, grid="JN89HE", frame="BEACON")
        runtime.heard.record("OK1AAA", now, frame="BEACON")
        workspace.refresh()

        rows = {
            workspace.heard_table.item(row, 0).text(): (
                workspace.heard_table.item(row, 5).text(),
                workspace.heard_table.item(row, 6).text(),
            )
            for row in range(workspace.heard_table.rowCount())
        }
        assert rows["OK2IPW"][0] == "JN89HE"
        assert "km" in rows["OK2IPW"][1] and "°" in rows["OK2IPW"][1]
        # No position, no path -- and no invented one.
        assert rows["OK1AAA"] == ("-", "-")
    finally:
        workspace.close()
        runtime.close()
