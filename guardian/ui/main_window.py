"""Guardian main window (CustomTkinter).

A control panel for the ARDOS control/routing layer. Phase 1 focuses on the
operator surface: station identity, radio + VARA connection, the configurable
route table, a message composer that builds real control bursts, and a live
log. Backend wiring is real where it can be (config, routes, frame encoding)
and connection-ready stubs where hardware/VARA must be present.
"""

from __future__ import annotations

import datetime
import os
import tempfile
import threading
import time
import webbrowser
from tkinter import filedialog

import customtkinter as ctk

from .. import __app_name__, __version__
from ..assets import get_ico_path, get_tray_image
from ..config import StationConfig
from ..protocol import ControlFrame, FrameType, Priority, Flags
from ..install import hamlib_installer
from ..message import Attachment, Folder, MailMessage, MessageStore, Status
from ..message.forms import FORMS, form_names
from ..radio import RadioState, make_driver
from ..radio.presets import CURATED, load_hamlib_models
from ..radio.rigctld_launcher import RigctldProcess
from ..modem import make_modem
from ..modem.audio import AudioControlTransport, default_device_names, list_audio_devices, resolve_device
from ..payload import make_backend
from ..radio.scanner import Channel, ChannelPlan, ChannelScanner
from ..radio.usb_serial import detect as detect_usb_serial
from ..radio.usb_serial import list_serial_ports, port_device
from ..routing import HeardStations, Route, RouteTable
from ..session import NullTransport, Orchestrator, SessionState
from ..vara import VaraClient

POLL_MS = 2000
GREEN = "#2faa4d"
RED = "#c0392b"
AMBER = "#d68910"
GREY = "#7f8c8d"


class GuardianApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()

        self.cfg = StationConfig.load()
        self.routes = RouteTable.load()
        self.radio = make_driver(self.cfg)
        self.rigctld = RigctldProcess(self.cfg.rigctld_path)
        self.vara = VaraClient(self.cfg.vara_host, self.cfg.vara_cmd_port, self.cfg.vara_data_port)
        self.vara.on_notification = self._on_vara_notification
        self._apply_vara_host_ptt()

        # Control-net runs on the real audio modem over the radio. Until the
        # operator starts the audio control channel it sits on a NullTransport
        # (idle) — there is no on-PC loopback/simulation path.
        if self.cfg.control_channel not in ("off", "audio"):
            self.cfg.control_channel = "off"
        self.heard = HeardStations()
        self.mailstore = MessageStore()
        self.mail_folder = Folder.INBOX
        self.mail_selected = None
        self._stored_inbound: set = set()
        # Message-tracking + net-awareness view state (built lazily per row/card,
        # updated in place each refresh — never re-created per tick).
        self._session_cards: dict = {}        # msg_id -> {frame, stage labels, …}
        self._msg_progress: dict[int, int] = {}   # msg_id -> furthest milestone reached
        self._msg_times: dict[int, list] = {}     # msg_id -> [started_str, updated_str]
        self._heard_rows: dict = {}           # callsign -> {frame, dot, labels…}
        self.channel_plan = ChannelPlan.load()
        self.scanner = ChannelScanner(
            self.radio, self.channel_plan, dwell=self.cfg.scan_dwell,
            on_change=lambda ch: self.log(f"Scan -> {ch.name} ({ch.freq_hz/1e6:.4f} MHz)"),
            on_log=self.log,
        )
        self.audio_transport = None  # set when control channel = audio
        self._deliver_attempts: dict[int, float] = {}
        self._last_beacon = 0.0
        self._last_autodeliver = 0.0
        self.net = self._build_net(NullTransport())

        ctk.set_appearance_mode(self.cfg.appearance)
        ctk.set_default_color_theme("blue")

        self.title(f"{__app_name__} — ARDOS Control Layer  v{__version__}")
        self.geometry("1040x780")
        self.minsize(900, 700)
        self._apply_icon()

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_tabs()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.tray = None
        self._start_tray()
        self.log(f"{__app_name__} v{__version__} started. Station: {self.cfg.callsign}")
        # Radio state is polled on a BACKGROUND thread — querying Hamlib means
        # several blocking CI-V round-trips, which would otherwise freeze the UI
        # (typing lag) every poll. The UI just reads the cached snapshot.
        self._radio_state = RadioState(connected=False)
        self._closing = False
        self._radio_thread = threading.Thread(target=self._radio_poll_loop, name="radio-poll", daemon=True)
        self._radio_thread.start()
        self._poll()
        self._net_loop()

    # ------------------------------------------------------------------ #
    #  Icon + system tray                                                 #
    # ------------------------------------------------------------------ #
    def _apply_icon(self) -> None:
        ico = str(get_ico_path())

        def apply():
            # default= sets the icon for this window and all future toplevels.
            self._safe(lambda: self.iconbitmap(default=ico))
            self._safe(lambda: self.wm_iconbitmap(ico))

        apply()
        # A PhotoImage icon as well (helps Alt-Tab / some shells), kept on self
        # so it isn't garbage-collected.
        try:
            from PIL import ImageTk
            self._icon_photo = ImageTk.PhotoImage(get_tray_image())
            self.iconphoto(True, self._icon_photo)
        except Exception:
            pass
        # CustomTkinter resets the icon shortly after creation — re-apply twice.
        self.after(300, lambda: self._safe(apply))
        self.after(1000, lambda: self._safe(apply))

    def _start_tray(self) -> None:
        try:
            import pystray
        except Exception:
            return

        def show(*_):
            self.after(0, self._show_window)

        def hide(*_):
            self.after(0, self.withdraw)

        def quit_app(*_):
            self.after(0, self._on_close)

        menu = pystray.Menu(
            pystray.MenuItem("Open Guardian", show, default=True),
            pystray.MenuItem("Hide to tray", hide),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", quit_app),
        )
        self.tray = pystray.Icon("guardian", get_tray_image(), "Guardian — ARDOS", menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def _show_window(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    @staticmethod
    def _safe(fn) -> None:
        try:
            fn()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Sidebar                                                            #
    # ------------------------------------------------------------------ #
    def _build_sidebar(self) -> None:
        bar = ctk.CTkFrame(self, width=210, corner_radius=0)
        bar.grid(row=0, column=0, sticky="nsew")
        bar.grid_rowconfigure(12, weight=1)

        ctk.CTkLabel(bar, text="GUARDIAN", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, padx=20, pady=(20, 0))
        ctk.CTkLabel(bar, text="ARDOS control layer", text_color=GREY).grid(
            row=1, column=0, padx=20, pady=(0, 12))

        self.lbl_call = ctk.CTkLabel(bar, text=self.cfg.callsign, font=ctk.CTkFont(size=18, weight="bold"))
        self.lbl_call.grid(row=2, column=0, padx=20, pady=(0, 2))
        self.lbl_mode = ctk.CTkLabel(bar, text="", text_color=GREY)
        self.lbl_mode.grid(row=3, column=0, padx=20, pady=(0, 12))

        self.dot_radio = self._status_row(bar, 4, "Radio")
        self.dot_vara = self._status_row(bar, 5, "VARA")
        self.dot_ptt = self._status_row(bar, 6, "PTT")
        self.dot_channel = self._status_row(bar, 7, "Control ch.")

        ctk.CTkLabel(bar, text="RX level", text_color=GREY).grid(row=8, column=0, padx=20, pady=(10, 0), sticky="w")
        self.side_rx = ctk.CTkProgressBar(bar, height=10)
        self.side_rx.set(0)
        self.side_rx.grid(row=9, column=0, padx=20, pady=(2, 0), sticky="ew")

        self.lbl_mailcount = ctk.CTkLabel(bar, text="", justify="left", text_color=GREY)
        self.lbl_mailcount.grid(row=10, column=0, padx=20, pady=(10, 0), sticky="w")
        self.lbl_netcount = ctk.CTkLabel(bar, text="Net: quiet", justify="left", text_color=GREY)
        self.lbl_netcount.grid(row=11, column=0, padx=20, pady=(8, 0), sticky="w")

        ctk.CTkButton(bar, text="⚙  Settings", fg_color=GREY,
                      command=lambda: self.tabs.set("⚙ Settings")).grid(
            row=13, column=0, padx=20, pady=(8, 20), sticky="ew")

    def _status_row(self, parent, row: int, label: str):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=row, column=0, padx=20, pady=4, sticky="ew")
        dot = ctk.CTkLabel(frame, text="●", text_color=GREY, font=ctk.CTkFont(size=16))
        dot.pack(side="left")
        ctk.CTkLabel(frame, text=f"  {label}").pack(side="left")
        return dot

    # ------------------------------------------------------------------ #
    #  Tabs                                                               #
    # ------------------------------------------------------------------ #
    def _build_tabs(self) -> None:
        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=0, column=1, padx=16, pady=16, sticky="nsew")
        # Operational tabs first (the things you DO), then a single Settings area.
        for name in ("Home", "Mail", "Net", "Mesh", "Log", "⚙ Settings"):
            self.tabs.add(name)
        self._build_dashboard(self.tabs.tab("Home"))
        self._build_mail_tab(self.tabs.tab("Mail"))
        self._build_net_tab(self.tabs.tab("Net"))
        self._build_mesh_tab(self.tabs.tab("Mesh"))
        self._build_log_tab(self.tabs.tab("Log"))
        self._build_settings_tab(self.tabs.tab("⚙ Settings"))

    def _build_settings_tab(self, tab) -> None:
        """All configuration lives here, grouped into sections."""
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        self.settings_tabs = ctk.CTkTabview(tab)
        self.settings_tabs.grid(row=0, column=0, sticky="nsew")
        for name in ("Station", "Radio", "VARA", "Channel", "Mesh", "Routing", "Advanced"):
            self.settings_tabs.add(name)
        self._build_station_settings(self.settings_tabs.tab("Station"))
        self._build_radio_tab(self.settings_tabs.tab("Radio"))
        self._build_vara_tab(self.settings_tabs.tab("VARA"))
        self._build_channel_settings(self.settings_tabs.tab("Channel"))
        self._build_mesh_settings(self.settings_tabs.tab("Mesh"))
        self._build_routing_tab(self.settings_tabs.tab("Routing"))
        self._build_messages_tab(self.settings_tabs.tab("Advanced"))

    def _goto_settings(self, section: str) -> None:
        self.tabs.set("⚙ Settings")
        self._safe(lambda: self.settings_tabs.set(section))

    # ---- Home (guided dashboard) -------------------------------------- #
    MODES = ["Live · VARA P2P", "Live · Winlink"]
    _MODE_DESC = {
        "Live · VARA P2P": "On-air, Guardian moves the payload itself over VARA. Needs radio + audio + VARA.",
        "Live · Winlink": "On-air, you transfer the payload with your own Winlink session. Needs radio + audio.",
    }

    def _build_dashboard(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(4, weight=1)

        mode_card = ctk.CTkFrame(tab)
        mode_card.grid(row=0, column=0, padx=10, pady=(10, 6), sticky="ew")
        mode_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(mode_card, text="Operating mode", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, padx=14, pady=(12, 2), sticky="w")
        self.mode_seg = ctk.CTkSegmentedButton(mode_card, values=self.MODES, command=self._set_mode)
        self.mode_seg.set(self._current_mode())
        self.mode_seg.grid(row=1, column=0, padx=14, pady=4, sticky="w")
        self.mode_desc = ctk.CTkLabel(mode_card, text="", text_color=GREY, justify="left")
        self.mode_desc.grid(row=2, column=0, padx=14, pady=(0, 12), sticky="w")

        steps_card = ctk.CTkFrame(tab)
        steps_card.grid(row=1, column=0, padx=10, pady=6, sticky="ew")
        steps_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(steps_card, text="Setup checklist", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, padx=14, pady=(12, 2), sticky="w")
        self.checklist = ctk.CTkFrame(steps_card, fg_color="transparent")
        self.checklist.grid(row=1, column=0, padx=8, pady=(0, 10), sticky="ew")
        self.checklist.grid_columnconfigure(1, weight=1)

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.grid(row=2, column=0, padx=10, pady=4, sticky="ew")
        ctk.CTkButton(actions, text="✎ Compose mail", command=self._compose_mail).pack(side="left", padx=6)
        ctk.CTkButton(actions, text="📬 Open Mail", fg_color=GREY, command=lambda: self.tabs.set("Mail")).pack(side="left", padx=6)
        ctk.CTkButton(actions, text="Connect radio", fg_color=GREY, command=self._connect_radio).pack(side="left", padx=6)
        self.vara_btn = ctk.CTkButton(actions, text="Connect VARA", fg_color=GREY, command=self._connect_vara)
        self.vara_btn.pack(side="left", padx=6)
        self.channel_btn = ctk.CTkButton(actions, text="Start control ch.", command=self._toggle_control_channel)
        self.channel_btn.pack(side="left", padx=6)
        self._refresh_channel_btn()
        self._refresh_mode_buttons()

        sig = ctk.CTkFrame(tab)
        sig.grid(row=3, column=0, padx=10, pady=6, sticky="ew")
        sig.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(sig, text="Signal & audio", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, columnspan=2, padx=14, pady=(12, 4), sticky="w")
        ctk.CTkLabel(sig, text="RX level").grid(row=1, column=0, padx=14, pady=4, sticky="w")
        self.sig_bar = ctk.CTkProgressBar(sig)
        self.sig_bar.set(0)
        self.sig_bar.grid(row=1, column=1, padx=14, pady=4, sticky="ew")
        self.sig_lbl = ctk.CTkLabel(sig, text="Audio control channel off (live mode shows levels here).",
                                    justify="left", text_color=GREY)
        self.sig_lbl.grid(row=2, column=0, columnspan=2, padx=14, pady=(0, 12), sticky="w")

        status = ctk.CTkFrame(tab)
        status.grid(row=4, column=0, padx=10, pady=(6, 10), sticky="nsew")
        status.grid_columnconfigure((0, 1, 2), weight=1)
        self.db_radio = self._status_card(status, 0, "Radio")
        self.db_vara = self._status_card(status, 1, "VARA")
        self.db_mail = self._status_card(status, 2, "Mailbox")
        self.db_station = ctk.CTkLabel(status, text="", justify="left", text_color=GREY)
        self.db_station.grid(row=1, column=0, columnspan=3, padx=14, pady=(0, 12), sticky="w")
        self._set_mode_desc()
        self._refresh_setup_checklist()
        self._refresh_station_card()

    def _status_card(self, parent, col: int, title: str):
        f = ctk.CTkFrame(parent)
        f.grid(row=0, column=col, padx=8, pady=10, sticky="nsew")
        ctk.CTkLabel(f, text=title, font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=12, pady=(10, 2))
        lbl = ctk.CTkLabel(f, text="—", justify="left", text_color=GREY)
        lbl.pack(anchor="w", padx=12, pady=(0, 10))
        return lbl

    def _current_mode(self) -> str:
        return "Live · VARA P2P" if self.cfg.payload_backend == "vara_p2p" else "Live · Winlink"

    def _set_mode_desc(self) -> None:
        self.mode_desc.configure(text=self._MODE_DESC.get(self.mode_seg.get(), ""))

    def _set_mode(self, mode: str) -> None:
        self._set_mode_desc()
        self.cfg.payload_backend = "vara_p2p" if "VARA" in mode else "winlink_manual"
        if hasattr(self, "payload_menu"):
            self.payload_menu.set(self.cfg.payload_backend)
        self.net.payload = self._payload_for_net()
        self._start_audio_channel()
        self.cfg.save()
        self._refresh_setup_checklist()
        self._refresh_station_card()
        self._refresh_mode_buttons()
        self.log(f"Mode: {mode}")

    def _refresh_setup_checklist(self) -> None:
        if not hasattr(self, "checklist"):
            return
        mode = self._current_mode()
        cs = self.cfg.callsign
        radio_ok = self._safe_bool(lambda: self.radio.is_open)
        audio_ok = self.audio_transport is not None
        vara_ok = self.vara.connected
        # Rebuilding ~15 CustomTkinter widgets is expensive; only do it when the
        # checklist state actually changes (otherwise it hitched every poll).
        sig = (mode, bool(cs and cs != "NOCALL"), radio_ok, audio_ok, vara_ok)
        if sig == getattr(self, "_checklist_sig", None):
            return
        self._checklist_sig = sig
        for w in self.checklist.winfo_children():
            w.destroy()

        steps = [("Set your callsign & station info",
                  bool(cs and cs != "NOCALL"), "Station")]
        steps.append(("Connect your radio (Hamlib or VOX)", radio_ok, "Radio"))
        steps.append(("Start the audio control channel", audio_ok, "Channel"))
        if mode == "Live · VARA P2P":
            steps.append(("Connect VARA (moves the payload)", vara_ok, "VARA"))
        else:
            steps.append(("Winlink: you'll transfer the payload yourself", None, "VARA"))
        ready = all(s[1] for s in steps if s[1] is not None)
        steps.append((f"Ready — compose in Mail{'' if ready else ' (finish the steps above)'}", ready, None))

        for i, (label, done, section) in enumerate(steps):
            mark = "✓" if done else ("➖" if done is None else "⬜")
            color = GREEN if done else (GREY if done is None else AMBER)
            ctk.CTkLabel(self.checklist, text=mark, text_color=color,
                         font=ctk.CTkFont(size=15)).grid(row=i, column=0, padx=(8, 6), pady=3, sticky="w")
            ctk.CTkLabel(self.checklist, text=f"{i+1}. {label}", anchor="w").grid(
                row=i, column=1, padx=4, pady=3, sticky="w")
            if section:
                ctk.CTkButton(self.checklist, text="Go", width=44, height=26,
                              command=lambda s=section: self._goto_settings(s)).grid(
                    row=i, column=2, padx=8, pady=3)

    def _refresh_mode_buttons(self) -> None:
        """In Live·Winlink the payload goes Winlink Express <-> VARA directly, so
        Guardian must NOT connect to VARA (one master per VARA). Hide its Connect
        VARA button in that mode; show it for VARA P2P."""
        if not hasattr(self, "vara_btn"):
            return
        if self.cfg.payload_backend == "winlink_manual":
            self.vara_btn.pack_forget()
        elif self.vara_btn.winfo_manager() != "pack":   # not currently shown
            self.vara_btn.pack(side="left", padx=6, before=self.channel_btn)

    @staticmethod
    def _safe_bool(fn) -> bool:
        try:
            return bool(fn())
        except Exception:
            return False

    def _refresh_station_card(self) -> None:
        if not hasattr(self, "db_station"):
            return
        c = self.cfg
        self.db_station.configure(
            text=(f"{c.callsign} · {c.operator_name or 'no operator'} · radio {c.radio or '-'} "
                  f"({c.radio_backend}) · VARA {c.vara_mode} · modem {c.active_modem()} · "
                  f"payload {c.payload_backend} · {len(self.routes)} routes"))

    # ---- Settings: Station -------------------------------------------- #
    def _build_station_settings(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(tab, text="Station identity", font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, columnspan=2, padx=14, pady=(12, 6), sticky="w")
        self._field(tab, 1, "Callsign", "callsign")
        self._field(tab, 2, "Operator name", "operator_name")
        self._field(tab, 3, "Default TTL (max hops)", "default_ttl")
        ctk.CTkLabel(tab, text="Appearance").grid(row=4, column=0, padx=14, pady=8, sticky="w")
        self.appearance_menu = ctk.CTkOptionMenu(tab, values=["System", "Dark", "Light"], command=self._set_appearance)
        self.appearance_menu.set(self.cfg.appearance)
        self.appearance_menu.grid(row=4, column=1, padx=14, pady=8, sticky="w")
        ctk.CTkButton(tab, text="Save", command=self._save_config).grid(row=5, column=0, padx=14, pady=14, sticky="w")

    # ---- Settings: Channel & payload ---------------------------------- #
    def _build_channel_settings(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(tab, text="Control channel", font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, columnspan=3, padx=14, pady=(12, 4), sticky="w")
        ctk.CTkLabel(tab, text="Channel").grid(row=1, column=0, padx=14, pady=6, sticky="w")
        self.channel_seg = ctk.CTkSegmentedButton(tab, values=["off", "audio"], command=self._set_control_channel)
        self.channel_seg.set(self.cfg.control_channel if self.cfg.control_channel in ("off", "audio") else "off")
        self.channel_seg.grid(row=1, column=1, padx=14, pady=6, sticky="w")
        ctk.CTkLabel(tab, text="off = control net idle (frees the codec for VARA) · audio = real RF via the radio",
                     text_color=GREY).grid(row=2, column=1, padx=14, sticky="w")

        ctk.CTkLabel(tab, text="Audio input").grid(row=3, column=0, padx=14, pady=6, sticky="w")
        self.audio_in_menu = ctk.CTkOptionMenu(tab, values=["(default)"],
                                               command=lambda _=None: self._audio_default_warn())
        self.audio_in_menu.grid(row=3, column=1, padx=14, pady=6, sticky="ew")
        ctk.CTkLabel(tab, text="Audio output").grid(row=4, column=0, padx=14, pady=6, sticky="w")
        self.audio_out_menu = ctk.CTkOptionMenu(tab, values=["(default)"],
                                                command=lambda _=None: self._audio_default_warn())
        self.audio_out_menu.grid(row=4, column=1, padx=14, pady=6, sticky="ew")
        ctk.CTkButton(tab, text="Refresh devices", command=self._refresh_audio_devices).grid(
            row=3, column=2, padx=8, pady=6)
        self.channel_status = ctk.CTkLabel(tab, text="", text_color=GREY)
        self.channel_status.grid(row=5, column=1, padx=14, pady=(0, 8), sticky="w")

        ctk.CTkLabel(tab, text="Payload transport", font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=6, column=0, columnspan=3, padx=14, pady=(14, 4), sticky="w")
        self.payload_menu = ctk.CTkOptionMenu(tab, values=["vara_p2p", "winlink_manual"], command=self._set_payload_backend)
        self.payload_menu.set(self.cfg.payload_backend)
        self.payload_menu.grid(row=7, column=1, padx=14, pady=6, sticky="w")
        ctk.CTkLabel(tab, text="vara_p2p = Guardian sends it over VARA · winlink_manual = you send via Winlink",
                     text_color=GREY).grid(row=8, column=1, padx=14, sticky="w")
        self._refresh_audio_devices()

    # ---- Settings: Mesh options --------------------------------------- #
    def _build_mesh_settings(self, tab) -> None:
        ctk.CTkLabel(tab, text="Smart routing & mesh", font=ctk.CTkFont(size=15, weight="bold")).pack(
            anchor="w", padx=14, pady=(12, 6))
        self.auto_route_chk = ctk.CTkCheckBox(
            tab, text="Auto-route — discover a next hop (ROUTE_QUERY) when none is configured",
            command=self._apply_mesh_opts)
        self.auto_relay_chk = ctk.CTkCheckBox(
            tab, text="Auto-relay — forward messages for other stations (mesh)", command=self._apply_mesh_opts)
        self.auto_deliver_chk = ctk.CTkCheckBox(
            tab, text="Auto-deliver — send waiting mail when its next hop is heard", command=self._apply_mesh_opts)
        self.beacon_chk = ctk.CTkCheckBox(
            tab, text="Presence beacon — periodically announce I'm here (so others can deliver to me)",
            command=self._apply_mesh_opts)
        for chk, on in ((self.auto_route_chk, self.cfg.auto_route),
                        (self.auto_relay_chk, self.cfg.auto_relay),
                        (self.auto_deliver_chk, self.cfg.auto_deliver),
                        (self.beacon_chk, self.cfg.beacon_enabled)):
            if on:
                chk.select()
            chk.pack(anchor="w", padx=18, pady=4)

    # ---- Radio tab ---------------------------------------------------- #
    def _build_radio_tab(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(tab, text="Radio control", font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, columnspan=2, padx=14, pady=(10, 2), sticky="w")

        # Friendly radio picker: choose by name, ids fill in automatically.
        ctk.CTkLabel(tab, text="Radio").grid(row=2, column=0, padx=14, pady=8, sticky="w")
        pick = ctk.CTkFrame(tab, fg_color="transparent")
        pick.grid(row=2, column=1, padx=14, pady=8, sticky="ew")
        pick.grid_columnconfigure(0, weight=1)
        self.preset_menu = ctk.CTkOptionMenu(
            pick, values=[p.label for p in CURATED], command=self._apply_preset
        )
        self.preset_menu.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(pick, text="Browse all…", width=90, command=self._browse_radios).grid(
            row=0, column=1, padx=(8, 0)
        )

        ctk.CTkLabel(tab, text="Radio backend").grid(row=3, column=0, padx=14, pady=8, sticky="w")
        self.backend_menu = ctk.CTkOptionMenu(tab, values=["none", "hamlib", "vox"], command=lambda _=None: None)
        self.backend_menu.set(self.cfg.radio_backend)
        self.backend_menu.grid(row=3, column=1, padx=14, pady=8, sticky="ew")

        self._field(tab, 4, "Radio model name", "radio")
        self._field(tab, 5, "Hamlib rig model id", "rig_model")

        # CAT / PTT COM port — pick from the live list, no typing.
        ctk.CTkLabel(tab, text="CAT / PTT COM port").grid(row=6, column=0, padx=14, pady=8, sticky="w")
        cport = ctk.CTkFrame(tab, fg_color="transparent")
        cport.grid(row=6, column=1, padx=14, pady=8, sticky="ew")
        cport.grid_columnconfigure(0, weight=1)
        self.cat_port_menu = ctk.CTkOptionMenu(cport, values=["(none)"])
        self.cat_port_menu.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(cport, text="↻", width=34, command=self._refresh_serial_ports).grid(row=0, column=1, padx=(8, 0))
        self._refresh_serial_ports()

        self._field(tab, 7, "rigctld host", "rigctld_host")
        self._field(tab, 8, "rigctld port", "rigctld_port")

        ctk.CTkLabel(tab, text="VOX PTT line").grid(row=10, column=0, padx=14, pady=8, sticky="w")
        self.ptt_menu = ctk.CTkOptionMenu(tab, values=["RTS", "DTR"])
        self.ptt_menu.set(self.cfg.ptt_line)
        self.ptt_menu.grid(row=10, column=1, padx=14, pady=8, sticky="ew")

        btns = ctk.CTkFrame(tab, fg_color="transparent")
        btns.grid(row=11, column=0, columnspan=2, padx=10, pady=14, sticky="ew")
        ctk.CTkButton(btns, text="Save", command=self._save_config).pack(side="left", padx=6)
        self.radio_btn = ctk.CTkButton(btns, text="Connect radio", command=self._toggle_radio)
        self.radio_btn.pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Test PTT (2s)", fg_color=AMBER, command=self._test_ptt).pack(side="left", padx=6)
        self._refresh_radio_btn()

        # Dependency helpers.
        tools = ctk.CTkFrame(tab)
        tools.grid(row=12, column=0, columnspan=2, padx=10, pady=(4, 12), sticky="ew")
        ctk.CTkLabel(tools, text="Drivers", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(8, 2))
        toolbtns = ctk.CTkFrame(tools, fg_color="transparent")
        toolbtns.pack(fill="x", padx=6, pady=(0, 6))
        ctk.CTkButton(toolbtns, text="Install / locate Hamlib", command=self._install_hamlib).pack(side="left", padx=6)
        ctk.CTkButton(toolbtns, text="Detect USB / serial adapter…", fg_color=GREY, command=self._detect_usb).pack(side="left", padx=6)
        self.hamlib_status = ctk.CTkLabel(tools, text="", text_color=GREY, justify="left")
        self.hamlib_status.pack(anchor="w", padx=10, pady=(0, 8))
        self._refresh_hamlib_status()

    # ---- VARA tab ----------------------------------------------------- #
    def _build_vara_tab(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(tab, text="VARA mode").grid(row=0, column=0, padx=14, pady=8, sticky="w")
        self.vara_mode_seg = ctk.CTkSegmentedButton(
            tab, values=["FM", "HF"], command=self._switch_vara_mode
        )
        self.vara_mode_seg.set(self.cfg.vara_mode)
        self.vara_mode_seg.grid(row=0, column=1, padx=14, pady=8, sticky="w")

        self.modem_lbl = ctk.CTkLabel(tab, text="", text_color=GREY)
        self.modem_lbl.grid(row=1, column=1, padx=14, pady=(0, 6), sticky="w")

        self._field(tab, 2, "VARA host", "vara_host")
        self._field(tab, 3, "Command port", "vara_cmd_port")
        self._field(tab, 4, "Data port", "vara_data_port")

        self.vara_hostptt_chk = ctk.CTkCheckBox(
            tab, text="Guardian keys PTT for VARA (host PTT — experimental)",
            command=self._toggle_vara_host_ptt)
        if self.cfg.vara_host_ptt:
            self.vara_hostptt_chk.select()
        self.vara_hostptt_chk.grid(row=4, column=2, padx=14, pady=6, sticky="w")
        ctk.CTkLabel(
            tab, text="Guardian keys the rig (CI-V/RTS/DTR via Hamlib) on VARA's PTT signal, so VARA\n"
                      "needs no COM port — generic across radios. Set VARA's own PTT to None/VOX.",
            text_color=GREY, justify="left").grid(row=5, column=2, padx=14, sticky="w")

        self.vara_handoffcom_chk = ctk.CTkCheckBox(
            tab, text="Hand the COM port to Winlink during hand-off (experimental)",
            command=self._toggle_vara_handoff_com)
        if self.cfg.vara_handoff_com:
            self.vara_handoffcom_chk.select()
        self.vara_handoffcom_chk.grid(row=6, column=2, padx=14, pady=6, sticky="w")
        ctk.CTkLabel(
            tab, text="Live·Winlink only: release the COM port + rigctld while you transfer in\n"
                      "Winlink (so its VARA can key PTT on rigs without VOX); reclaimed when you\n"
                      "confirm the hand-off dialog.",
            text_color=GREY, justify="left").grid(row=7, column=2, padx=14, sticky="w")

        btns = ctk.CTkFrame(tab, fg_color="transparent")
        btns.grid(row=6, column=0, columnspan=2, padx=10, pady=14, sticky="ew")
        ctk.CTkButton(btns, text="Save", command=self._save_config).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Connect VARA", command=self._connect_vara).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Disconnect", fg_color=GREY, command=self._disconnect_vara).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="LISTEN ON", command=lambda: self._vara_cmd(lambda: self.vara.listen(True))).pack(side="left", padx=6)

        ctk.CTkLabel(tab, text="VARA notifications:").grid(row=7, column=0, padx=14, pady=(10, 2), sticky="w")
        self.vara_box = ctk.CTkTextbox(tab, height=200)
        self.vara_box.grid(row=8, column=0, columnspan=2, padx=14, pady=(0, 14), sticky="nsew")
        tab.grid_rowconfigure(8, weight=1)
        self._update_modem_label()

    def _toggle_vara_host_ptt(self) -> None:
        self.cfg.vara_host_ptt = bool(self.vara_hostptt_chk.get())
        self._apply_vara_host_ptt()
        self.log("VARA host-PTT " + ("ENABLED — Guardian will key the rig on VARA's PTT signal "
                                     "(set VARA's own PTT to None)." if self.cfg.vara_host_ptt
                                     else "disabled — VARA keys its own PTT."))

    def _toggle_vara_handoff_com(self) -> None:
        self.cfg.vara_handoff_com = bool(self.vara_handoffcom_chk.get())
        self.log("Winlink COM hand-off " + ("ENABLED — COM/rigctld released during the hand-off "
                                            "and reclaimed on confirm." if self.cfg.vara_handoff_com
                                            else "disabled — Guardian keeps the COM."))

    def _switch_vara_mode(self, mode: str) -> None:
        # Remember the ports the user has typed for the *current* mode first.
        self._pull_vara_ports_into_cfg()
        self.cfg.remember_vara_ports()
        # Switch and reflect the new mode's ports in the entries.
        self.cfg.apply_vara_mode(mode)
        self._set_entry("vara_cmd_port", str(self.cfg.vara_cmd_port))
        self._set_entry("vara_data_port", str(self.cfg.vara_data_port))
        self._update_modem_label()
        self.log(f"VARA mode -> {mode}  (control modem: {self.cfg.active_modem()})")

    def _pull_vara_ports_into_cfg(self) -> None:
        try:
            self.cfg.vara_cmd_port = int(self._entries["vara_cmd_port"].get() or 0)
            self.cfg.vara_data_port = int(self._entries["vara_data_port"].get() or 0)
        except (ValueError, KeyError):
            pass

    def _update_modem_label(self) -> None:
        modem = self.cfg.active_modem()
        pretty = {"afsk1200": "AFSK 1200 (Bell 202)", "mfsk16": "MFSK-16 (HF robust)"}.get(modem, modem)
        self.modem_lbl.configure(text=f"Control-burst modem: {pretty}")

    # ---- Routing tab -------------------------------------------------- #
    def _build_routing_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        editor = ctk.CTkFrame(tab)
        editor.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        for i in range(5):
            editor.grid_columnconfigure(i, weight=1)
        for c, t in enumerate(("Destination / group", "Preferred next hop", "Backup (or ANY)",
                               "QSY freq MHz (opt.)", "Mode")):
            ctk.CTkLabel(editor, text=t).grid(row=0, column=c, padx=6, pady=4, sticky="w")
        self.r_dest = ctk.CTkEntry(editor, placeholder_text="OK1CCC")
        self.r_pref = ctk.CTkEntry(editor, placeholder_text="OK1DDD")
        self.r_back = ctk.CTkEntry(editor, placeholder_text="OK1EEE / ANY")
        self.r_freq = ctk.CTkEntry(editor, placeholder_text="145.500")
        self.r_mode = ctk.CTkOptionMenu(editor, values=["", "FM", "USB", "LSB", "DATA"], width=80)
        self.r_dest.grid(row=1, column=0, padx=6, pady=4, sticky="ew")
        self.r_pref.grid(row=1, column=1, padx=6, pady=4, sticky="ew")
        self.r_back.grid(row=1, column=2, padx=6, pady=4, sticky="ew")
        self.r_freq.grid(row=1, column=3, padx=6, pady=4, sticky="ew")
        self.r_mode.grid(row=1, column=4, padx=6, pady=4, sticky="w")
        btns = ctk.CTkFrame(editor, fg_color="transparent")
        btns.grid(row=2, column=0, columnspan=5, padx=2, pady=(2, 6), sticky="w")
        ctk.CTkButton(btns, text="Add / Update", command=self._add_route).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Remove", fg_color=GREY, command=self._remove_route).pack(side="left", padx=6)
        self.qsy_chk = ctk.CTkCheckBox(btns, text="Auto-QSY before VARA P2P (ignored for Winlink)",
                                       command=self._apply_qsy_opt)
        if self.cfg.auto_qsy:
            self.qsy_chk.select()
        self.qsy_chk.pack(side="left", padx=18)

        self.route_box = ctk.CTkTextbox(tab, font=ctk.CTkFont(family="Consolas", size=13))
        self.route_box.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")
        self._refresh_routes()

    def _apply_qsy_opt(self) -> None:
        self.cfg.auto_qsy = bool(self.qsy_chk.get())
        self.cfg.save()
        self.log(f"Auto-QSY: {self.cfg.auto_qsy}")

    # ---- Net tab (live session orchestration) ------------------------- #
    # ---- Message-tracking model -------------------------------------- #
    # A message's life as a small set of operator-meaningful milestones, so a
    # session can be shown as a "where is it now" progress strip rather than a
    # raw state name. Two tracks: outbound (I'm sending/relaying) and inbound.
    _OUT_STAGES = ["Queued", "Announced", "Acked", "Transfer", "Received", "Delivered"]
    _IN_STAGES = ["Heard", "Acked", "Receiving", "Received", "Delivered"]

    @property
    def _OUT_INDEX(self):
        return {
            SessionState.IDLE: 0, SessionState.ROUTE_DISCOVERY: 0,
            SessionState.ANNOUNCING: 1, SessionState.WAITING_BUSY: 1,
            SessionState.STARTING_VARA: 2, SessionState.TRANSFERRING: 3,
            SessionState.CONFIRMED: 4, SessionState.DELIVERED: 5,
        }

    @property
    def _IN_INDEX(self):
        return {
            SessionState.HEARD: 0, SessionState.ACKED: 1,
            SessionState.RECEIVING: 2, SessionState.RECEIVED_OK: 3,
            SessionState.DELIVERED: 4,
        }

    def _milestone(self, msg):
        """(stage names, furthest reached index, status) for a session.

        status ∈ {"progress","done","failed"}. Progress is monotonic — a retry
        or a stale frame can't drag the strip backwards."""
        if msg.direction == "out":
            stages, idx_map = self._OUT_STAGES, self._OUT_INDEX
        else:
            stages, idx_map = self._IN_STAGES, self._IN_INDEX
        prev = self._msg_progress.get(msg.msg_id, 0)
        reached = max(prev, idx_map.get(msg.state, prev))
        self._msg_progress[msg.msg_id] = reached
        if msg.state is SessionState.DELIVERED:
            status = "done"
        elif msg.state in (SessionState.FAILED, SessionState.CANCELLED):
            status = "failed"
        else:
            status = "progress"
        return stages, reached, status

    @staticmethod
    def _fmt_age(seconds: float) -> str:
        s = int(seconds)
        if s < 60:
            return f"{s}s ago"
        if s < 3600:
            return f"{s // 60}m ago"
        return f"{s // 3600}h ago"

    @staticmethod
    def _freshness(age: float) -> str:
        """Colour a heard station by how recently we last heard it."""
        if age < 120:
            return GREEN          # fresh — reachable right now
        if age < 600:
            return AMBER          # getting stale
        return GREY               # old, may be gone

    def _build_net_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)

        compose = ctk.CTkFrame(tab)
        compose.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        compose.grid_columnconfigure((1, 3), weight=1)
        ctk.CTkLabel(compose, text="Send a message over the control net",
                     font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, columnspan=4, padx=10, pady=(10, 6), sticky="w")

        ctk.CTkLabel(compose, text="Final destination").grid(row=1, column=0, padx=8, pady=4, sticky="w")
        self.n_final = ctk.CTkEntry(compose, placeholder_text="OK1CCC")
        self.n_final.grid(row=1, column=1, padx=8, pady=4, sticky="ew")
        ctk.CTkLabel(compose, text="Next hop (optional)").grid(row=1, column=2, padx=8, pady=4, sticky="w")
        self.n_next = ctk.CTkEntry(compose, placeholder_text="blank = route, else direct")
        self.n_next.grid(row=1, column=3, padx=8, pady=4, sticky="ew")

        ctk.CTkLabel(compose, text="Priority").grid(row=2, column=0, padx=8, pady=4, sticky="w")
        self.n_prio = ctk.CTkOptionMenu(compose, values=[p.name for p in Priority])
        self.n_prio.set(Priority.ROUTINE.name)
        self.n_prio.grid(row=2, column=1, padx=8, pady=4, sticky="w")

        ctk.CTkLabel(compose, text="Message").grid(row=3, column=0, padx=8, pady=4, sticky="nw")
        self.n_body = ctk.CTkEntry(compose, placeholder_text="message text / body…")
        self.n_body.grid(row=3, column=1, columnspan=3, padx=8, pady=4, sticky="ew")

        actions = ctk.CTkFrame(compose, fg_color="transparent")
        actions.grid(row=5, column=0, columnspan=4, padx=4, pady=8, sticky="ew")
        ctk.CTkButton(actions, text="Send over net", command=self._net_send).pack(side="left", padx=6)
        self.sim_note = ctk.CTkLabel(actions, text="", text_color=GREY)
        self.sim_note.pack(side="left", padx=6)

        # Bench test: drive the VARA payload phase directly, bypassing the
        # control-burst handshake. Lets you prove the VARA round-trip on real
        # radios before the on-air control modem is verified. Uses the fields
        # above (Next hop / Message). Keep the control channel on Loopback so
        # the radio's codec stays free for VARA.
        bench = ctk.CTkFrame(compose, fg_color="transparent")
        bench.grid(row=6, column=0, columnspan=4, padx=4, pady=(0, 8), sticky="ew")
        ctk.CTkLabel(bench, text="Bench test (bypass control net):",
                     text_color=AMBER).pack(side="left", padx=6)
        ctk.CTkButton(bench, text="Force SEND over VARA", width=170,
                      command=self._bench_send).pack(side="left", padx=6)
        ctk.CTkButton(bench, text="Force RECEIVE (LISTEN)", width=180,
                      command=self._bench_receive).pack(side="left", padx=6)

        hdr = ctk.CTkFrame(tab, fg_color="transparent")
        hdr.grid(row=2, column=0, padx=14, pady=(4, 0), sticky="ew")
        ctk.CTkLabel(hdr, text="Message tracking",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        self.sessions_summary = ctk.CTkLabel(hdr, text="", text_color=GREY)
        self.sessions_summary.pack(side="left", padx=10)
        self.session_panel = ctk.CTkScrollableFrame(tab, height=170)
        self.session_panel.grid(row=3, column=0, padx=10, pady=(2, 8), sticky="nsew")
        self.session_panel.grid_columnconfigure(0, weight=1)
        self._sessions_empty = ctk.CTkLabel(self.session_panel, text="(no messages yet)",
                                            text_color=GREY)
        self._sessions_empty.grid(row=0, column=0, padx=8, pady=8, sticky="w")

        ctk.CTkLabel(tab, text="On-air channel monitor").grid(row=4, column=0, padx=14, pady=(4, 0), sticky="w")
        self.channel_box = ctk.CTkTextbox(tab, height=140, font=ctk.CTkFont(family="Consolas", size=12))
        self.channel_box.grid(row=5, column=0, padx=10, pady=(0, 10), sticky="nsew")
        tab.grid_rowconfigure(3, weight=1)

    def _net_send(self) -> None:
        final = self.n_final.get().strip().upper()
        if not final:
            self.log("Net send: enter a final destination")
            return
        # Next hop is optional: explicit value wins; else use a route if one
        # exists; else connect directly to the destination. So a single reachable
        # station only needs the Final destination filled in.
        explicit = self.n_next.get().strip().upper()
        next_hop = explicit or (self.routes.next_hop(final) or final)
        msg_id = self.mailstore.next_id(self.cfg.callsign)
        msg = self.net.send_message(
            final_dest=final, body=self.n_body.get(), msg_id=msg_id,
            priority=Priority[self.n_prio.get()], next_hop=next_hop,
        )
        self.log(f"Net: started session #{msg.msg_id} -> {msg.next_hop} (final {final})")

    # ------------------------------------------------------------------ #
    #  Bench test — exercise the VARA payload layer with no control net   #
    # ------------------------------------------------------------------ #
    def _bench_send(self) -> None:
        final = self.n_final.get().strip().upper()
        explicit = self.n_next.get().strip().upper()
        if not (final or explicit):
            self.log("Bench: enter a Final destination (and/or Next hop) callsign first")
            return
        # VARA always connects to the NEXT HOP, never the final destination.
        # Resolve it from the route table when the Next-hop field is blank, so
        # the bench matches the real routed path (e.g. final OK2IPW -> OK2MTW).
        next_hop = explicit or (self.routes.next_hop(final) or final)
        if not self.vara.connected:
            self.log("Bench: VARA command port not connected — connect VARA first")
            return
        backend = self._make_payload_backend()
        msg_id = self.mailstore.next_id(self.cfg.callsign)
        self.net.force_send(
            final_dest=final or next_hop,
            next_hop=next_hop, msg_id=msg_id, body=self.n_body.get(),
            priority=Priority[self.n_prio.get()], payload=backend,
        )
        self.log(f"Bench: force-send #{msg_id} -> {next_hop} (final {final or next_hop}) "
                 f"over VARA (control net bypassed)")

    def _bench_receive(self) -> None:
        if not self.vara.connected:
            self.log("Bench: VARA command port not connected — connect VARA first")
            return
        backend = self._make_payload_backend()
        msg_id = self.mailstore.next_id(self.cfg.callsign)
        source = self.n_next.get().strip().upper() or "BENCH"
        self.net.force_receive(
            source=source, final_dest=self.cfg.callsign, msg_id=msg_id, payload=backend,
        )
        self.log(f"Bench: force-receive #{msg_id} — VARA LISTEN (control net bypassed)")

    def _refresh_audio_devices(self) -> None:
        inputs, outputs = list_audio_devices()
        in_vals = ["(default)"] + inputs
        out_vals = ["(default)"] + outputs
        self.audio_in_menu.configure(values=in_vals)
        self.audio_out_menu.configure(values=out_vals)
        self.audio_in_menu.set(self.cfg.audio_input if self.cfg.audio_input in in_vals else "(default)")
        self.audio_out_menu.set(self.cfg.audio_output if self.cfg.audio_output in out_vals else "(default)")
        if not inputs and not outputs:
            self.channel_status.configure(text="No audio backend/devices found.", text_color=AMBER)
        else:
            self._audio_default_warn(n_in=len(inputs), n_out=len(outputs))

    def _audio_default_warn(self, n_in: int | None = None, n_out: int | None = None) -> bool:
        """Warn (in channel_status) if a selected device is the Windows default —
        Windows then competes for the codec and routes system sounds into it.
        Returns True if the selection is clean (no clash)."""
        in_name, out_name = default_device_names()
        sel_in, sel_out = self.audio_in_menu.get(), self.audio_out_menu.get()
        clash = []
        if sel_in != "(default)" and in_name and sel_in.strip() == in_name:
            clash.append("input")
        if sel_out != "(default)" and out_name and sel_out.strip() == out_name:
            clash.append("output")
        if clash:
            self.channel_status.configure(
                text=("⚠ Selected " + " & ".join(clash) + " is the WINDOWS DEFAULT device. "
                      "Windows will compete for it and mix system sounds into your TX. "
                      "Open Windows Sound settings and set the default to your PC speakers/mic — "
                      "leave the radio codec for Guardian/VARA only."),
                text_color=AMBER)
            return False
        msg = "Audio devices OK"
        if n_in is not None:
            msg = f"{n_in} in / {n_out} out · radio codec is not the Windows default ✓"
        self.channel_status.configure(text=msg, text_color=GREY)
        return True

    def _set_control_channel(self, mode: str) -> None:
        if mode == "audio":
            self._start_audio_channel()
        else:
            self._stop_audio_channel()

    def _toggle_control_channel(self) -> None:
        """Home-page toggle: same on/off as the Channel segmented button. Often
        a quick off→on is all it takes to (re)open the codec cleanly."""
        if self.audio_transport is not None:
            self._stop_audio_channel()
        else:
            self._start_audio_channel()
        self._refresh_channel_btn()

    def _refresh_channel_btn(self, on: bool | None = None) -> None:
        if not hasattr(self, "channel_btn"):
            return
        if on is None:
            on = self.audio_transport is not None
        if on:
            self.channel_btn.configure(text="Control ch.: ON  (tap to stop)", fg_color=GREEN)
        else:
            self.channel_btn.configure(text="Start control ch.", fg_color=("#3B8ED0", "#1F6AA5"))

    def _stop_audio_channel(self) -> None:
        """Turn the control net OFF: stop the audio modem (frees the codec for
        VARA) and drop back to an idle NullTransport."""
        if getattr(self, "audio_transport", None) is not None:
            self._safe(self.audio_transport.stop)
            self.audio_transport = None
        self.cfg.control_channel = "off"
        self.net = self._build_net(NullTransport())
        self.channel_seg.set("off")
        self.sim_note.configure(text="Control net off.")
        self.channel_status.configure(text="Control net off (codec free for VARA).", text_color=GREY)
        self.log("Control channel: off")

    def _start_audio_channel(self) -> None:
        in_name = self.audio_in_menu.get()
        out_name = self.audio_out_menu.get()
        self.cfg.audio_input = "" if in_name == "(default)" else in_name
        self.cfg.audio_output = "" if out_name == "(default)" else out_name
        # Open by device INDEX, not name: a name like "USB Audio CODEC" exists
        # under every Windows host API (MME/DirectSound/WASAPI/WDM-KS), so a
        # name is ambiguous and sounddevice refuses it. resolve_device() picks
        # the unique index on the default host API.
        in_dev = None if in_name == "(default)" else resolve_device(in_name, "input")
        out_dev = None if out_name == "(default)" else resolve_device(out_name, "output")
        modem = make_modem(self.cfg.active_modem())
        transport = AudioControlTransport(
            modem=modem, ptt=self._radio_ptt,
            sample_rate=modem.fs if hasattr(modem, "fs") else 48000,
            input_device=in_dev, output_device=out_dev, on_log=self.log,
        )
        try:
            transport.start()
        except Exception as exc:  # noqa: BLE001 - no audio backend / bad device
            self.log(f"Audio channel failed: {exc} — control net stays off")
            self.cfg.control_channel = "off"
            self.channel_seg.set("off")
            self.channel_status.configure(text=f"Audio failed: {exc}", text_color=RED)
            return
        self.audio_transport = transport
        self.cfg.control_channel = "audio"
        self.net = self._build_net(transport)
        self.channel_seg.set("audio")
        self.sim_note.configure(text="LIVE audio over the radio.")
        self.channel_status.configure(text=f"Audio active ({modem.name}).", text_color=GREEN)
        self.log(f"Control channel: audio ({modem.name}) in={in_dev or 'default'} out={out_dev or 'default'}")
        in_def, out_def = default_device_names()
        if (in_name != "(default)" and in_def and in_name.strip() == in_def) or \
           (out_name != "(default)" and out_def and out_name.strip() == out_def):
            self.log("⚠ Selected audio device is the Windows DEFAULT — set Windows default to your "
                     "PC speakers/mic so the radio codec is used only by Guardian/VARA.")

    def _radio_ptt(self, on: bool) -> None:
        try:
            self.radio.set_ptt(on)
        except Exception as exc:  # noqa: BLE001
            self.log(f"PTT error: {exc}")

    def _apply_vara_host_ptt(self) -> None:
        """Wire (or unwire) host-PTT: when enabled, Guardian keys the radio on
        VARA's PTT ON/OFF, so VARA needs no COM port. Set VARA's own PTT to None."""
        self.vara.on_ptt = self._radio_ptt if self.cfg.vara_host_ptt else None

    def _make_payload_backend(self):
        # Winlink hand-off may also release the COM port (Winlink's own VARA can
        # then own it for PTT on rigs without VOX). VARA P2P keeps the COM —
        # Guardian needs rigctld to key PTT itself — so it only frees the codec.
        if self.cfg.payload_backend == "winlink_manual":
            acquire, release = self._winlink_acquire, self._winlink_release
        else:
            acquire, release = self._suspend_control_channel, self._resume_control_channel
        return make_backend(
            self.cfg.payload_backend, vara=self.vara,
            prompt=self._winlink_prompt, on_log=self.log,
            on_qsy=self._qsy_to, on_unqsy=self._qsy_restore,
            on_acquire=acquire, on_release=release,
        )

    def _suspend_control_channel(self) -> None:
        """Release the soundcard so VARA (or Winlink) can own it. No-op unless
        the control channel is the live audio modem sharing one codec."""
        t = getattr(self, "audio_transport", None)
        if t is not None:
            self._safe(t.stop)
            self.log("Control channel released — soundcard handed to VARA")

    def _resume_control_channel(self) -> None:
        """Reclaim the soundcard for the control modem after the payload phase."""
        t = getattr(self, "audio_transport", None)
        if t is not None and self.cfg.control_channel == "audio":
            try:
                t.start()
                self.log("Control channel resumed — soundcard reclaimed")
            except Exception as exc:  # noqa: BLE001
                self.log(f"Control channel resume failed: {exc}")

    # ---- Winlink hand-off: free codec (+ optionally COM) for Winlink's VARA --
    def _winlink_acquire(self) -> None:
        self._suspend_control_channel()        # free the soundcard
        if self.cfg.vara_handoff_com:
            self._release_com()                # free the COM/rigctld too

    def _winlink_release(self) -> None:
        if self.cfg.vara_handoff_com:
            self._reacquire_com()              # take the COM back first
        self._resume_control_channel()         # then reclaim the soundcard

    def _release_com(self) -> None:
        """Drop the COM port (close radio, stop our rigctld) so Winlink's VARA
        can open it for PTT. Reclaimed on operator confirmation."""
        self._safe(self.radio.close)
        self._safe(self.rigctld.stop)
        self.log("COM/rigctld released — Winlink's VARA can use the COM for PTT")
        self._refresh_radio_btn(False)

    def _reacquire_com(self) -> None:
        """Restart rigctld + reopen the radio after the Winlink transfer."""
        self.log("Reclaiming COM/rigctld after Winlink hand-off…")
        self._connect_radio()                  # ensure rigctld + open radio

    def _qsy_to(self, callsign: str) -> None:
        """Tune the radio to a station's configured frequency (VARA P2P only)."""
        if not self.cfg.auto_qsy:
            return
        fm = self.routes.freq_for(callsign)
        if not fm:
            return
        hz, mode = fm
        try:
            self._qsy_prev = self.radio.get_state().frequency_hz   # remember to restore
            self.radio.set_frequency(hz)
            if mode:
                self.radio.set_mode(mode)
            self.log(f"QSY → {callsign} on {hz/1e6:.4f} MHz {mode}".rstrip())
        except Exception as exc:  # noqa: BLE001 - VOX/None can't tune
            self.log(f"QSY skipped ({callsign}): {exc}")

    def _qsy_restore(self) -> None:
        prev = getattr(self, "_qsy_prev", None)
        if self.cfg.auto_qsy and prev:
            try:
                self.radio.set_frequency(prev)
                self.log(f"QSY restored to {prev/1e6:.4f} MHz")
            except Exception:
                pass
        self._qsy_prev = None

    def _payload_for_net(self):
        """The payload backend the live orchestrator should use — always the
        configured real backend (vara_p2p or winlink_manual). There is no
        simulation path any more."""
        return self._make_payload_backend()

    def _build_net(self, transport) -> Orchestrator:
        """Create an Orchestrator on `transport` with current mesh settings."""
        return Orchestrator(
            self.cfg.callsign, transport, routes=self.routes,
            on_event=self._on_session_event, payload=self._payload_for_net(),
            heard=self.heard, auto_route=self.cfg.auto_route, relay=self.cfg.auto_relay,
        )

    def _set_payload_backend(self, name: str) -> None:
        self.cfg.payload_backend = name
        self.net.payload = self._payload_for_net()
        self.log(f"Payload transport set to {name}")
        self._refresh_mode_buttons()
        if hasattr(self, "db_station"):
            self._refresh_station_card()

    def _winlink_prompt(self, role: str, msg, done) -> None:
        """Operator hand-off dialog for the Winlink manual backend."""
        win = ctk.CTkToplevel(self)
        win.title("Winlink hand-off")
        win.geometry("460x230")
        win.transient(self)
        win.grab_set()
        if role == "send":
            text = (f"Send this message via your Winlink session:\n\n"
                    f"  Message ID : {msg.msg_id}\n"
                    f"  To next hop: {msg.next_hop}\n"
                    f"  Final dest : {msg.final_dest}\n"
                    f"  Priority   : {msg.priority.name}\n\n"
                    f"Click 'Sent' once your Winlink transfer completes.")
            ok_label, cancel_label = "Sent", "Cancel"
        else:
            text = (f"Expect an incoming Winlink message:\n\n"
                    f"  Message ID : {msg.msg_id}\n"
                    f"  From       : {msg.source}\n"
                    f"  Final dest : {msg.final_dest}\n\n"
                    f"Click 'Received' once it arrives in your Winlink inbox.")
            ok_label, cancel_label = "Received", "Failed"

        ctk.CTkLabel(win, text=text, justify="left").pack(padx=16, pady=16, anchor="w")
        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(pady=10)

        def finish(ok: bool):
            self._safe(win.destroy)
            done(ok)

        ctk.CTkButton(btns, text=ok_label, command=lambda: finish(True)).pack(side="left", padx=8)
        ctk.CTkButton(btns, text=cancel_label, fg_color=GREY, command=lambda: finish(False)).pack(side="left", padx=8)
        win.protocol("WM_DELETE_WINDOW", lambda: finish(False))

    def _on_session_event(self, message, event: str) -> None:
        now_s = datetime.datetime.now().strftime("%H:%M:%S")
        t = self._msg_times.get(message.msg_id)
        if t is None:
            self._msg_times[message.msg_id] = [now_s, now_s]
        else:
            t[1] = now_s
        # Advance the progress strip here, on every transition — so a stage the
        # message passed through is remembered even if the next state (e.g. a
        # failure) lands before the 1 Hz render runs.
        idx_map = self._OUT_INDEX if message.direction == "out" else self._IN_INDEX
        prev = self._msg_progress.get(message.msg_id, 0)
        self._msg_progress[message.msg_id] = max(prev, idx_map.get(message.state, prev))
        self.after(0, lambda: self.log(f"[{message.source}#{message.msg_id}] {event}"))
        self.after(0, self._refresh_sessions)
        self.after(0, lambda: self._mail_track(message))

    def _mail_track(self, message) -> None:
        """Reflect session progress into the mailbox (status + inbound storage)."""
        mid = message.msg_id
        # Outbound: my queued/sending mail confirmed or failed.
        if message.direction == "out" and mid in self.mailstore._index:
            if message.state in (SessionState.DELIVERED, SessionState.CONFIRMED):
                self.mailstore.set_status(mid, status=Status.DELIVERED, folder=Folder.SENT)
            elif message.state is SessionState.FAILED:
                self.mailstore.set_status(mid, status=Status.FAILED)
        # Inbound: a real payload arrived — store it once.
        if (message.direction == "in" and message.payload_bytes
                and mid not in self._stored_inbound
                and message.state in (SessionState.RECEIVED_OK, SessionState.DELIVERED)):
            self._stored_inbound.add(mid)
            try:
                got = self.mailstore.store_incoming(message.payload_bytes, self.cfg.callsign,
                                                    via=message.source)
                self.log(f"Mail received: {got.summary()} -> {got.folder}")
            except Exception as exc:  # noqa: BLE001 - bad bundle shouldn't crash UI
                self.log(f"Could not store incoming #{mid}: {exc}")
        self._refresh_mail_list()

    def _on_channel_frame(self, who: str, frame) -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.after(0, lambda: self._append(self.channel_box, f"{ts}  {who:<8} {frame.summary()}"))

    def _refresh_sessions(self) -> None:
        if not hasattr(self, "session_panel"):
            return
        sessions = self.net.sessions
        self._sessions_empty.grid_remove() if sessions else self._sessions_empty.grid()

        # Build a card the first time we see a message; thereafter just update it
        # in place (no widget churn per tick — see the typing-lag note in memory).
        new_mid = False
        for mid, msg in sessions.items():
            if mid not in self._session_cards:
                self._build_session_card(mid, msg)
                new_mid = True
            self._update_session_card(mid, msg)

        # Re-order only when the membership changed: newest message on top.
        if new_mid:
            for row, mid in enumerate(sorted(self._session_cards, reverse=True)):
                self._session_cards[mid]["frame"].grid(row=row + 1, column=0,
                                                        padx=4, pady=4, sticky="ew")

        live = sum(1 for m in sessions.values() if not m.state.terminal)
        done = sum(1 for m in sessions.values() if m.state is SessionState.DELIVERED)
        fail = sum(1 for m in sessions.values()
                   if m.state in (SessionState.FAILED, SessionState.CANCELLED))
        bits = []
        if live:
            bits.append(f"{live} in progress")
        if done:
            bits.append(f"{done} delivered")
        if fail:
            bits.append(f"{fail} failed")
        self.sessions_summary.configure(text=" · ".join(bits))

    def _build_session_card(self, mid: int, msg) -> None:
        card = ctk.CTkFrame(self.session_panel)
        card.grid_columnconfigure(0, weight=1)
        head = ctk.CTkLabel(card, anchor="w", font=ctk.CTkFont(size=13, weight="bold"))
        head.grid(row=0, column=0, padx=10, pady=(7, 0), sticky="ew")

        strip = ctk.CTkFrame(card, fg_color="transparent")
        strip.grid(row=1, column=0, padx=8, pady=(3, 0), sticky="w")
        stages = self._OUT_STAGES if msg.direction == "out" else self._IN_STAGES
        stage_lbls = []
        for i, name in enumerate(stages):
            if i:
                ctk.CTkLabel(strip, text="›", text_color=GREY,
                             font=ctk.CTkFont(size=12)).pack(side="left", padx=1)
            lbl = ctk.CTkLabel(strip, text=f"○ {name}", text_color=GREY,
                               font=ctk.CTkFont(size=12))
            lbl.pack(side="left", padx=2)
            stage_lbls.append(lbl)

        note = ctk.CTkLabel(card, anchor="w", text_color=GREY, font=ctk.CTkFont(size=11))
        note.grid(row=2, column=0, padx=10, pady=(2, 7), sticky="ew")
        self._session_cards[mid] = {"frame": card, "head": head,
                                    "stages": stage_lbls, "note": note}

    def _update_session_card(self, mid: int, msg) -> None:
        card = self._session_cards[mid]
        stages, reached, status = self._milestone(msg)
        arrow = "→" if msg.direction == "out" else "←"
        via = f"  via {msg.next_hop}" if (msg.next_hop and msg.next_hop != msg.final_dest) else ""
        card["head"].configure(
            text=f"#{mid}  {msg.source} {arrow} {msg.final_dest}{via}   [{msg.priority.name}]")
        for i, lbl in enumerate(card["stages"]):
            name = stages[i]
            if i < reached:
                lbl.configure(text=f"✓ {name}", text_color=GREEN)
            elif i == reached:
                if status == "done":
                    lbl.configure(text=f"✓ {name}", text_color=GREEN)
                elif status == "failed":
                    lbl.configure(text=f"✗ {name}", text_color=RED)
                else:
                    lbl.configure(text=f"● {name}", text_color=AMBER)
            else:
                lbl.configure(text=f"○ {name}", text_color=GREY)
        times = self._msg_times.get(mid)
        when = f"  ·  started {times[0]}, updated {times[1]}" if times else ""
        if status == "failed" and msg.error:
            card["note"].configure(text=f"⚠ {msg.error}{when}", text_color=RED)
        else:
            card["note"].configure(text=f"{msg.state.value}{when}", text_color=GREY)

    # ---- Mail tab (Winlink-like mailbox) ------------------------------ #
    _FOLDERS = [("Inbox", Folder.INBOX), ("Outbox", Folder.OUTBOX),
                ("Sent", Folder.SENT), ("Transit", Folder.TRANSIT)]

    def _build_mail_tab(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        side = ctk.CTkFrame(tab, width=170)
        side.grid(row=0, column=0, padx=(10, 6), pady=10, sticky="ns")
        ctk.CTkButton(side, text="✎  Compose", command=self._compose_mail).pack(
            fill="x", padx=10, pady=(12, 8))
        self.mail_folder_btns = {}
        for label, key in self._FOLDERS:
            b = ctk.CTkButton(side, text=label, anchor="w", fg_color="transparent",
                              command=lambda k=key: self._select_folder(k))
            b.pack(fill="x", padx=10, pady=2)
            self.mail_folder_btns[key] = b
        ctk.CTkButton(side, text="Refresh", fg_color=GREY, command=self._refresh_mail_list).pack(
            fill="x", padx=10, pady=(12, 4))

        right = ctk.CTkFrame(tab, fg_color="transparent")
        right.grid(row=0, column=1, padx=(6, 10), pady=10, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)
        right.grid_rowconfigure(2, weight=1)

        self.mail_title = ctk.CTkLabel(right, text="Inbox", font=ctk.CTkFont(size=16, weight="bold"))
        self.mail_title.grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.mail_list = ctk.CTkScrollableFrame(right, label_text="Messages")
        self.mail_list.grid(row=1, column=0, sticky="nsew", pady=(0, 6))
        self.mail_list.grid_columnconfigure(0, weight=1)

        reader = ctk.CTkFrame(right)
        reader.grid(row=2, column=0, sticky="nsew")
        reader.grid_columnconfigure(0, weight=1)
        reader.grid_rowconfigure(0, weight=1)
        self.mail_reader = ctk.CTkTextbox(reader, font=ctk.CTkFont(family="Consolas", size=12))
        self.mail_reader.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))
        self.mail_atts = ctk.CTkFrame(reader, fg_color="transparent")
        self.mail_atts.grid(row=1, column=0, sticky="ew", padx=8)
        self.mail_actions = ctk.CTkFrame(reader, fg_color="transparent")
        self.mail_actions.grid(row=2, column=0, sticky="ew", padx=8, pady=6)

        self._select_folder(Folder.INBOX)

    def _select_folder(self, folder: str) -> None:
        self.mail_folder = folder
        for key, b in self.mail_folder_btns.items():
            b.configure(fg_color=("#3B8ED0" if key == folder else "transparent"))
        self._refresh_mail_list()

    def _refresh_mail_list(self) -> None:
        if not hasattr(self, "mail_list"):
            return
        counts = self.mailstore.counts()
        for label, key in self._FOLDERS:
            unread = self.mailstore.unread(key)
            badge = f" ({counts.get(key, 0)}" + (f", {unread} new)" if unread else ")")
            self.mail_folder_btns[key].configure(text=f"{label}{badge}")
        name = next((l for l, k in self._FOLDERS if k == self.mail_folder), self.mail_folder)
        self.mail_title.configure(text=name)
        for w in self.mail_list.winfo_children():
            w.destroy()
        rows = self.mailstore.list(self.mail_folder)
        if not rows:
            ctk.CTkLabel(self.mail_list, text="(empty)", text_color=GREY).grid(row=0, column=0, sticky="w", padx=6, pady=6)
            return
        for i, m in enumerate(rows):
            via = f" via {m['next_hop']}" if m.get("next_hop") else ""
            att = f" 📎{m['att']}" if m.get("att") else ""
            dot = "● " if not m.get("read", True) else ""
            txt = (f"{dot}#{m['msg_id']}  {m['source']}→{m['final_dest']}{via}\n"
                   f"    {m.get('subject') or '(no subject)'}  ·  {m['status']}{att}  ·  {m['size']}B")
            sel = (m["msg_id"] == self.mail_selected)
            ctk.CTkButton(self.mail_list, text=txt, anchor="w", height=44,
                          fg_color=("#2A4D69" if sel else "transparent"),
                          command=lambda mid=m["msg_id"]: self._open_mail(mid)).grid(
                row=i, column=0, sticky="ew", pady=1)

    def _open_mail(self, msg_id: int) -> None:
        self.mail_selected = msg_id
        self.mailstore.mark_read(msg_id)
        mail = self.mailstore.get(msg_id)
        self._refresh_mail_list()
        for w in self.mail_atts.winfo_children():
            w.destroy()
        for w in self.mail_actions.winfo_children():
            w.destroy()
        self.mail_reader.delete("1.0", "end")
        if mail is None:
            self.mail_reader.insert("end", "(message not found)")
            return
        prio = Priority(mail.priority).name if mail.priority in Priority._value2member_map_ else str(mail.priority)
        path = " → ".join(mail.hops) if mail.hops else "-"
        self.mail_reader.insert("end",
            f"From:     {mail.source}\nTo:       {mail.final_dest}\n"
            f"Subject:  {mail.subject}\nPriority: {prio}\nRoute:    {path}\n"
            f"Status:   {mail.status}\n{'-'*48}\n{mail.body}\n")
        if mail.attachments:
            ctk.CTkLabel(self.mail_atts, text="Attachments:").pack(side="left", padx=(0, 6))
            for a in mail.attachments:
                ctk.CTkButton(self.mail_atts, text=f"💾 {a.name} ({a.size}B)", height=26,
                              command=lambda att=a: self._save_attachment(att)).pack(side="left", padx=4)
                ctk.CTkButton(self.mail_atts, text="Open", width=50, height=26, fg_color=GREY,
                              command=lambda att=a: self._open_attachment(att)).pack(side="left", padx=(0, 8))
        # Folder-specific actions.
        if mail.folder == Folder.INBOX:
            ctk.CTkButton(self.mail_actions, text="↩ Reply", command=lambda: self._compose_mail(reply_to=mail)).pack(side="left", padx=4)
        if mail.folder == Folder.TRANSIT:
            ctk.CTkButton(self.mail_actions, text="Deliver now", command=lambda: self._deliver_transit(msg_id)).pack(side="left", padx=4)
        if mail.folder in (Folder.OUTBOX,):
            ctk.CTkButton(self.mail_actions, text="Send now", command=lambda: self._send_mail(mail)).pack(side="left", padx=4)
        ctk.CTkButton(self.mail_actions, text="Simulate receive (demo)", fg_color=AMBER,
                      command=lambda: self._demo_receive(msg_id)).pack(side="left", padx=4)
        ctk.CTkButton(self.mail_actions, text="Delete", fg_color=RED,
                      command=lambda: self._delete_mail(msg_id)).pack(side="left", padx=4)

    def _save_attachment(self, att: Attachment) -> None:
        path = filedialog.asksaveasfilename(initialfile=att.name, title="Save attachment")
        if path:
            try:
                with open(path, "wb") as fh:
                    fh.write(att.data)
                self.log(f"Saved attachment to {path}")
            except OSError as exc:
                self.log(f"Save failed: {exc}")

    def _open_attachment(self, att: Attachment) -> None:
        try:
            p = os.path.join(tempfile.gettempdir(), att.name)
            with open(p, "wb") as fh:
                fh.write(att.data)
            os.startfile(p)  # noqa: S606 - user-initiated open of their own attachment
        except Exception as exc:  # noqa: BLE001
            self.log(f"Open failed: {exc}")

    def _delete_mail(self, msg_id: int) -> None:
        self.mailstore.delete(msg_id)
        if self.mail_selected == msg_id:
            self.mail_selected = None
            self.mail_reader.delete("1.0", "end")
            for w in self.mail_atts.winfo_children():
                w.destroy()
            for w in self.mail_actions.winfo_children():
                w.destroy()
        self._refresh_mail_list()
        self.log(f"Deleted message #{msg_id}")

    def _demo_receive(self, msg_id: int) -> None:
        mail = self.mailstore.get(msg_id)
        if mail is None:
            return
        got = self.mailstore.store_incoming(mail.to_bundle(), self.cfg.callsign, via=mail.source)
        self.log(f"(demo) stored incoming #{got.msg_id} into {got.folder}")
        self._select_folder(got.folder)

    def _compose_mail(self, reply_to: MailMessage | None = None) -> None:
        win = ctk.CTkToplevel(self)
        win.title("Compose message")
        win.geometry("600x600")
        win.transient(self)
        win.grid_columnconfigure(0, weight=1)
        win.grid_rowconfigure(2, weight=1)
        atts: list[Attachment] = []
        widgets: dict[str, object] = {}   # form field key -> widget

        # Header: To + Template + Priority.
        head = ctk.CTkFrame(win)
        head.grid(row=0, column=0, padx=10, pady=(10, 4), sticky="ew")
        head.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(head, text="To (final dest)").grid(row=0, column=0, padx=8, pady=6, sticky="w")
        to = ctk.CTkEntry(head, placeholder_text="OK1CCC")
        to.grid(row=0, column=1, padx=8, pady=6, sticky="ew")
        ctk.CTkLabel(head, text="Template").grid(row=1, column=0, padx=8, pady=6, sticky="w")
        tpl = ctk.CTkOptionMenu(head, values=form_names(), width=150)
        tpl.set("Plain")
        tpl.grid(row=1, column=1, padx=8, pady=6, sticky="w")
        prio = ctk.CTkOptionMenu(head, values=[p.name for p in Priority], width=130)
        prio.set(Priority.ROUTINE.name)
        prio.grid(row=1, column=2, padx=8, pady=6)

        content = ctk.CTkScrollableFrame(win, label_text="Message")
        content.grid(row=2, column=0, padx=10, pady=4, sticky="nsew")
        content.grid_columnconfigure(0, weight=1)

        def render(_=None):
            for w in content.winfo_children():
                w.destroy()
            widgets.clear()
            name = tpl.get()
            if name == "Plain":
                ctk.CTkLabel(content, text="Subject").grid(row=0, column=0, sticky="w", padx=6, pady=(6, 0))
                s = ctk.CTkEntry(content)
                s.grid(row=1, column=0, sticky="ew", padx=6)
                widgets["_subject"] = s
                b = ctk.CTkTextbox(content, height=240)
                b.grid(row=2, column=0, sticky="nsew", padx=6, pady=6)
                content.grid_rowconfigure(2, weight=1)
                widgets["_body"] = b
                return
            form = FORMS[name]
            r = 0
            for f in form.fields:
                ctk.CTkLabel(content, text=f.label).grid(row=r, column=0, sticky="w", padx=6, pady=(6, 0))
                r += 1
                if f.multiline:
                    w = ctk.CTkTextbox(content, height=90)
                else:
                    w = ctk.CTkEntry(content)
                w.grid(row=r, column=0, sticky="ew", padx=6)
                widgets[f.key] = w
                r += 1

        tpl.configure(command=render)
        render()

        att_lbl = ctk.CTkLabel(win, text="No attachments", text_color=GREY)
        att_lbl.grid(row=3, column=0, padx=10, pady=2, sticky="w")

        def add_att():
            for p in filedialog.askopenfilenames(title="Attach files"):
                try:
                    with open(p, "rb") as fh:
                        atts.append(Attachment(os.path.basename(p), fh.read()))
                except OSError as exc:
                    self.log(f"Attach failed: {exc}")
            total = sum(a.size for a in atts)
            warn = "  ⚠ large for RF" if total > 50_000 else ""
            att_lbl.configure(text=f"{len(atts)} file(s), {total} B{warn}")

        def _val(w) -> str:
            return w.get("1.0", "end").rstrip("\n") if isinstance(w, ctk.CTkTextbox) else w.get().strip()

        def finish(send: bool):
            dest = to.get().strip().upper()
            if send and not dest:
                self.log("Compose: enter a destination")
                return
            name = tpl.get()
            if name == "Plain":
                subject = _val(widgets["_subject"])
                body = _val(widgets["_body"])
            else:
                form = FORMS[name]
                values = {k: _val(w) for k, w in widgets.items()}
                body = form.render(values)
                subject = form.subject(values) or form.name
            mail = MailMessage(
                msg_id=self.mailstore.next_id(self.cfg.callsign), source=self.cfg.callsign,
                final_dest=dest, subject=subject, body=body, attachments=list(atts),
                priority=Priority[prio.get()].value, created=time.time(),
                hops=[self.cfg.callsign],
                folder=Folder.OUTBOX if send else Folder.DRAFT,
                status=Status.QUEUED if send else Status.DRAFT,
            )
            self.mailstore.add(mail)
            self._safe(win.destroy)
            if send:
                self._send_mail(mail)
            self._select_folder(mail.folder)

        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.grid(row=4, column=0, padx=10, pady=10, sticky="e")
        ctk.CTkButton(bar, text="📎 Attach", fg_color=GREY, command=add_att).pack(side="left", padx=6)
        ctk.CTkButton(bar, text="Send", command=lambda: finish(True)).pack(side="left", padx=6)
        ctk.CTkButton(bar, text="Save draft", fg_color=GREY, command=lambda: finish(False)).pack(side="left", padx=6)
        ctk.CTkButton(bar, text="Cancel", fg_color=GREY, command=win.destroy).pack(side="left", padx=6)

        # Reply prefill (plain template).
        if reply_to is not None:
            to.insert(0, reply_to.source)
            widgets["_subject"].insert(0, "Re: " + reply_to.subject)
            quoted = "\n".join("> " + ln for ln in reply_to.body.splitlines())
            widgets["_body"].insert("1.0", f"\n\n--- {reply_to.source} wrote ---\n{quoted}\n")
            self.mail_selected = reply_to.msg_id

    def _send_mail(self, mail: MailMessage) -> None:
        self.mailstore.set_status(mail.msg_id, status=Status.SENDING)
        self.net.send_message(
            final_dest=mail.final_dest, body=mail.subject, msg_id=mail.msg_id,
            priority=Priority(mail.priority), payload_bytes=mail.to_bundle(),
        )
        est = mail.est_seconds()
        self.log(f"Mail #{mail.msg_id} -> {mail.final_dest} queued ({mail.content_size()}B, ~{est}s on-air)")
        self._refresh_mail_list()

    def _deliver_transit(self, msg_id: int) -> None:
        mail = self.mailstore.get(msg_id)
        if mail is None:
            return
        self.mailstore.set_status(msg_id, status=Status.SENDING)
        self.net.send_message(
            final_dest=mail.final_dest, body=mail.subject, msg_id=mail.msg_id,
            priority=Priority(mail.priority), payload_bytes=mail.to_bundle(),
        )
        self.log(f"Forwarding transit #{msg_id} toward {mail.final_dest}")
        self._refresh_mail_list()

    # ---- Mesh tab (smart routing + scanning) -------------------------- #
    def _build_mesh_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(tab, fg_color="transparent")
        hdr.grid(row=0, column=0, padx=14, pady=(10, 0), sticky="ew")
        ctk.CTkLabel(hdr, text="Stations heard on the net",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        self.heard_summary = ctk.CTkLabel(hdr, text="", text_color=GREY)
        self.heard_summary.pack(side="left", padx=10)
        ctk.CTkLabel(tab, text="● heard <2 min   ● <10 min   ● older  ·  "
                     "click a station to use it as your next hop",
                     text_color=GREY, font=ctk.CTkFont(size=11)).grid(
            row=1, column=0, padx=14, pady=(0, 2), sticky="w")
        self.heard_panel = ctk.CTkScrollableFrame(tab)
        self.heard_panel.grid(row=2, column=0, padx=10, pady=(2, 8), sticky="nsew")
        self.heard_panel.grid_columnconfigure(0, weight=1)
        self._heard_empty = ctk.CTkLabel(self.heard_panel, text="(nothing heard yet — "
                                         "stations appear here when their control bursts arrive)",
                                         text_color=GREY)
        self._heard_empty.grid(row=0, column=0, padx=8, pady=8, sticky="w")
        tab.grid_rowconfigure(2, weight=1)

        # Channel scanning.
        scan = ctk.CTkFrame(tab)
        scan.grid(row=3, column=0, padx=10, pady=8, sticky="nsew")
        scan.grid_columnconfigure(6, weight=1)
        scan.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(scan, text="Channel scanning", font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, columnspan=7, padx=10, pady=(8, 4), sticky="w")
        self.ch_name = ctk.CTkEntry(scan, placeholder_text="Name", width=120)
        self.ch_name.grid(row=1, column=0, padx=4, pady=4)
        self.ch_freq = ctk.CTkEntry(scan, placeholder_text="MHz e.g. 145.500", width=120)
        self.ch_freq.grid(row=1, column=1, padx=4, pady=4)
        self.ch_mode = ctk.CTkOptionMenu(scan, values=["FM", "USB", "LSB", "DATA"], width=80)
        self.ch_mode.set("FM")
        self.ch_mode.grid(row=1, column=2, padx=4, pady=4)
        ctk.CTkButton(scan, text="Add", width=60, command=self._add_channel).grid(row=1, column=3, padx=4)
        ctk.CTkButton(scan, text="Remove", width=70, fg_color=GREY, command=self._remove_channel).grid(row=1, column=4, padx=4)
        ctk.CTkButton(scan, text="Start scan", width=90, command=self._start_scan).grid(row=1, column=5, padx=4)
        ctk.CTkButton(scan, text="Stop", width=60, fg_color=GREY, command=self._stop_scan).grid(row=1, column=6, padx=4, sticky="w")
        self.channels_box = ctk.CTkTextbox(scan, font=ctk.CTkFont(family="Consolas", size=12))
        self.channels_box.grid(row=2, column=0, columnspan=7, padx=8, pady=8, sticky="nsew")
        self._refresh_channels()

    def _apply_mesh_opts(self) -> None:
        self.cfg.auto_route = bool(self.auto_route_chk.get())
        self.cfg.auto_relay = bool(self.auto_relay_chk.get())
        self.cfg.auto_deliver = bool(self.auto_deliver_chk.get())
        self.cfg.beacon_enabled = bool(self.beacon_chk.get())
        self.net.auto_route = self.cfg.auto_route
        self.net.relay = self.cfg.auto_relay
        self.cfg.save()
        self.log(f"Mesh: auto_route={self.cfg.auto_route} auto_relay={self.cfg.auto_relay} "
                 f"auto_deliver={self.cfg.auto_deliver} beacon={self.cfg.beacon_enabled}")

    def _refresh_heard(self) -> None:
        if not hasattr(self, "heard_panel"):
            return
        now = time.monotonic()
        self.heard.prune(now)            # always prune (cheap, keeps table honest)
        stations = self.heard.active(now)
        present = {s.callsign for s in stations}

        # Drop rows for stations that have aged out.
        for call in list(self._heard_rows):
            if call not in present:
                self._heard_rows.pop(call)["frame"].destroy()

        self._heard_empty.grid() if not stations else self._heard_empty.grid_remove()

        added = False
        for s in stations:
            if s.callsign not in self._heard_rows:
                self._build_heard_row(s.callsign)
                added = True
            self._update_heard_row(s, now)

        # Order freshest-first. Rows are only re-gridded (not re-created), so this
        # is cheap; do it whenever membership changed.
        if added:
            order = [s.callsign for s in stations]   # already freshest-first
            for row, call in enumerate(order):
                self._heard_rows[call]["frame"].grid(row=row + 1, column=0,
                                                      padx=4, pady=3, sticky="ew")

        relays = [s for s in stations if s.reaches]
        bits = [f"{len(stations)} heard"]
        if relays:
            bits.append(f"{len(relays)} can relay")
        self.heard_summary.configure(text=" · ".join(bits))

        # Mirror a compact summary into the sidebar, visible from any tab.
        if hasattr(self, "lbl_netcount"):
            if not stations:
                self.lbl_netcount.configure(text="Net: quiet")
            else:
                hint = f"\nRelay via {relays[0].callsign}" if relays else ""
                self.lbl_netcount.configure(text=f"Net: {len(stations)} heard{hint}")

    def _build_heard_row(self, call: str) -> None:
        row = ctk.CTkFrame(self.heard_panel)
        row.grid_columnconfigure(2, weight=1)
        dot = ctk.CTkLabel(row, text="●", width=16, font=ctk.CTkFont(size=15))
        dot.grid(row=0, column=0, padx=(8, 2), pady=5)
        name = ctk.CTkLabel(row, text=call, width=80, anchor="w",
                            font=ctk.CTkFont(size=13, weight="bold"))
        name.grid(row=0, column=1, padx=2, pady=5, sticky="w")
        meta = ctk.CTkLabel(row, text="", anchor="w", text_color=GREY,
                            font=ctk.CTkFont(size=11))
        meta.grid(row=0, column=2, padx=6, pady=5, sticky="w")
        reaches = ctk.CTkLabel(row, text="", anchor="e", text_color=GREEN,
                               font=ctk.CTkFont(size=11))
        reaches.grid(row=0, column=3, padx=(6, 10), pady=5, sticky="e")
        # The whole row is a click target: use this station as the next hop.
        for w in (row, dot, name, meta, reaches):
            w.configure(cursor="hand2")
            w.bind("<Button-1>", lambda _e, c=call: self._use_as_next_hop(c))
        self._heard_rows[call] = {"frame": row, "dot": dot, "meta": meta, "reaches": reaches}

    def _use_as_next_hop(self, call: str) -> None:
        """Click a heard station to drop it into the Net composer's next-hop."""
        if hasattr(self, "n_next"):
            self.n_next.delete(0, "end")
            self.n_next.insert(0, call)
        self.tabs.set("Net")
        if hasattr(self, "n_final"):
            self.n_final.focus_set()
        self.log(f"Net: next hop set to {call} — add a destination + message, then Send over net")

    def _update_heard_row(self, s, now: float) -> None:
        row = self._heard_rows[s.callsign]
        age = s.age(now)
        row["dot"].configure(text_color=self._freshness(age))
        snr = f"  ·  SNR {s.last_snr:.0f} dB" if s.last_snr is not None else ""
        last = f"  ·  {s.last_frame}" if s.last_frame else ""
        row["meta"].configure(text=f"heard {self._fmt_age(age)}  ·  ×{s.count}{snr}{last}")
        row["reaches"].configure(text=("→ " + ", ".join(sorted(s.reaches))) if s.reaches else "")

    def _refresh_channels(self) -> None:
        self.channels_box.delete("1.0", "end")
        cur = self.scanner.current.name if (self.scanner.enabled and self.scanner.current) else None
        if not len(self.channel_plan):
            self.channels_box.insert("end", "(no channels — add one above)\n")
        for c in self.channel_plan.channels:
            mark = " <= scanning" if c.name == cur else ""
            self.channels_box.insert("end", f"{c.name:<16}{c.freq_hz/1e6:>11.4f} MHz  {c.mode}{mark}\n")

    def _add_channel(self) -> None:
        name = self.ch_name.get().strip()
        try:
            hz = int(float(self.ch_freq.get().strip()) * 1_000_000)
        except ValueError:
            self.log("Channel: enter frequency in MHz, e.g. 145.500")
            return
        if not name:
            self.log("Channel: enter a name")
            return
        self.channel_plan.add(Channel(name, hz, self.ch_mode.get()))
        self.channel_plan.save()
        self._refresh_channels()
        self.log(f"Channel added: {name} {hz/1e6:.4f} MHz {self.ch_mode.get()}")

    def _remove_channel(self) -> None:
        name = self.ch_name.get().strip()
        self.channel_plan.remove(name)
        self.channel_plan.save()
        self._refresh_channels()
        self.log(f"Channel removed: {name}")

    def _start_scan(self) -> None:
        if not self.radio.is_open:
            self.log("Scan: connect the radio first (Radio tab)")
            return
        self.scanner.dwell = self.cfg.scan_dwell
        self.scanner.start(time.monotonic())
        self.log("Channel scan started")

    def _stop_scan(self) -> None:
        self.scanner.stop()
        self._refresh_channels()
        self.log("Channel scan stopped")

    # ---- Messages tab ------------------------------------------------- #
    def _build_messages_tab(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(tab, text="Compose control burst", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, columnspan=2, padx=14, pady=(12, 8), sticky="w"
        )

        ctk.CTkLabel(tab, text="Frame type").grid(row=1, column=0, padx=14, pady=6, sticky="w")
        self.m_type = ctk.CTkOptionMenu(tab, values=[t.name for t in FrameType])
        self.m_type.set(FrameType.HAVE_MSG.name)
        self.m_type.grid(row=1, column=1, padx=14, pady=6, sticky="ew")

        self.m_final = self._mfield(tab, 2, "Final destination", "OK1CCC")
        self.m_next = self._mfield(tab, 3, "Next hop (blank = auto from routes)", "OK1DDD")
        self.m_id = self._mfield(tab, 4, "Message ID", "1001")

        ctk.CTkLabel(tab, text="Priority").grid(row=5, column=0, padx=14, pady=6, sticky="w")
        self.m_prio = ctk.CTkOptionMenu(tab, values=[p.name for p in Priority])
        self.m_prio.set(Priority.ROUTINE.name)
        self.m_prio.grid(row=5, column=1, padx=14, pady=6, sticky="ew")

        self.m_ack = ctk.CTkCheckBox(tab, text="ACK required")
        self.m_ack.grid(row=6, column=1, padx=14, pady=6, sticky="w")

        ctk.CTkButton(tab, text="Build burst", command=self._build_burst).grid(row=7, column=0, padx=14, pady=12, sticky="w")
        ctk.CTkButton(tab, text="Build + decode (self-test)", fg_color=AMBER, command=self._roundtrip_burst).grid(row=7, column=1, padx=14, pady=12, sticky="w")

        self.m_out = ctk.CTkTextbox(tab, height=180, font=ctk.CTkFont(family="Consolas", size=12))
        self.m_out.grid(row=8, column=0, columnspan=2, padx=14, pady=(0, 14), sticky="nsew")
        tab.grid_rowconfigure(8, weight=1)

    # ---- Log tab ------------------------------------------------------ #
    def _build_log_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        self.log_box = ctk.CTkTextbox(tab, font=ctk.CTkFont(family="Consolas", size=12))
        self.log_box.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")

    # ------------------------------------------------------------------ #
    #  Small helpers for form fields bound to config                      #
    # ------------------------------------------------------------------ #
    def _field(self, tab, row: int, label: str, attr: str):
        ctk.CTkLabel(tab, text=label).grid(row=row, column=0, padx=14, pady=8, sticky="w")
        entry = ctk.CTkEntry(tab)
        entry.insert(0, str(getattr(self.cfg, attr)))
        entry.grid(row=row, column=1, padx=14, pady=8, sticky="ew")
        if not hasattr(self, "_entries"):
            self._entries = {}
        self._entries[attr] = entry
        return entry

    def _set_entry(self, attr: str, value: str) -> None:
        """Replace the text of a config-bound entry created by _field()."""
        entry = getattr(self, "_entries", {}).get(attr)
        if entry is not None:
            entry.delete(0, "end")
            entry.insert(0, value)

    def _mfield(self, tab, row: int, label: str, placeholder: str):
        ctk.CTkLabel(tab, text=label).grid(row=row, column=0, padx=14, pady=6, sticky="w")
        entry = ctk.CTkEntry(tab, placeholder_text=placeholder)
        entry.grid(row=row, column=1, padx=14, pady=6, sticky="ew")
        return entry

    # ------------------------------------------------------------------ #
    #  Actions                                                            #
    # ------------------------------------------------------------------ #
    def _save_config(self) -> None:
        for attr, entry in getattr(self, "_entries", {}).items():
            raw = entry.get().strip()
            current = getattr(self.cfg, attr)
            if isinstance(current, int):
                try:
                    setattr(self.cfg, attr, int(raw or 0))
                except ValueError:
                    self.log(f"Invalid number for {attr!r}: {raw!r}")
            else:
                setattr(self.cfg, attr, raw)
        self.cfg.radio_backend = self.backend_menu.get()
        self.cfg.ptt_line = self.ptt_menu.get()
        self.cfg.cat_port = self._selected_cat_port()
        if hasattr(self, "vara_hostptt_chk"):
            self.cfg.vara_host_ptt = bool(self.vara_hostptt_chk.get())
        if hasattr(self, "vara_handoffcom_chk"):
            self.cfg.vara_handoff_com = bool(self.vara_handoffcom_chk.get())
        self.cfg.appearance = self.appearance_menu.get()
        self.cfg.vara_mode = self.vara_mode_seg.get()
        self.cfg.control_channel = self.channel_seg.get()
        self.cfg.audio_input = "" if self.audio_in_menu.get() == "(default)" else self.audio_in_menu.get()
        self.cfg.audio_output = "" if self.audio_out_menu.get() == "(default)" else self.audio_out_menu.get()
        self.cfg.remember_vara_ports()
        path = self.cfg.save()
        # Rebuild driver/VARA in case endpoints changed.
        self.radio = make_driver(self.cfg)
        self.rigctld = RigctldProcess(self.cfg.rigctld_path)
        self.vara = VaraClient(self.cfg.vara_host, self.cfg.vara_cmd_port, self.cfg.vara_data_port)
        self.vara.on_notification = self._on_vara_notification
        self._apply_vara_host_ptt()
        self.net.callsign = self.cfg.callsign.strip().upper()
        self.net.payload = self._payload_for_net()  # uses the rebuilt VARA client
        self.lbl_call.configure(text=self.cfg.callsign)
        self._refresh_station_card()
        self.log(f"Configuration saved to {path}")

    def _apply_preset(self, label: str) -> None:
        preset = next((p for p in CURATED if p.label == label), None)
        if preset is None:
            return
        self.backend_menu.set(preset.backend)
        self._set_entry("rig_model", str(preset.rig_model))
        # Fill a clean model name only for concrete radios.
        name = preset.label if preset.rig_model > 1 else ""
        self._set_entry("radio", name)
        if preset.backend == "hamlib" and preset.rig_model == 0:
            self.log("Use 'Browse all…' to choose the exact radio and fill its Hamlib id")
        else:
            self.log(f"Preset applied: {preset.label} (backend {preset.backend}, model {preset.rig_model})")

    def _browse_radios(self) -> None:
        """Searchable picker populated from the installed Hamlib (`rigctl -l`)."""
        win = ctk.CTkToplevel(self)
        win.title("Select radio (Hamlib)")
        win.geometry("460x520")
        win.transient(self)
        win.grid_columnconfigure(0, weight=1)
        win.grid_rowconfigure(2, weight=1)

        search = ctk.CTkEntry(win, placeholder_text="Type to filter, e.g. 7300 / Yaesu / Kenwood")
        search.grid(row=0, column=0, padx=12, pady=12, sticky="ew")
        status = ctk.CTkLabel(win, text="Loading Hamlib model list…", text_color=GREY)
        status.grid(row=1, column=0, padx=12, sticky="w")
        listbox = ctk.CTkScrollableFrame(win)
        listbox.grid(row=2, column=0, padx=12, pady=12, sticky="nsew")
        listbox.grid_columnconfigure(0, weight=1)

        def choose(model_id: int, label: str):
            self.backend_menu.set("hamlib")
            self._set_entry("radio", label)
            self._set_entry("rig_model", str(model_id))
            self.log(f"Selected {label} (Hamlib model {model_id})")
            win.destroy()

        def render(models):
            for w in listbox.winfo_children():
                w.destroy()
            q = search.get().strip().lower()
            shown = [m for m in models if q in f"{m.model_id} {m.label}".lower()][:300]
            status.configure(text=f"{len(shown)} shown of {len(models)} radios")
            for i, m in enumerate(shown):
                ctk.CTkButton(
                    listbox, text=f"{m.label}   (#{m.model_id}, {m.status})",
                    anchor="w", fg_color="transparent", hover_color=("#d0d0d0", "#333333"),
                    command=lambda mm=m: choose(mm.model_id, mm.label),
                ).grid(row=i, column=0, sticky="ew", pady=1)

        def load():
            models = load_hamlib_models(self.cfg.rigctld_path)
            if not models:
                self.after(0, lambda: status.configure(
                    text="Hamlib (rigctl) not found. Install Hamlib, then retry."))
                return
            models.sort(key=lambda m: m.label.lower())
            self._hamlib_models = models
            self.after(0, lambda: render(models))

        search.bind("<KeyRelease>", lambda _e: render(getattr(self, "_hamlib_models", [])))
        threading.Thread(target=load, daemon=True).start()

    def _refresh_hamlib_status(self) -> None:
        path = hamlib_installer.existing_rigctld(self.cfg.rigctld_path)
        if path:
            self.hamlib_status.configure(text=f"Hamlib OK: {path}", text_color=GREEN)
        else:
            self.hamlib_status.configure(text="Hamlib not found — click Install to fetch it.", text_color=AMBER)

    def _install_hamlib(self) -> None:
        self.log("Hamlib install requested…")

        def worker():
            try:
                path = hamlib_installer.install(progress=lambda m: self.after(0, lambda: self.log(m)))
                self.cfg.rigctld_path = path
                self.cfg.save()
                self.rigctld = RigctldProcess(self.cfg.rigctld_path)
                if self.backend_menu.get() == "none":
                    self.after(0, lambda: self.backend_menu.set("hamlib"))
                self.after(0, self._refresh_hamlib_status)
                self.after(0, lambda: self.log("Hamlib install complete."))
            except Exception as exc:  # noqa: BLE001 - surface any failure in the log
                self.after(0, lambda: self.log(f"Hamlib install failed: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _detect_usb(self) -> None:
        """Popup listing COM ports, their USB-serial chipset, and driver links."""
        adapters = detect_usb_serial()
        win = ctk.CTkToplevel(self)
        win.title("USB / serial adapters")
        win.geometry("560x420")
        win.transient(self)
        win.grid_columnconfigure(0, weight=1)
        win.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            win, justify="left", text=(
                "VOX/serial PTT needs no extra install (pyserial is bundled).\n"
                "If a port's chipset shows 'driver needed', use its official link."),
        ).grid(row=0, column=0, padx=14, pady=(12, 6), sticky="w")

        body = ctk.CTkScrollableFrame(win)
        body.grid(row=1, column=0, padx=12, pady=(0, 12), sticky="nsew")
        body.grid_columnconfigure(0, weight=1)

        if not adapters:
            ctk.CTkLabel(body, text="No COM ports detected. Plug in the radio interface and retry.",
                         text_color=AMBER).grid(row=0, column=0, padx=8, pady=8, sticky="w")
        for i, a in enumerate(adapters):
            card = ctk.CTkFrame(body)
            card.grid(row=i, column=0, padx=4, pady=4, sticky="ew")
            card.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(card, justify="left", anchor="w", text=(
                f"{a.device}   {a.description}\n"
                f"Chipset: {a.chipset}   (USB {a.vidpid})\n{a.note}"),
            ).grid(row=0, column=0, padx=10, pady=8, sticky="w")
            if a.driver_url:
                ctk.CTkButton(card, text="Open driver page", width=130,
                              command=lambda u=a.driver_url: webbrowser.open(u)).grid(
                    row=0, column=1, padx=10, pady=8)
            self.log(f"USB: {a.device} {a.chipset} ({a.vidpid})")

    def _refresh_serial_ports(self) -> None:
        """Repopulate the COM-port dropdown from the live serial port list."""
        ports = list_serial_ports()
        vals = ports or ["(none)"]
        self.cat_port_menu.configure(values=vals)
        match = next((v for v in vals if port_device(v) == self.cfg.cat_port), None)
        self.cat_port_menu.set(match or vals[0])

    def _selected_cat_port(self) -> str:
        sel = self.cat_port_menu.get()
        return "" if sel == "(none)" else port_device(sel)

    def _toggle_radio(self) -> None:
        if self._safe_bool(lambda: self.radio.is_open):
            self._disconnect_radio()
        else:
            self._connect_radio()
        self._refresh_radio_btn()

    def _refresh_radio_btn(self, connected: bool | None = None) -> None:
        if not hasattr(self, "radio_btn"):
            return
        if connected is None:
            connected = self._safe_bool(lambda: self.radio.is_open)
        if connected:
            self.radio_btn.configure(text="Disconnect radio", fg_color=GREY)
        else:
            self.radio_btn.configure(text="Connect radio", fg_color=("#3B8ED0", "#1F6AA5"))

    def _connect_radio(self) -> None:
        # rigctld is required for the Hamlib backend, so always make sure a
        # working one is up (reuse if responsive, replace if wedged). No checkbox.
        if self.backend_menu.get() == "hamlib":
            try:
                model = int(self._entries["rig_model"].get() or 0)
            except ValueError:
                model = 0
            msg = self.rigctld.ensure(
                model, self._selected_cat_port(),
                int(self._entries["rigctld_port"].get() or 4532),
                self.cfg.cat_baud,
            )
            self.log(msg)
        try:
            self.radio.open()
            self.log(f"Radio connected via {self.radio.name}")
        except Exception as exc:  # noqa: BLE001 - surface any backend error
            self.log(f"Radio connect failed: {exc}")
        self._refresh_radio_btn()

    def _disconnect_radio(self) -> None:
        self.radio.close()
        self.log("Radio disconnected")
        self._refresh_radio_btn()

    def _test_ptt(self) -> None:
        try:
            self.radio.set_ptt(True)
            self.log("PTT keyed (test)")
            self.after(2000, self._unkey)
        except Exception as exc:  # noqa: BLE001
            self.log(f"PTT test failed: {exc}")

    def _unkey(self) -> None:
        try:
            self.radio.set_ptt(False)
            self.log("PTT released")
        except Exception as exc:  # noqa: BLE001
            self.log(f"PTT release failed: {exc}")

    def _connect_vara(self) -> None:
        def worker():
            try:
                self.vara.connect()
                if self.cfg.callsign and self.cfg.callsign != "NOCALL":
                    self.vara.set_mycall(self.cfg.callsign)
                self.log(f"VARA connected ({self.cfg.vara_host}:{self.cfg.vara_cmd_port})")
            except Exception as exc:  # noqa: BLE001
                self.log(f"VARA connect failed: {exc}")
        threading.Thread(target=worker, daemon=True).start()

    def _disconnect_vara(self) -> None:
        self.vara.disconnect()
        self.log("VARA disconnected")

    def _vara_cmd(self, fn) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            self.log(f"VARA command failed: {exc}")

    def _on_vara_notification(self, text: str) -> None:
        # Called from the VARA reader thread — marshal to UI thread.
        self.after(0, lambda: self._append(self.vara_box, text))
        self.after(0, lambda: self.log(f"[VARA] {text}"))

    # ---- routing actions --------------------------------------------- #
    def _add_route(self) -> None:
        dest = self.r_dest.get().strip()
        pref = self.r_pref.get().strip()
        if not dest or not pref:
            self.log("Route needs at least a destination and a preferred hop")
            return
        freq_hz = 0
        raw = self.r_freq.get().strip()
        if raw:
            try:
                freq_hz = int(float(raw) * 1_000_000)
            except ValueError:
                self.log("QSY freq: enter MHz like 145.500 (ignored)")
        self.routes.add(Route(dest, pref, self.r_back.get().strip(), freq_hz, self.r_mode.get()))
        self.routes.save()
        self._refresh_routes()
        self._refresh_station_card()
        self.log(f"Route {dest.upper()} -> {pref.upper()} saved")

    def _remove_route(self) -> None:
        dest = self.r_dest.get().strip()
        if not dest:
            self.log("Enter the destination to remove")
            return
        self.routes.remove(dest)
        self.routes.save()
        self._refresh_routes()
        self._refresh_station_card()
        self.log(f"Route {dest.upper()} removed")

    def _refresh_routes(self) -> None:
        self.route_box.delete("1.0", "end")
        header = f"{'DESTINATION':<16}{'PREFERRED':<12}{'BACKUP':<12}{'QSY FREQ':<14}{'MODE'}\n"
        self.route_box.insert("end", header)
        self.route_box.insert("end", "-" * 58 + "\n")
        if not len(self.routes):
            self.route_box.insert("end", "(no routes configured)\n")
        for r in self.routes:
            fq = f"{r.freq_hz/1e6:.4f} MHz" if r.freq_hz else "-"
            self.route_box.insert("end", f"{r.destination:<16}{r.preferred:<12}{r.backup or '-':<12}{fq:<14}{r.mode or '-'}\n")

    # ---- message actions --------------------------------------------- #
    def _compose_frame(self) -> ControlFrame:
        final = self.m_final.get().strip().upper()
        next_hop = self.m_next.get().strip().upper()
        if not next_hop and final:
            next_hop = self.routes.next_hop(final) or ""
        try:
            msg_id = int(self.m_id.get().strip() or 0)
        except ValueError:
            msg_id = 0
        flags = Flags.ACK_REQUIRED if self.m_ack.get() else Flags.NONE
        return ControlFrame(
            type=FrameType[self.m_type.get()],
            source=self.cfg.callsign,
            destination=final,
            next_hop=next_hop,
            message_id=msg_id,
            priority=Priority[self.m_prio.get()],
            ttl=self.cfg.default_ttl,
            flags=flags,
        )

    def _build_burst(self) -> None:
        frame = self._compose_frame()
        raw = frame.encode()
        self.m_out.delete("1.0", "end")
        self.m_out.insert("end", f"{frame.summary()}\n\n")
        self.m_out.insert("end", f"Encoded {len(raw)} bytes:\n{raw.hex(' ')}\n")
        self.log(f"Built burst: {frame.summary()} ({len(raw)} bytes)")

    def _roundtrip_burst(self) -> None:
        frame = self._compose_frame()
        raw = frame.encode()
        try:
            decoded = ControlFrame.decode(raw)
            ok = decoded.summary() == frame.summary()
            self.m_out.delete("1.0", "end")
            self.m_out.insert("end", f"Encoded {len(raw)} bytes:\n{raw.hex(' ')}\n\n")
            self.m_out.insert("end", f"Decoded: {decoded.summary()}\n")
            self.m_out.insert("end", f"CRC + round-trip: {'OK' if ok else 'MISMATCH'}\n")
            self.log(f"Self-test round-trip: {'OK' if ok else 'MISMATCH'}")
        except Exception as exc:  # noqa: BLE001
            self.m_out.insert("end", f"\nDECODE FAILED: {exc}\n")
            self.log(f"Self-test decode failed: {exc}")

    # ------------------------------------------------------------------ #
    #  Status polling + logging                                           #
    # ------------------------------------------------------------------ #
    def _radio_poll_loop(self) -> None:
        """Background: refresh the cached radio state without blocking the UI."""
        while not self._closing:
            try:
                self._radio_state = self.radio.get_state()
            except Exception:  # noqa: BLE001 - never let the poller die
                self._radio_state = RadioState(connected=False)
            time.sleep(1.0)

    def _poll(self) -> None:
        rs = self._radio_state          # cached snapshot from the background thread
        self._set_dot(self.dot_radio, rs.connected)
        self._refresh_radio_btn(rs.connected)
        self._set_dot(self.dot_ptt, rs.ptt, on_color=AMBER)
        radio_txt = "Not connected"
        if rs.connected:
            radio_txt = f"Connected ({self.radio.name})\nFreq: {rs.freq_mhz()}\nMode: {rs.mode or '--'}\nPTT:  {'ON' if rs.ptt else 'off'}"
        elif rs.error:
            radio_txt = f"Not connected\n{rs.error}"
        self.db_radio.configure(text=radio_txt)

        vs = self.vara.state
        self._set_dot(self.dot_vara, vs.cmd_connected)
        vara_txt = "Not connected"
        if vs.cmd_connected:
            vara_txt = f"Connected\nMYCALL: {vs.mycall or '-'}\nLink: {vs.link_state}\nLast: {vs.last_notification or '-'}"
        self.db_vara.configure(text=vara_txt)

        # Sidebar: control-channel + mode + mailbox counts. The dot is GREEN only
        # when the audio control modem is actually running (codec open), GREY
        # otherwise — it reflects the real channel state, not the saved config.
        on_air = self.audio_transport is not None
        self._set_dot(self.dot_channel, on_air)
        self._refresh_channel_btn(on_air)
        self.lbl_mode.configure(text=self._current_mode())
        counts = self.mailstore.counts()
        unread = self.mailstore.unread(Folder.INBOX)
        self.lbl_mailcount.configure(
            text=f"Inbox {counts.get('inbox', 0)}" + (f" ({unread} new)" if unread else "")
                 + f"\nOutbox {counts.get('outbox', 0)}  Transit {counts.get('transit', 0)}")
        if hasattr(self, "db_mail"):
            self.db_mail.configure(
                text=(f"Inbox {counts.get('inbox',0)} ({unread} new)\n"
                      f"Outbox {counts.get('outbox',0)} waiting\n"
                      f"Transit {counts.get('transit',0)} held"))
        self._refresh_setup_checklist()
        self._update_signal(rs)

        self.after(POLL_MS, self._poll)

    def _update_signal(self, rs) -> None:
        """RX audio level + noise floor + S-meter, with an interference hint."""
        level01 = 0.0
        text = "Audio control channel off — switch to a Live mode to see RX levels."
        if self.audio_transport is not None:
            lv = self.audio_transport.levels()
            # Map -60..0 dBFS to 0..1 for the bars.
            level01 = max(0.0, min(1.0, (lv["rms_db"] + 60) / 60))
            floor01 = max(0.0, min(1.0, (lv["floor_db"] + 60) / 60))
            hint = "floor low — channel clean"
            if lv["floor_db"] > -25:
                hint = "⚠ high noise floor — check local interference / RFI / gain"
            elif lv["floor_db"] > -40:
                hint = "moderate noise floor"
            smeter = f"  ·  S-meter: {rs.signal}" if rs.signal is not None else ""
            text = (f"RX {lv['rms_db']:.0f} dBFS   floor {lv['floor_db']:.0f} dBFS "
                    f"({floor01*100:.0f}%){smeter}\n{hint}")
        if hasattr(self, "sig_bar"):
            self.sig_bar.set(level01)
            self.sig_lbl.configure(text=text)
        if hasattr(self, "side_rx"):
            self.side_rx.set(level01)

    def _net_loop(self) -> None:
        """Drive the control-net state machine and deliver queued frames."""
        now = time.monotonic()
        self.net.tick(now)
        if self.audio_transport is not None:
            self.audio_transport.pump()      # deliver RX frames on this thread
        if self.scanner.enabled:
            self.scanner.tick(now)
            self._refresh_channels()
        self._maybe_beacon(now)
        self._auto_deliver_scan(now)
        # Rebuilding the session/heard tables is comparatively expensive; do it
        # ~1 Hz, not every 250 ms tick, so typing stays smooth.
        self._net_ticks = getattr(self, "_net_ticks", 0) + 1
        if self._net_ticks % 4 == 0:
            self._refresh_sessions()
            self._refresh_heard()
        self.after(250, self._net_loop)

    def _maybe_beacon(self, now: float) -> None:
        if self.cfg.beacon_enabled and now - self._last_beacon >= self.cfg.beacon_interval:
            self._last_beacon = now
            try:
                self.net.beacon()
                self.log("Presence beacon sent")
            except Exception as exc:  # noqa: BLE001
                self.log(f"Beacon failed: {exc}")

    def _auto_deliver_scan(self, now: float) -> None:
        """Send waiting Outbox/Transit mail when its next hop is currently heard."""
        if now - self._last_autodeliver < 5.0:
            return
        self._last_autodeliver = now
        candidates = []
        if self.cfg.auto_deliver:
            candidates += self.mailstore.list(Folder.OUTBOX)
        if self.cfg.auto_relay:
            candidates += self.mailstore.list(Folder.TRANSIT)
        for meta in candidates:
            mid = meta["msg_id"]
            sess = self.net.sessions.get(mid)
            if sess and not sess.state.terminal:
                continue  # already in flight
            hop, _how = self.net._resolve_next_hop(meta["final_dest"])
            if not hop or not self.heard.is_heard(hop, now):
                continue
            if now - self._deliver_attempts.get(mid, -1e9) < 30.0:
                continue
            self._deliver_attempts[mid] = now
            mail = self.mailstore.get(mid)
            if mail is not None:
                self.log(f"Auto-deliver #{mid} -> {hop} (heard)")
                self._send_mail(mail)

    def _set_dot(self, dot, on: bool, on_color: str = GREEN) -> None:
        dot.configure(text_color=on_color if on else GREY)

    def _set_appearance(self, mode: str) -> None:
        ctk.set_appearance_mode(mode)
        self.cfg.appearance = mode

    def log(self, msg: str) -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._append(self.log_box, f"{ts}  {msg}")

    def _append(self, box, text: str) -> None:
        box.insert("end", text + "\n")
        box.see("end")

    def _on_close(self) -> None:
        self._closing = True            # stop the background radio poller
        try:
            self.cfg.appearance = self.appearance_menu.get()
            self.cfg.save()
        except Exception:
            pass
        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            pass
        try:
            if self.audio_transport is not None:
                self.audio_transport.stop()
        except Exception:
            pass
        try:
            self.radio.close()
        except Exception:
            pass
        try:
            self.rigctld.stop()
        except Exception:
            pass
        try:
            self.vara.disconnect()
        except Exception:
            pass
        self.destroy()
