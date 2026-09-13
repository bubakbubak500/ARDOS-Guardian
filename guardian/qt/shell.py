"""Guardian's compact PySide6 operational shell."""

from __future__ import annotations

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QFontDatabase,
    QIcon,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QFileDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
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
from ..radio.presets import DUMMY_MODEL
from ..services import ApplicationSnapshot
from ..services import LogLevel
from ..config import SC_FTN_WAVEFORM
from ..ofdm.automatic import automatic_g2_policy
from ..waveforms.config import profile_for
from .alerts import AlertBanner
from .notifications import EmergencyDialog, NotificationCenter, SoundPlayer
from .diagnostics_dialog import DiagnosticsDialog
from .help_dialog import HelpDialog
from .inputs import FrequencySpinBox
from .log_format import render_events
from .log_workspace import LogWorkspace
from .mail_workspace import MailWorkspace
from .network_workspace import NetworkWorkspace
from .readiness_dialog import ReadinessDialog
from .runtime import ShellRuntime
from .settings_dialog import SettingsDialog
from .map_window import MapWindow
from .modem_workspace import ModemWorkspace
from .spectrum_window import SpectrumWindow
from .station_lab_workspace import StationLabWorkspace
from .theme import ThemeController, ThemePreference
from .transfer_progress import TransferPanel, transfer_state
from .update_dialog import UpdateDialog
from .window_geometry import fit_window_to_screen


PAYLOAD_LABELS = {
    "vara_p2p": "VARA P2P",
    "ofdm_vhf": "SC-FTN",
}


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


