"""Guardian's compact PySide6 operational shell."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QFileDialog,
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
from ..i18n import dual, tr
from ..routing import read_csv, write_csv
from ..routing.csv_io import TEMPLATE_ROWS
from ..services import ApplicationSnapshot
from .diagnostics_dialog import DiagnosticsDialog
from .help_dialog import HelpDialog
from .log_workspace import LogWorkspace
from .mail_workspace import MailWorkspace
from .network_workspace import NetworkWorkspace
from .readiness_dialog import ReadinessDialog
from .runtime import ShellRuntime
from .settings_dialog import SettingsDialog
from .spectrum_window import SpectrumWindow
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
        # On Windows an owned top-level window is forced above its owner.
        # Keep the spectrum independent so either window can receive focus.
        self.spectrum_window = SpectrumWindow(runtime, settings)
        self.theme_controller.theme_changed.connect(
            self.spectrum_window.set_tokens
        )
        self.spectrum_window.set_tokens(self.theme_controller.tokens)

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
        file_menu = self.menuBar().addMenu(tr("menu.file"))
        import_network = QAction(tr("menu.network_import"), self)
        import_network.triggered.connect(self._import_network)
        file_menu.addAction(import_network)
        export_network = QAction(tr("menu.network_export"), self)
        export_network.triggered.connect(self._export_network)
        file_menu.addAction(export_network)
        network_template = QAction(tr("menu.network_template"), self)
        network_template.triggered.connect(self._save_network_template)
        file_menu.addAction(network_template)
        file_menu.addSeparator()
        exit_action = QAction(tr("menu.exit"), self)
        exit_action.setShortcut("Alt+F4")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = self.menuBar().addMenu(tr("menu.view"))
        spectrum_action = QAction(
            dual("VARA spectrum && waterfall", "Spektrum && waterfall VARA"),
            self,
        )
        spectrum_action.setShortcut("Ctrl+Shift+W")
        spectrum_action.triggered.connect(self.show_spectrum)
        view_menu.addAction(spectrum_action)
        view_menu.addSeparator()
        self.workspace_actions: dict[str, QAction] = {}
        workspace_group = QActionGroup(self)
        workspace_group.setExclusive(True)
        workspace_labels = (
            ("home", tr("menu.home")),
            ("mail", tr("menu.mail")),
            ("network", tr("menu.network")),
            ("log", tr("menu.log")),
        )
        for index, (name, label) in enumerate(workspace_labels):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(index == 0)
            action.triggered.connect(
                lambda _checked=False, name=name:
                self._show_workspace(name)
            )
            workspace_group.addAction(action)
            view_menu.addAction(action)
            self.workspace_actions[name] = action

        tools_menu = self.menuBar().addMenu(tr("menu.tools"))
        connect_radio = QAction(tr("menu.radio_toggle"), self)
        connect_radio.triggered.connect(self._toggle_radio)
        tools_menu.addAction(connect_radio)
        connect_vara = QAction(tr("menu.vara_toggle"), self)
        connect_vara.triggered.connect(self._toggle_vara)
        tools_menu.addAction(connect_vara)
        control_channel = QAction(tr("menu.control_toggle"), self)
        control_channel.triggered.connect(self._toggle_control)
        tools_menu.addAction(control_channel)
        tools_menu.addSeparator()
        readiness = QAction(tr("menu.readiness"), self)
        readiness.triggered.connect(self._show_readiness)
        tools_menu.addAction(readiness)
        diagnostics = QAction(tr("menu.diagnostics"), self)
        diagnostics.triggered.connect(self._show_diagnostics)
        tools_menu.addAction(diagnostics)
        tools_menu.addSeparator()
        updates = QAction(tr("menu.updates"), self)
        updates.triggered.connect(self._check_for_updates)
        tools_menu.addAction(updates)

        settings_menu = self.menuBar().addMenu(tr("menu.settings"))
        self.station_settings_action = QAction(
            tr("menu.station_settings"),
            self,
        )
        self.station_settings_action.triggered.connect(self._show_settings)
        settings_menu.addAction(self.station_settings_action)
        theme_menu = settings_menu.addMenu(tr("menu.theme"))
        group = QActionGroup(self)
        group.setExclusive(True)
        self.theme_actions: dict[ThemePreference, QAction] = {}
        labels = {
            ThemePreference.SYSTEM: tr("theme.system"),
            ThemePreference.LIGHT: tr("theme.light"),
            ThemePreference.DARK: tr("theme.dark"),
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

        help_menu = self.menuBar().addMenu(tr("menu.help"))
        user_guide = QAction(tr("menu.user_guide"), self)
        user_guide.triggered.connect(self._show_help)
        help_menu.addAction(user_guide)
        help_menu.addSeparator()
        about = QAction(tr("menu.about"), self)
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
            tr("shell.ready")
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
        section = QLabel(tr("shell.station_context"))
        section.setObjectName("SectionLabel")
        self.context_value = QLabel()
        self.context_value.setObjectName("ContextValue")
        self.context_value.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.context_detail = QLabel()
        self.context_detail.setObjectName("Metadata")
        self.context_activity = QLabel()
        self.context_activity.setObjectName("ContextActivity")
        self.context_activity.setWordWrap(True)
        context_layout.addWidget(section)
        context_layout.addWidget(self.context_value)
        context_layout.addWidget(self.context_detail)
        context_layout.addWidget(self.context_activity)
        context_layout.addStretch()
        layout.addWidget(context, 1)

        operation = QWidget()
        operation.setMinimumWidth(285)
        operation_layout = QVBoxLayout(operation)
        operation_layout.setContentsMargins(0, 0, 0, 0)
        operation_layout.setSpacing(4)
        operation_title = QLabel(tr("shell.operation"))
        operation_title.setObjectName("SectionLabel")
        self.operation_state = StatusIndicator()
        self.operation_state.set_status("inactive", tr("shell.station_idle"))
        operation_detail = QLabel(tr("shell.operation_detail"))
        operation_detail.setObjectName("Metadata")
        operation_detail.setWordWrap(True)
        controls = QHBoxLayout()
        self.radio_button = QPushButton(tr("shell.connect_radio"))
        self.radio_button.clicked.connect(self._toggle_radio)
        self.vara_button = QPushButton(tr("shell.connect_vara"))
        self.vara_button.clicked.connect(self._toggle_vara)
        self.control_button = QPushButton(tr("shell.start_control"))
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
            "inbox": MetricItem(tr("metric.inbox")),
            "unread": MetricItem(tr("metric.unread")),
            "outbox": MetricItem(tr("metric.outbox")),
            "transit": MetricItem(tr("metric.transit")),
            "sessions": MetricItem(tr("metric.sessions")),
            "heard": MetricItem(tr("metric.heard")),
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

        heading = QLabel(tr("readiness.title"))
        heading.setObjectName("PanelHeader")
        description = QLabel(tr("readiness.short"))
        description.setObjectName("Metadata")
        layout.addWidget(heading)
        layout.addWidget(description)

        self.readiness = QTreeWidget()
        self.readiness.setColumnCount(3)
        self.readiness.setHeaderLabels(
            [
                tr("readiness.component"),
                tr("readiness.state"),
                tr("readiness.detail"),
            ]
        )
        self.readiness.setRootIsDecorated(False)
        self.readiness.setAlternatingRowColors(True)
        self.readiness.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        self.readiness.header().setStretchLastSection(True)
        self.readiness.setColumnWidth(0, 145)
        self.readiness.setColumnWidth(1, 115)
        layout.addWidget(self.readiness, 1)

        hint = QLabel(tr("readiness.hint"))
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

        heading = QLabel(tr("activity.title"))
        heading.setObjectName("PanelHeader")
        self.activity_count = QLabel(tr("activity.events", count=0))
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
        self.activity.setAccessibleName(tr("activity.accessible"))
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
            tr("activity.events", count=len(self.runtime.events.history()))
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
        display_names = {
            "home": tr("menu.home"),
            "mail": tr("menu.mail"),
            "network": tr("menu.network"),
            "log": tr("menu.log"),
        }
        self.statusBar().showMessage(
            tr("workspace.status", name=display_names[name])
        )

    def show_spectrum(self) -> None:
        self.spectrum_window.show()
        self.spectrum_window.raise_()
        self.spectrum_window.activateWindow()

    def show_spectrum_if_applicable(self) -> None:
        if self.runtime.config.payload_backend == "vara_p2p":
            self.show_spectrum()

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
        radio_name = (
            config.radio
            or config.radio_backend
            or tr("context.not_configured")
        )
        self.context_detail.setText(
            tr(
                "context.radio_modem",
                radio=radio_name,
                modem=config.active_modem(),
            )
        )

        mailbox = snapshot.mailbox
        context_items = []
        if mailbox.unread:
            context_items.append(tr("context.unread", count=mailbox.unread))
        # A failed message stays in the outbox so it can be retried, but it is
        # not waiting to send -- report the two separately.
        pending = mailbox.outbox - mailbox.outbox_failed
        if pending > 0:
            context_items.append(tr("context.outbox", count=pending))
        if mailbox.outbox_failed:
            context_items.append(
                tr("context.outbox_failed", count=mailbox.outbox_failed)
            )
        if mailbox.transit:
            context_items.append(tr("context.transit", count=mailbox.transit))
        if snapshot.network.active_sessions:
            context_items.append(
                tr("context.sessions", count=snapshot.network.active_sessions)
            )
        if snapshot.vara.link_state == "CONNECTING":
            context_items.append(tr("context.vara_connecting"))
        self.context_activity.setText("  ·  ".join(context_items))
        self.context_activity.setVisible(bool(context_items))

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
        self.radio_status.set_status(
            radio_role,
            tr("status.radio_on")
            if snapshot.radio.connected
            else tr("status.radio_off"),
        )
        self.vara_status.set_status(
            vara_role,
            tr("status.vara_on")
            if snapshot.vara.command_connected
            else tr("status.vara_off"),
        )
        self.control_status.set_status(
            control_role,
            tr("status.control_on")
            if snapshot.network.control_channel_active
            else tr("status.control_off"),
        )
        self.hamlib_status.set_status(
            hamlib_role,
            tr("status.hamlib_ready")
            if dependency.hamlib_available
            else tr("status.hamlib_missing"),
        )
        self.radio_button.setText(
            tr("shell.disconnect_radio")
            if snapshot.radio.connected
            else tr("shell.connect_radio")
        )
        self.vara_button.setText(
            tr("shell.disconnect_vara")
            if snapshot.vara.command_connected
            else tr("shell.connect_vara")
        )
        self.control_button.setText(
            tr("shell.stop_control")
            if snapshot.network.control_channel_active
            else tr("shell.start_control")
        )
        if snapshot.network.control_channel_active:
            self.operation_state.set_status(
                "success", tr("status.control_active")
            )
        elif snapshot.radio.connected or snapshot.vara.command_connected:
            self.operation_state.set_status(
                "info", tr("status.hardware_connected")
            )
        else:
            self.operation_state.set_status(
                "inactive", tr("shell.station_idle")
            )

        rows = [
            (
                tr("ready.identity"),
                tr("common.ready")
                if config.callsign and config.callsign != "NOCALL"
                else tr("ready.needs_setup"),
                config.callsign or tr("ready.no_callsign"),
            ),
            (
                tr("ready.radio"),
                tr("common.configured")
                if config.radio_backend != "none"
                else tr("common.not_configured"),
                radio_name,
            ),
            (
                "Hamlib",
                tr("common.available")
                if dependency.hamlib_available
                else tr("common.missing"),
                dependency.hamlib_path or tr("ready.hamlib_guidance"),
            ),
            (
                f"VARA {config.vara_mode}",
                tr("ready.endpoint"),
                f"{config.vara_host}:{config.vara_cmd_port}",
            ),
            (
                tr("ready.payload"),
                payload,
                tr("ready.payload_detail"),
            ),
        ]
        self.readiness.clear()
        for component, state, detail in rows:
            self.readiness.addTopLevelItem(
                QTreeWidgetItem([component, state, detail])
            )

    def _show_settings(self) -> None:
        applied_audio = [
            self.runtime.config.audio_input,
            self.runtime.config.audio_output,
        ]
        dialog = SettingsDialog(
            self.runtime.config,
            self.theme_controller.preference,
            self,
            settings=self.settings,
        )

        def apply_changes() -> None:
            selected_audio = [
                self.runtime.config.audio_input,
                self.runtime.config.audio_output,
            ]
            audio_changed = selected_audio != applied_audio
            if audio_changed and self.runtime.operations.audio_transport is not None:
                verified = self.runtime.operations.restart_control_channel()
                if verified:
                    transport = self.runtime.operations.audio_transport
                    dialog.audio_status.setText(
                        dual(
                            "Applied and verified. "
                            f"RX: {transport.actual_input_device_name}; "
                            f"TX: {transport.actual_output_device_name}.",
                            "Použito a ověřeno. "
                            f"RX: {transport.actual_input_device_name}; "
                            f"TX: {transport.actual_output_device_name}.",
                        )
                    )
                else:
                    dialog.audio_status.setText(
                        dual(
                            "The selected audio endpoints could not be opened; "
                            "the control channel was stopped.",
                            "Vybrané zvukové endpointy se nepodařilo otevřít; "
                            "řídicí kanál byl zastaven.",
                        )
                    )
            applied_audio[:] = selected_audio
            self.runtime.operations.configure_vara_host_ptt()
            self.theme_controller.set_preference(dialog.selected_theme)
            self._rebuild_translated_ui()
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

    def _show_help(self) -> None:
        HelpDialog(self).exec()

    def _rebuild_translated_ui(self) -> None:
        current_name = "home"
        if hasattr(self, "workspace_stack"):
            current_widget = self.workspace_stack.currentWidget()
            current_name = next(
                (
                    name
                    for name, widget in self.workspace_names.items()
                    if widget is current_widget
                ),
                "home",
            )
        self.menuBar().clear()
        previous = self.takeCentralWidget()
        if previous is not None:
            previous.deleteLater()
        self._build_menu()
        self._build_shell()
        self._show_workspace(current_name)
        history = self.runtime.events.history()
        if history:
            self.activity.setPlainText(
                "\n".join(event.display_text for event in history)
            )

    def _check_for_updates_silently(self) -> None:
        self.runtime.request_update_check(self._update_check_completed)

    def _check_for_updates(self) -> None:
        if self.runtime.request_update_check(self._update_check_completed):
            self.statusBar().showMessage(
                dual(
                    "Checking for Guardian updates…",
                    "Kontroluji aktualizace Guardianu…",
                )
            )

    def _update_check_completed(self, result) -> None:
        if result.error is not None:
            self.statusBar().showMessage(
                dual(
                    f"Update check failed: {result.error}",
                    f"Kontrola aktualizací selhala: {result.error}",
                ),
                10_000,
            )
            return
        if result.value is None:
            self.statusBar().showMessage(
                dual("Guardian is up to date.", "Guardian je aktuální."),
                5_000,
            )
            return
        UpdateDialog(self.runtime, result.value, self).exec()

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            tr("about.title"),
            tr("about.body", app=__app_name__, version=__version__),
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
            dual("Start control channel", "Spustit řídicí kanál"),
            dual(
                "Start the live audio control channel? ARDOS control frames may "
                "key the configured radio only after you explicitly send or respond.",
                "Spustit živý zvukový řídicí kanál? Rámce ARDOS mohou zaklíčovat "
                "nastavené rádio až při výslovném odeslání nebo odpovědi.",
            ),
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.runtime.operations.start_control_channel()

    # ------------------------------ network file ---------------------- #
    _NETWORK_FILTER = "CSV (*.csv);;*"

    def _import_network(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, tr("menu.network_import"), "", self._NETWORK_FILTER
        )
        if not chosen:
            return
        try:
            report = read_csv(chosen)
        except OSError as exc:
            QMessageBox.warning(self, tr("menu.network_import"), str(exc))
            return
        for route in report.routes:
            self.runtime.routes.add(route)
        if report.routes:
            self.runtime.routes.save()
            self.runtime.refresh()
            self.workspace_names["network"].refresh()
        summary = tr("network.import_done", count=report.imported)
        if report.problems:
            summary += "\n\n" + "\n".join(report.problems[:12])
            if len(report.problems) > 12:
                summary += f"\n… (+{len(report.problems) - 12})"
        QMessageBox.information(self, tr("menu.network_import"), summary)
        self.runtime.events.publish(
            tr("network.import_done", count=report.imported), source="network"
        )

    def _export_network(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(
            self, tr("menu.network_export"), "guardian-network.csv",
            self._NETWORK_FILTER,
        )
        if chosen:
            self._write_network(chosen, self.runtime.routes.routes)

    def _save_network_template(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(
            self, tr("menu.network_template"), "guardian-network-template.csv",
            self._NETWORK_FILTER,
        )
        if chosen:
            self._write_network(chosen, TEMPLATE_ROWS)

    def _write_network(self, path: str, routes) -> None:
        try:
            write_csv(path, routes)
        except OSError as exc:
            QMessageBox.warning(self, tr("menu.network_export"), str(exc))
            return
        self.runtime.events.publish(
            tr("network.export_done", path=path), source="network"
        )

    def _restore_geometry(self) -> None:
        geometry = self.settings.value("ui/main_geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.settings.setValue("ui/main_geometry", self.saveGeometry())
        self.settings.sync()
        self.spectrum_window.shutdown()
        super().closeEvent(event)
