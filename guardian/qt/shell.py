"""Guardian's compact PySide6 operational shell."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __app_name__, __version__
from ..assets import get_ico_path
from ..services import ApplicationSnapshot
from .diagnostics_dialog import DiagnosticsDialog
from .log_workspace import LogWorkspace
from .mail_workspace import MailWorkspace
from .network_workspace import NetworkWorkspace
from .readiness_dialog import ReadinessDialog
from .runtime import ShellRuntime
from .settings_dialog import SettingsDialog
from .theme import ThemeController, ThemePreference
from .update_dialog import UpdateDialog


def _repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class MetricItem(QWidget):
    def __init__(self, label: str, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.label = QLabel(label)
        self.label.setObjectName("MetricLabel")
        self.value = QLabel("0")
        self.value.setObjectName("MetricValue")
        layout.addWidget(self.label)
        layout.addWidget(self.value)


class StatusIndicator(QLabel):
    def set_status(self, state: str, label: str) -> None:
        symbols = {
            "success": "●",
            "info": "◐",
            "warning": "◆",
            "danger": "◆",
            "inactive": "○",
        }
        self.setProperty("statusRole", state)
        self.setText(f"{symbols.get(state, '○')} {label}")
        self.setAccessibleName(label)
        _repolish(self)


class GuardianMainWindow(QMainWindow):
    def __init__(
        self,
        runtime: ShellRuntime,
        settings: QSettings,
    ) -> None:
        super().__init__()
        self.runtime = runtime
        self.settings = settings
        self.theme_controller = ThemeController(settings, self)
        self.runtime.operations.winlink_prompt = self._winlink_prompt

        self.setWindowTitle(f"{__app_name__} — ARDOS  v{__version__}")
        self.setWindowIcon(QIcon(str(get_ico_path())))
        self.setMinimumSize(1180, 720)
        self.resize(1366, 768)

        self._build_menu()
        self._build_shell()
        self._restore_geometry()
        self._refresh()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(500)
        self.refresh_timer.timeout.connect(self._refresh)
        self.refresh_timer.start()
        QTimer.singleShot(5_000, self._check_for_updates_silently)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Alt+F4")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = self.menuBar().addMenu("&View")
        self.workspace_actions: dict[str, QAction] = {}
        workspace_group = QActionGroup(self)
        workspace_group.setExclusive(True)
        for index, label in enumerate(("Home", "Mail", "Network", "Log")):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(index == 0)
            action.triggered.connect(
                lambda _checked=False, name=label.lower():
                self._show_workspace(name)
            )
            workspace_group.addAction(action)
            view_menu.addAction(action)
            self.workspace_actions[label.lower()] = action

        tools_menu = self.menuBar().addMenu("&Tools")
        connect_radio = QAction("Connect / disconnect radio", self)
        connect_radio.triggered.connect(self._toggle_radio)
        tools_menu.addAction(connect_radio)
        connect_vara = QAction("Connect / disconnect VARA", self)
        connect_vara.triggered.connect(self._toggle_vara)
        tools_menu.addAction(connect_vara)
        control_channel = QAction("Start / stop control channel", self)
        control_channel.triggered.connect(self._toggle_control)
        tools_menu.addAction(control_channel)
        tools_menu.addSeparator()
        readiness = QAction("Station readiness", self)
        readiness.triggered.connect(self._show_readiness)
        tools_menu.addAction(readiness)
        diagnostics = QAction("Diagnostics", self)
        diagnostics.triggered.connect(self._show_diagnostics)
        tools_menu.addAction(diagnostics)
        tools_menu.addSeparator()
        updates = QAction("Check for updates", self)
        updates.triggered.connect(self._check_for_updates)
        tools_menu.addAction(updates)

        settings_menu = self.menuBar().addMenu("&Settings")
        configuration = QAction("Station settings", self)
        configuration.setShortcut("Ctrl+,")
        configuration.triggered.connect(self._show_settings)
        settings_menu.addAction(configuration)
        theme_menu = settings_menu.addMenu("Theme")
        group = QActionGroup(self)
        group.setExclusive(True)
        self.theme_actions: dict[ThemePreference, QAction] = {}
        labels = {
            ThemePreference.SYSTEM: "Follow system",
            ThemePreference.LIGHT: "Light",
            ThemePreference.DARK: "Dark",
        }
        for preference, label in labels.items():
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(self.theme_controller.preference == preference)
            action.triggered.connect(
                lambda _checked=False, selected=preference:
                self.theme_controller.set_preference(selected)
            )
            group.addAction(action)
            theme_menu.addAction(action)
            self.theme_actions[preference] = action

        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("About Guardian", self)
        about.triggered.connect(self._show_about)
        help_menu.addAction(about)

    def _build_shell(self) -> None:
        root = QWidget()
        root.setObjectName("AppShell")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(6)
        self.setCentralWidget(root)

        outer.addWidget(self._build_operational_header())
        outer.addWidget(self._build_metric_strip())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.workspace_stack = QStackedWidget()
        self.workspace_names = {
            "home": self._build_workspace(),
            "mail": MailWorkspace(self.runtime),
            "network": NetworkWorkspace(self.runtime),
            "log": LogWorkspace(self.runtime),
        }
        for workspace in self.workspace_names.values():
            self.workspace_stack.addWidget(workspace)
        splitter.addWidget(self.workspace_stack)
        splitter.addWidget(self._build_activity())
        splitter.setStretchFactor(0, 65)
        splitter.setStretchFactor(1, 35)
        splitter.setSizes([760, 420])
        outer.addWidget(splitter, 1)
        outer.addWidget(self._build_status_strip())

        self.statusBar().showMessage(
            "Guardian operational workspace ready"
        )

    def _build_operational_header(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("OperationalHeader")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(16)

        context = QWidget()
        context_layout = QVBoxLayout(context)
        context_layout.setContentsMargins(0, 0, 0, 0)
        context_layout.setSpacing(4)
        section = QLabel("STATION CONTEXT")
        section.setObjectName("SectionLabel")
        self.context_value = QLabel()
        self.context_value.setObjectName("ContextValue")
        self.context_value.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.context_detail = QLabel()
        self.context_detail.setObjectName("Metadata")
        context_layout.addWidget(section)
        context_layout.addWidget(self.context_value)
        context_layout.addWidget(self.context_detail)
        context_layout.addStretch()
        layout.addWidget(context, 1)

        operation = QWidget()
        operation.setMinimumWidth(285)
        operation_layout = QVBoxLayout(operation)
        operation_layout.setContentsMargins(0, 0, 0, 0)
        operation_layout.setSpacing(4)
        operation_title = QLabel("OPERATION")
        operation_title.setObjectName("SectionLabel")
        self.operation_state = StatusIndicator()
        self.operation_state.set_status("inactive", "Station idle")
        operation_detail = QLabel(
            "Connect hardware explicitly, then start the audio control channel "
            "when the station is ready to exchange ARDOS frames."
        )
        operation_detail.setObjectName("Metadata")
        operation_detail.setWordWrap(True)
        controls = QHBoxLayout()
        self.radio_button = QPushButton("Connect radio")
        self.radio_button.clicked.connect(self._toggle_radio)
        self.vara_button = QPushButton("Connect VARA")
        self.vara_button.clicked.connect(self._toggle_vara)
        self.control_button = QPushButton("Start control")
        self.control_button.setObjectName("primaryAction")
        self.control_button.clicked.connect(self._toggle_control)
        controls.addWidget(self.radio_button)
        controls.addWidget(self.vara_button)
        controls.addWidget(self.control_button)
        operation_layout.addWidget(operation_title)
        operation_layout.addWidget(self.operation_state)
        operation_layout.addWidget(operation_detail)
        operation_layout.addLayout(controls)
        layout.addWidget(operation)
        return panel

    def _build_metric_strip(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("MetricStrip")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(20)
        self.metrics = {
            "inbox": MetricItem("Inbox"),
            "unread": MetricItem("Unread"),
            "outbox": MetricItem("Outbox"),
            "transit": MetricItem("Transit"),
            "sessions": MetricItem("Sessions"),
            "heard": MetricItem("Heard"),
        }
        for item in self.metrics.values():
            layout.addWidget(item)
        layout.addStretch()
        return panel

    def _build_workspace(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("WorkspacePanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        heading = QLabel("Station readiness")
        heading.setObjectName("PanelHeader")
        description = QLabel(
            "A concise view of the components required for normal operation."
        )
        description.setObjectName("Metadata")
        layout.addWidget(heading)
        layout.addWidget(description)

        self.readiness = QTreeWidget()
        self.readiness.setColumnCount(3)
        self.readiness.setHeaderLabels(["Component", "State", "Detail"])
        self.readiness.setRootIsDecorated(False)
        self.readiness.setAlternatingRowColors(True)
        self.readiness.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        self.readiness.header().setStretchLastSection(True)
        self.readiness.setColumnWidth(0, 145)
        self.readiness.setColumnWidth(1, 115)
        layout.addWidget(self.readiness, 1)

        hint = QLabel(
            "Use Tools > Station readiness to locate or install missing components."
        )
        hint.setObjectName("Metadata")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return panel

    def _build_activity(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("ActivityPanel")
        panel.setMinimumWidth(380)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        heading = QLabel("Activity")
        heading.setObjectName("PanelHeader")
        self.activity_count = QLabel("0 events")
        self.activity_count.setObjectName("Metadata")
        top = QHBoxLayout()
        top.addWidget(heading)
        top.addStretch()
        top.addWidget(self.activity_count)
        layout.addLayout(top)

        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.activity.setFont(fixed)
        self.activity.setAccessibleName("Guardian activity log")
        layout.addWidget(self.activity, 1)
        return panel

    def _build_status_strip(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("StatusStrip")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(18)
        self.radio_status = StatusIndicator()
        self.vara_status = StatusIndicator()
        self.control_status = StatusIndicator()
        self.hamlib_status = StatusIndicator()
        for indicator in (
            self.radio_status,
            self.vara_status,
            self.control_status,
            self.hamlib_status,
        ):
            indicator.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
            )
            layout.addWidget(indicator)
        layout.addStretch()
        return panel

    def _refresh(self) -> None:
        self.runtime.drain_workers()
        self.runtime.tick()
        snapshot = self.runtime.snapshots.read()
        self._apply_snapshot(snapshot)
        events = self.runtime.events.drain()
        if events:
            self.activity.appendPlainText(
                "\n".join(event.display_text for event in events)
            )
        self.activity_count.setText(
            f"{len(self.runtime.events.history())} events"
        )
        active = self.workspace_stack.currentWidget()
        refresh = getattr(active, "refresh", None)
        if callable(refresh):
            refresh()

    def _show_workspace(self, name: str) -> None:
        workspace = self.workspace_names.get(name)
        if workspace is None:
            return
        self.workspace_stack.setCurrentWidget(workspace)
        self.workspace_actions[name].setChecked(True)
        refresh = getattr(workspace, "refresh", None)
        if callable(refresh):
            refresh()
        self.statusBar().showMessage(f"{name.title()} workspace")

    def _apply_snapshot(self, snapshot: ApplicationSnapshot) -> None:
        config = self.runtime.config
        payload = (
            "VARA P2P"
            if config.payload_backend == "vara_p2p"
            else "Winlink"
        )
        self.context_value.setText(
            f"{config.callsign or 'NOCALL'}  ·  {config.vara_mode}  ·  {payload}"
        )
        radio_name = config.radio or config.radio_backend or "not configured"
        self.context_detail.setText(
            f"Radio: {radio_name}  ·  Control modem: {config.active_modem()}"
        )

        mailbox = snapshot.mailbox
        values = {
            "inbox": mailbox.inbox,
            "unread": mailbox.unread,
            "outbox": mailbox.outbox,
            "transit": mailbox.transit,
            "sessions": snapshot.network.active_sessions,
            "heard": snapshot.network.heard_stations,
        }
        for key, value in values.items():
            self.metrics[key].value.setText(str(value))

        radio_role = "success" if snapshot.radio.connected else "inactive"
        vara_role = "success" if snapshot.vara.command_connected else "inactive"
        control_role = (
            "success" if snapshot.network.control_channel_active else "inactive"
        )
        dependency = snapshot.dependencies
        hamlib_role = "success" if dependency.hamlib_available else "warning"
        self.radio_status.set_status(radio_role, "Radio: connected" if snapshot.radio.connected else "Radio: off")
        self.vara_status.set_status(vara_role, "VARA: connected" if snapshot.vara.command_connected else "VARA: off")
        self.control_status.set_status(
            control_role,
            "Control: active"
            if snapshot.network.control_channel_active
            else "Control: off",
        )
        self.hamlib_status.set_status(
            hamlib_role,
            "Hamlib: ready" if dependency.hamlib_available else "Hamlib: missing",
        )
        self.radio_button.setText(
            "Disconnect radio" if snapshot.radio.connected else "Connect radio"
        )
        self.vara_button.setText(
            "Disconnect VARA"
            if snapshot.vara.command_connected
            else "Connect VARA"
        )
        self.control_button.setText(
            "Stop control"
            if snapshot.network.control_channel_active
            else "Start control"
        )
        if snapshot.network.control_channel_active:
            self.operation_state.set_status("success", "Control channel active")
        elif snapshot.radio.connected or snapshot.vara.command_connected:
            self.operation_state.set_status("info", "Hardware connected")
        else:
            self.operation_state.set_status("inactive", "Station idle")

        rows = [
            (
                "Station identity",
                "Ready" if config.callsign and config.callsign != "NOCALL" else "Needs setup",
                config.callsign or "No callsign configured",
            ),
            (
                "Radio control",
                "Configured" if config.radio_backend != "none" else "Not configured",
                radio_name,
            ),
            (
                "Hamlib",
                "Available" if dependency.hamlib_available else "Missing",
                dependency.hamlib_path or "Open Station readiness for guided setup",
            ),
            (
                f"VARA {config.vara_mode}",
                "Endpoint set",
                f"{config.vara_host}:{config.vara_cmd_port}",
            ),
            (
                "Payload workflow",
                payload,
                "Uses the shared ARDOS session and payload controller",
            ),
        ]
        self.readiness.clear()
        for component, state, detail in rows:
            self.readiness.addTopLevelItem(
                QTreeWidgetItem([component, state, detail])
            )

    def _show_settings(self) -> None:
        dialog = SettingsDialog(
            self.runtime.config,
            self.theme_controller.preference,
            self,
        )

        def apply_changes() -> None:
            self.theme_controller.set_preference(dialog.selected_theme)
            self.runtime.refresh()
            self.runtime.request_dependency_refresh()
            self._refresh()

        dialog.saved.connect(apply_changes)
        dialog.exec()

    def _show_readiness(self) -> None:
        ReadinessDialog(self.runtime, self.settings, self).exec()
        self._refresh()

    def show_readiness_if_needed(self) -> None:
        completed = self.settings.value(
            "onboarding/completed",
            False,
            type=bool,
        )
        if not completed:
            self._show_readiness()

    def _show_diagnostics(self) -> None:
        self.runtime.drain_workers()
        DiagnosticsDialog(self.runtime, self).exec()

    def _check_for_updates_silently(self) -> None:
        self.runtime.request_update_check(self._update_check_completed)

    def _check_for_updates(self) -> None:
        if self.runtime.request_update_check(self._update_check_completed):
            self.statusBar().showMessage("Checking for Guardian updates…")

    def _update_check_completed(self, result) -> None:
        if result.error is not None:
            self.statusBar().showMessage(
                f"Update check failed: {result.error}",
                10_000,
            )
            return
        if result.value is None:
            self.statusBar().showMessage("Guardian is up to date.", 5_000)
            return
        UpdateDialog(self.runtime, result.value, self).exec()

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Guardian",
            f"<b>{__app_name__} {__version__}</b><br>"
            "ARDOS control and routing layer.<br><br>"
            "This Monitor shell uses the shared Modeling Anten design language.",
        )

    def _toggle_radio(self) -> None:
        if self.runtime.snapshots.read().radio.connected:
            self.runtime.operations.disconnect_radio()
        else:
            self.runtime.operations.connect_radio()

    def _toggle_vara(self) -> None:
        if self.runtime.snapshots.read().vara.command_connected:
            self.runtime.operations.disconnect_vara()
        else:
            self.runtime.operations.connect_vara()

    def _toggle_control(self) -> None:
        if self.runtime.operations.audio_transport is not None:
            self.runtime.operations.stop_control_channel()
            return
        answer = QMessageBox.question(
            self,
            "Start control channel",
            "Start the live audio control channel? ARDOS control frames may key "
            "the configured radio only after you explicitly send or respond.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.runtime.operations.start_control_channel()

    def _winlink_prompt(self, role: str, message, done) -> None:
        action = "Sent" if role == "send" else "Received"
        peer = message.next_hop if role == "send" else message.source
        answer = QMessageBox.question(
            self,
            "Winlink hand-off",
            f"Message #{message.msg_id}\nPeer: {peer}\n"
            f"Final destination: {message.final_dest}\n\n"
            f"Confirm {action.lower()} only after the Winlink transfer completes.",
        )
        done(answer == QMessageBox.StandardButton.Yes)

    def _restore_geometry(self) -> None:
        geometry = self.settings.value("ui/main_geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.settings.setValue("ui/main_geometry", self.saveGeometry())
        self.settings.sync()
        super().closeEvent(event)