class WorkspaceStack(QStackedWidget):
    """Only the visible workspace determines how much content must scroll."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.currentChanged.connect(lambda _index: self.updateGeometry())

    def minimumSizeHint(self):
        current = self.currentWidget()
        return current.minimumSizeHint() if current is not None else QSize(0, 0)

    def sizeHint(self):
        current = self.currentWidget()
        return current.sizeHint() if current is not None else QSize(0, 0)

    def hasHeightForWidth(self):
        current = self.currentWidget()
        return current is not None and current.hasHeightForWidth()

    def heightForWidth(self, width):
        current = self.currentWidget()
        return current.heightForWidth(width) if current is not None else -1


class ShellContent(QWidget):
    def heightForWidth(self, width):
        # QScrollArea otherwise treats the layout's preferred height as a
        # minimum, adding a scrollbar even when every control already fits.
        layout = self.layout()
        return layout.minimumHeightForWidth(width) if layout is not None else -1


class GuardianMainWindow(QMainWindow):
    # This is deliberately below the smallest desktop we support in the
    # responsive layout.  The network pages and settings dialogs own their
    # vertical scrolling; the shell viewport is a fallback for a very short
    # desktop or expanded status/alert panels.
    MINIMUM_SIZE = QSize(720, 440)
    COMPACT_WIDTH = 1_050

    def __init__(
        self,
        runtime: ShellRuntime,
        settings: QSettings,
    ) -> None:
        super().__init__()
        self.runtime = runtime
        self.settings = settings
        self._last_station_lab_offer: tuple[str, int] | None = None
        self.runtime.operations.confirm_manual_qsy = self._confirm_manual_qsy
        self.theme_controller = ThemeController(settings, self)
        self.theme_controller.theme_changed.connect(self._refresh_log_format)
        # On Windows an owned top-level window is forced above its owner.
        # Keep the spectrum independent so either window can receive focus.
        self.spectrum_window = SpectrumWindow(runtime, settings)
        self.theme_controller.theme_changed.connect(
            self.spectrum_window.set_tokens
        )
        self.spectrum_window.set_tokens(self.theme_controller.tokens)

        self.setWindowTitle(f"{__app_name__} — ARDOS  v{__version__}")
        self.setWindowIcon(QIcon(str(get_ico_path())))
        self.setMinimumSize(self.MINIMUM_SIZE)
        self.resize(1366, 768)

        self._build_menu()
        self._build_shell()
        self._build_notifications()
        from .warships_window import WarshipsAccess
        self.warships_access = WarshipsAccess(self)
        self._restore_geometry()
        # A geometry saved before a monitor change or DPI change may be larger
        # than today's logical work area.  Fit it once after restoration; later
        # manual resizes are respected.
        fit_window_to_screen(
            self,
            preferred_size=self.size(),
            minimum_size=self.MINIMUM_SIZE,
            margin=8,
            center=False,
        )
        self._update_responsive_layout(self.width())
        self._refresh()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(500)
        self.refresh_timer.timeout.connect(self._refresh)
        self.refresh_timer.start()
        # Protocol work must run promptly enough for ARQ/control handshakes;
        # rendering and notification polling remain on the slower UI cadence.
        self.protocol_timer = QTimer(self)
        self.protocol_timer.setInterval(100)
        self.protocol_timer.timeout.connect(self._protocol_tick)
        self.protocol_timer.start()
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
        map_action = QAction(tr("map.menu"), self)
        map_action.setShortcut("Ctrl+Shift+M")
        map_action.triggered.connect(self.show_map)
        view_menu.addAction(map_action)
        view_menu.addSeparator()
        self.workspace_actions: dict[str, QAction] = {}
        workspace_group = QActionGroup(self)
        workspace_group.setExclusive(True)
        workspace_labels = (
            ("home", tr("menu.home")),
            ("mail", tr("menu.mail")),
            ("network", tr("menu.network")),
            ("log", tr("menu.log")),
            ("modem", tr("menu.modem")),
            ("station_lab", tr("menu.station_lab")),
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
            if name != "modem":
                view_menu.addAction(action)
            self.workspace_actions[name] = action

        tools_menu = self.menuBar().addMenu(tr("menu.tools"))
        readiness = QAction(tr("menu.readiness"), self)
        readiness.triggered.connect(self._show_readiness)
        tools_menu.addAction(readiness)
        diagnostics = QAction(tr("menu.diagnostics"), self)
        diagnostics.triggered.connect(self._show_diagnostics)
        diagnostics_menu = tools_menu.addMenu(tr("menu.diagnostics"))
        diagnostics.setText(dual("Connection diagnostics", "Diagnostika připojení"))
        diagnostics_menu.addAction(diagnostics)
        diagnostics_menu.addAction(self.workspace_actions["modem"])
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
        root = ShellContent()
        root.setObjectName("AppShell")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(6)
        outer.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.shell_scroll = QScrollArea()
        self.shell_scroll.setObjectName("ShellContentScroll")
        self.shell_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.shell_scroll.setWidgetResizable(True)
        self.shell_scroll.setWidget(root)
        self.setCentralWidget(self.shell_scroll)

        self.operational_header = self._build_operational_header()
        outer.addWidget(self.operational_header)
        self.alert_banner = AlertBanner()
        outer.addWidget(self.alert_banner)
        self.metric_strip = self._build_metric_strip()
        outer.addWidget(self.metric_strip)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setMinimumHeight(260)
        splitter.setChildrenCollapsible(False)
        self.workspace_stack = WorkspaceStack()
        self.workspace_names = {
            "home": self._build_workspace(),
            "mail": MailWorkspace(self.runtime),
            "network": NetworkWorkspace(self.runtime),
            "log": LogWorkspace(self.runtime),
            "modem": ModemWorkspace(self.runtime),
            "station_lab": StationLabWorkspace(self.runtime),
        }
        for workspace in self.workspace_names.values():
            self.workspace_stack.addWidget(workspace)
        splitter.addWidget(self.workspace_stack)
        self.activity_panel = self._build_activity()
        splitter.addWidget(self.activity_panel)
        splitter.setStretchFactor(0, 65)
        splitter.setStretchFactor(1, 35)
        splitter.setSizes([760, 420])
        self.shell_splitter = splitter
        outer.addWidget(splitter, 1)
        self.status_strip = self._build_status_strip()
        outer.addWidget(self.status_strip)

        self.statusBar().showMessage(
            tr("shell.ready")
        )

    def _update_responsive_layout(self, width: int | None = None) -> None:
        """Switch the shell's secondary panes before they become unusable.

        The normal desktop keeps the activity feed beside the workspace. At a
        narrow logical width it is available through an explicit toggle. Keep
        the header side by side to conserve height; the shell scroll viewport
        handles unusually long content without compressing its controls.
        """

        if not hasattr(self, "shell_splitter"):
            return
        width = self.width() if width is None else int(width)
        compact = width < self.COMPACT_WIDTH
        if compact == getattr(self, "_compact_layout", None):
            if compact:
                self._resize_compact_splitter()
            return
        self._compact_layout = compact

        self.shell_splitter.setOrientation(
            Qt.Orientation.Vertical if compact else Qt.Orientation.Horizontal
        )
        if compact:
            self._header_operation.setMinimumWidth(0)
            self._metric_layout.setSpacing(8)
            self.activity_toggle.setVisible(True)
            self.activity_toggle.setChecked(False)
            self.activity_panel.setVisible(False)
            self._resize_compact_splitter()
        else:
            self.shell_splitter.setSizes([760, 420])
            self._header_operation.setMinimumWidth(285)
            self._metric_layout.setSpacing(20)
            self.activity_toggle.setVisible(False)
            self.activity_toggle.setChecked(False)
            self.activity_panel.setVisible(True)

    def _resize_compact_splitter(self) -> None:
        """Give the active workspace all compact height unless requested."""

        if not getattr(self, "_compact_layout", False):
            return
        height = self.shell_splitter.height()
        if height <= 0:
            QTimer.singleShot(0, self._resize_compact_splitter)
            return
        if self.activity_panel.isVisible():
            first = max(180, int(height * 0.68))
            second = max(120, height - first)
            self.shell_splitter.setSizes([first, second])
        else:
            self.shell_splitter.setSizes([height, 0])

    def _toggle_activity_panel(self, checked: bool) -> None:
        """Expose the activity feed on compact screens without stealing space."""

        if not getattr(self, "_compact_layout", False):
            return
        self.activity_panel.setVisible(bool(checked))
        if checked:
            available = max(320, self.shell_splitter.height())
            self.shell_splitter.setSizes(
                [max(220, int(available * 0.68)), max(120, int(available * 0.32))]
            )
        else:
            self.shell_splitter.setSizes([max(220, self.shell_splitter.height()), 0])

    def _build_operational_header(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("OperationalHeader")
        layout = QHBoxLayout(panel)
        self._header_layout = layout
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
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
        self.manual_frequency_row = QWidget()
        manual_frequency_layout = QHBoxLayout(self.manual_frequency_row)
        manual_frequency_layout.setContentsMargins(0, 0, 0, 0)
        manual_frequency_layout.setSpacing(8)
        self.manual_frequency_label = QLabel(tr("shell.manual_frequency"))
        self.manual_frequency = FrequencySpinBox()
        self.manual_frequency.setValue(
            int(self.runtime.config.manual_frequency_hz or 0)
        )
        self.manual_frequency.setKeyboardTracking(False)
        self.manual_frequency.editingFinished.connect(
            self._manual_frequency_changed
        )
        manual_frequency_layout.addWidget(self.manual_frequency_label)
        manual_frequency_layout.addWidget(self.manual_frequency)
        manual_frequency_layout.addStretch()
        context_layout.addWidget(self.manual_frequency_row)
        context_layout.addWidget(self.context_activity)
        context_layout.addStretch()
        layout.addWidget(context, 1)

        # The header's dead middle: while VARA is moving a payload this is the
        # only place in the shell that says how far along it is.
        self.transfer_panel = TransferPanel(self.theme_controller.tokens)
        self.theme_controller.theme_changed.connect(
            self.transfer_panel.set_tokens
        )
        layout.addWidget(self.transfer_panel, 2)

        operation = QWidget()
        operation.setMinimumWidth(285)
        self._header_operation = operation
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
        self._metric_layout = layout
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
        self._activity_rendered_events = None
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
        self.activity_toggle = QPushButton(tr("activity.title"))
        self.activity_toggle.setCheckable(True)
        self.activity_toggle.setChecked(False)
        self.activity_toggle.clicked.connect(self._toggle_activity_panel)
        self.activity_toggle.setVisible(False)
        layout.addWidget(self.activity_toggle)
        return panel

    def _build_notifications(self) -> None:
        """The tray presence and the two announcement levels."""
        player = SoundPlayer(
            self.runtime.config,
            on_refused=lambda reason: self.runtime.events.publish(
                dual(
                    f"Notification chime withheld: {reason}.",
                    f"Zvuk upozornění zadržen: {reason}.",
                ),
                LogLevel.WARNING,
                source="ui",
            ),
        )
        self.tray: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(QIcon(str(get_ico_path())), self)
            self.tray.setToolTip(
                f"{__app_name__} — {self.runtime.config.callsign}"
            )
            menu = QMenu(self)
            open_action = QAction(tr("tray.open"), menu)
            open_action.triggered.connect(self._restore_from_tray)
            menu.addAction(open_action)
            menu.addSeparator()
            exit_action = QAction(tr("menu.exit"), menu)
            exit_action.triggered.connect(self.close)
            menu.addAction(exit_action)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(self._tray_activated)
            self.tray.show()
        self.emergency_dialog = EmergencyDialog(
            player.play, self._notification_sound_allowed
        )
        self.notifications = NotificationCenter(
            self.runtime,
            toast=self._show_toast,
            emergency=self._show_emergency,
            play=player.play,
            window_active=self.isActiveWindow,
            sound_allowed=self._notification_sound_allowed,
        )

    def _notification_sound_allowed(self) -> bool:
        # Not while transmitting, and not while VARA holds the shared codec:
        # the operator is listening to the channel, not to the desktop.
        snapshot = self.runtime.snapshots.read()
        return not snapshot.radio.ptt and not self.runtime.operations.payload_active()

    def _tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._restore_from_tray()

    def _restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _show_toast(self, title: str, body: str) -> None:
        if self.tray is not None:
            self.tray.showMessage(
                title, body, QSystemTrayIcon.MessageIcon.Information, 6_000
            )

    def _show_emergency(self, title: str, body: str) -> None:
        self.emergency_dialog.announce(title, body)
        QApplication.alert(self)

    def _protocol_tick(self) -> None:
        """Advance workers and protocol state on the dedicated 100 ms clock."""
        self.runtime.drain_workers()
        self.runtime.tick()

    def _refresh(self) -> None:
        # Folder labels read the store directly. Refresh that same mailbox
        # state before rendering the header, including changes made by RX/TX.
        self.runtime.refresh()
        snapshot = self.runtime.snapshots.read()
        self._apply_snapshot(snapshot)
        self._poll_station_lab_offer()
        self.notifications.poll()
        self.runtime.events.drain()
        activity_history = self.runtime.events.activity_history()
        if activity_history != self._activity_rendered_events:
            render_events(self.activity, activity_history)
            self._activity_rendered_events = activity_history
        self.activity_count.setText(
            tr("activity.events", count=len(activity_history))
        )
        # The map is a plain window, not a workspace, so the poll has to feed
        # it the way it feeds the alert banner.
        map_window = getattr(self, "map_window", None)
        if map_window is not None and map_window.isVisible():
            map_window.refresh()
        active = self.workspace_stack.currentWidget()
        refresh = getattr(active, "refresh", None)
        if callable(refresh):
            refresh()

    def _poll_station_lab_offer(self) -> None:
        """Prompt once for an incoming paired SC-FTN calibration offer."""
        if self.runtime.config.payload_backend != "ofdm_vhf":
            return
        status = getattr(self.runtime.operations, "station_lab", None)
        peer = str(getattr(status, "peer", "") or "").strip().upper()
        session_id = int(getattr(status, "session_id", 0) or 0)
        pending = bool(getattr(status, "pending_offer", False))
        key = (peer, session_id)
        if not pending or not peer or not session_id:
            self._last_station_lab_offer = None
            return
        if key == self._last_station_lab_offer:
            return
        self._last_station_lab_offer = key
        self._show_workspace("station_lab")
        answer = QMessageBox.question(
            self,
            dual("Incoming SC-FTN AutoTune", "Příchozí AutoTune SC-FTN"),
            dual(
                f"{peer} requests a bounded station calibration run. Accept the "
                "request and allow the peer to key this station?",
                f"{peer} žádá o omezenou kalibraci stanice. Přijmout žádost a "
                "dovolit protistanici zaklíčovat tuto stanici?",
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        current = getattr(self.runtime.operations, "station_lab", None)
        still_pending = (
            bool(getattr(current, "pending_offer", False))
            and str(getattr(current, "peer", "") or "").strip().upper() == peer
            and int(getattr(current, "session_id", 0) or 0) == session_id
        )
        if still_pending:
            if answer == QMessageBox.StandardButton.Yes:
                self.runtime.operations.accept_station_calibration()
            else:
                self.runtime.operations.reject_station_calibration()
        workspace = self.workspace_names.get("station_lab")
        refresh = getattr(workspace, "refresh", None)
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
            "modem": tr("menu.modem"),
            "station_lab": tr("menu.station_lab"),
        }
        self.statusBar().showMessage(
            tr("workspace.status", name=display_names[name])
        )

    def _refresh_log_format(self, _tokens=None) -> None:
        activity_history = self.runtime.events.activity_history()
        if hasattr(self, "activity"):
            render_events(self.activity, activity_history)
            self._activity_rendered_events = activity_history
        workspace = getattr(self, "workspace_names", {}).get("log")
        if workspace is not None:
            workspace.invalidate_format()

    def show_spectrum(self) -> None:
        self.spectrum_window.show()
        self.spectrum_window.raise_()
        self.spectrum_window.activateWindow()

    def show_spectrum_if_applicable(self) -> None:
        if self.runtime.config.payload_backend == "vara_p2p":
            self.show_spectrum()

    def _sc_status(self):
        """Read the explicit SC-FTN status contract from Operations.

        The shell must not reach through payload implementation details to
        discover a status object.  Operations returns ``None`` when SC-FTN is
        not selected or has no active backend.
        """
        return self.runtime.operations.ofdm_status()

    def _apply_snapshot(self, snapshot: ApplicationSnapshot) -> None:
        self.alert_banner.show_latest(self.runtime.operations.alerts)
        config = self.runtime.config
        sc_selected = config.payload_backend == "ofdm_vhf"
        sc_status = self._sc_status() if sc_selected else None
        no_cat = (
            config.radio_backend == "hamlib"
            and int(config.rig_model or 0) == DUMMY_MODEL
        )
        self.manual_frequency_row.setVisible(no_cat)
        if no_cat and not self.manual_frequency.hasFocus():
            self.manual_frequency.setValue(int(config.manual_frequency_hz or 0))
        payload = PAYLOAD_LABELS.get(config.payload_backend, "VARA P2P")
        mode_label = (
            f"SC-FTN {str(config.g2_bandwidth).strip().upper()}"
            if sc_selected
            else config.vara_mode
        )
        self.context_value.setText(
            f"{config.callsign or 'NOCALL'}  ·  {mode_label}  ·  {payload}"
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
        self.transfer_panel.apply(
            transfer_state(
                snapshot,
                self.runtime.operations.payload_active(),
                sc_status,
            )
        )

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
        hamlib_required = config.radio_backend == "hamlib"
        hamlib_role = "success" if dependency.hamlib_available else "warning"
        self.radio_status.set_status(
            radio_role,
            tr("status.radio_on")
            if snapshot.radio.connected
            else tr("status.radio_off"),
        )
        if sc_selected:
            sc_state = str(getattr(sc_status, "state", "idle") or "idle").lower()
            self.vara_status.set_status(
                "success" if sc_state != "idle" else "inactive",
                dual("SC-FTN: active", "SC-FTN: aktivní")
                if sc_state != "idle"
                else dual("SC-FTN: ready", "SC-FTN: připraveno"),
            )
        else:
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
        # Guardian UART and VOX paths do not use Hamlib.  Keeping a visible
        # "Hamlib missing" badge for those paths falsely reports a blocker.
        self.hamlib_status.setVisible(hamlib_required)
        if hamlib_required:
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

        identity_row = (
            tr("ready.identity"),
            tr("common.ready")
            if config.callsign and config.callsign != "NOCALL"
            else tr("ready.needs_setup"),
            config.callsign or tr("ready.no_callsign"),
        )
        radio_row = (
            tr("ready.radio"),
            tr("common.configured")
            if config.radio_backend != "none"
            else tr("common.not_configured"),
            radio_name,
        )
        if sc_selected:
            sc_policy_summary = ""
            try:
                policy = automatic_g2_policy(
                    SC_FTN_WAVEFORM,
                    str(config.g2_bandwidth).strip().upper(),
                    radio_backend=config.radio_backend,
                    radio_model=config.radio,
                )
                profile = ModemWorkspace.effective_profile(
                    policy, policy.bandwidth
                )
                geometry = (
                    f"{profile.name}; {profile.occupied_bandwidth:.0f} Hz; "
                    f"center {profile.center_hz:.1f} Hz; "
                    f"symbol {profile.symbol_rate:.1f} sym/s"
                )
                sc_state = tr("common.ready")
                sc_policy_summary = policy.summary()
            except ValueError as exc:
                geometry = str(exc)
                sc_state = tr("common.missing")
            rows = [identity_row, radio_row]
            if config.radio_backend == "hamlib":
                rows.append(
                    (
                        "Hamlib",
                        tr("common.available")
                        if dependency.hamlib_available
                        else tr("common.missing"),
                        dependency.hamlib_path or tr("ready.hamlib_guidance"),
                    )
                )
            rows.extend(
                [
                    (
                        dual("Audio RX", "Zvuk RX"),
                        tr("common.configured")
                        if str(config.audio_input).strip()
                        else tr("common.not_configured"),
                        str(config.audio_input).strip() or dual("Select an input", "Vyberte vstup"),
                    ),
                    (
                        dual("Audio TX", "Zvuk TX"),
                        tr("common.configured")
                        if str(config.audio_output).strip()
                        else tr("common.not_configured"),
                        str(config.audio_output).strip() or dual("Select an output", "Vyberte výstup"),
                    ),
                    (
                        tr("ready.payload"),
                        f"{payload} / AUTO / {sc_state}",
                        geometry + (f"; {sc_policy_summary}" if sc_policy_summary else ""),
                    ),
                ]
            )
        else:
            rows = [
                identity_row,
                radio_row,
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
        operations = self.runtime.operations
        applied_audio = [
            self.runtime.config.audio_input,
            self.runtime.config.audio_output,
        ]
        applied_vara = [operations.vara_endpoint(), operations.vara_tuning()]
        applied_radio = [operations.radio_settings()]
        dialog = SettingsDialog(
            self.runtime.config,
            self.theme_controller.preference,
            self,
            settings=self.settings,
            operations=operations,
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

            # VARA only knows what it has been told. A setting changed here
            # never reached a already-connected modem before 0.6.33, so the
            # HF bandwidth silently stayed on whatever connect-time sent.
            endpoint, tuning = operations.vara_endpoint(), operations.vara_tuning()
            if operations.vara.connected:
                if endpoint != applied_vara[0]:
                    self.runtime.events.publish(
                        tr("vara.reconnecting_for_settings"), source="vara"
                    )
                    operations.disconnect_vara()
                    operations.connect_vara()
                elif tuning != applied_vara[1]:
                    if operations.apply_vara_session_settings():
                        self.runtime.events.publish(
                            tr("vara.settings_applied", bandwidth=tuning[0]),
                            source="vara",
                        )
            applied_vara[:] = [endpoint, tuning]

            # The radio driver is built from config once; a changed backend,
            # port or PTT wiring only exists in the config until it is rebuilt.
            radio_now = operations.radio_settings()
            if radio_now != applied_radio[0]:
                operations.reconfigure_radio()
            applied_radio[0] = radio_now

            self.runtime.operations.configure_vara_host_ptt()
            self.runtime.operations.apply_network_settings()
            if self.runtime.topology.links:
                self.runtime.routes.replace_topology(
                    self.runtime.topology.derive_routes(
                        self.runtime.config.callsign
                    )
                )
                self.runtime.routes.save()
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

    def show_map(self) -> None:
        """Open the station map, refreshed by the ordinary UI poll."""
        if getattr(self, "map_window", None) is None:
            # An owned top-level window is forced above its owner on Windows.
            # The map is an independent operating view, like the spectrum.
            self.map_window = MapWindow(self.runtime)
        self.map_window.show()
        self.map_window.raise_()
        self.map_window.activateWindow()

    def _manual_frequency_changed(self) -> None:
        self.runtime.operations.set_manual_frequency(
            self.manual_frequency.value()
        )

    def _confirm_manual_qsy(
        self, callsign: str, frequency_hz: int, mode: str
    ) -> bool:
        current = int(self.runtime.config.manual_frequency_hz or 0)
        answer = QMessageBox.question(
            self,
            tr("shell.manual_qsy_title"),
            tr(
                "shell.manual_qsy",
                callsign=callsign,
                frequency=f"{frequency_hz / 1_000_000:.4f} MHz",
                mode=mode or "—",
                current=(
                    f"{current / 1_000_000:.4f} MHz"
                    if current
                    else tr("shell.manual_frequency_unknown")
                ),
            ),
            QMessageBox.StandardButton.Ok
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Ok

    def _show_diagnostics(self) -> None:
        self.runtime.drain_workers()
        DiagnosticsDialog(self.runtime, self).exec()

    def _show_help(self) -> None:
        HelpDialog(self).exec()

    def _rebuild_translated_ui(self) -> None:
        current_name = "home"
        log_filter = None
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
            previous_log = self.workspace_names.get("log")
            if previous_log is not None:
                log_filter = (
                    previous_log.level.currentData(),
                    previous_log.search.text(),
                )
        self.menuBar().clear()
        previous = self.takeCentralWidget()
        if previous is not None:
            previous.deleteLater()
        self._build_menu()
        self._compact_layout = None
        self._build_shell()
        self._update_responsive_layout(self.width())
        if log_filter is not None:
            log_workspace = self.workspace_names["log"]
            index = log_workspace.level.findData(log_filter[0])
            if index >= 0:
                log_workspace.level.setCurrentIndex(index)
            log_workspace.search.setText(log_filter[1])
        self._show_workspace(current_name)
        history = self.runtime.events.activity_history()
        render_events(self.activity, history)
        self._activity_rendered_events = history

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

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_responsive_layout(event.size().width())

    def _restore_geometry(self) -> None:
        geometry = self.settings.value("ui/main_geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.warships_access.shutdown()
        self.refresh_timer.stop()
        self.protocol_timer.stop()
        self.settings.setValue("ui/main_geometry", self.saveGeometry())
        self.settings.sync()
        if getattr(self, "tray", None) is not None:
            self.tray.hide()
        emergency = getattr(self, "emergency_dialog", None)
        if emergency is not None:
            emergency.shutdown()
        self.spectrum_window.shutdown()
        map_window = getattr(self, "map_window", None)
        if map_window is not None:
            map_window.close()
        super().closeEvent(event)
