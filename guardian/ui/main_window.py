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
from ..radio import make_driver
from ..radio.presets import CURATED, load_hamlib_models
from ..radio.rigctld_launcher import RigctldProcess
from ..modem import make_modem
from ..modem.audio import AudioControlTransport, list_audio_devices
from ..payload import make_backend
from ..radio.scanner import Channel, ChannelPlan, ChannelScanner
from ..radio.usb_serial import detect as detect_usb_serial
from ..routing import HeardStations, Route, RouteTable
from ..session import LoopbackBus, Orchestrator, SessionState
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

        # Control-net: loopback (simulation) transport until the Phase-3 modem
        # exists. Our own orchestrator + on-demand virtual partner stations.
        self.heard = HeardStations()
        self.mailstore = MessageStore()
        self.mail_folder = Folder.INBOX
        self.mail_selected = None
        self._stored_inbound: set = set()
        self.channel_plan = ChannelPlan.load()
        self.scanner = ChannelScanner(
            self.radio, self.channel_plan, dwell=self.cfg.scan_dwell,
            on_change=lambda ch: self.log(f"Scan -> {ch.name} ({ch.freq_hz/1e6:.4f} MHz)"),
            on_log=self.log,
        )
        self.bus = LoopbackBus(monitor=self._on_channel_frame)
        self.partners: dict[str, Orchestrator] = {}
        self.audio_transport = None  # set when control channel = audio
        self._deliver_attempts: dict[int, float] = {}
        self._last_beacon = 0.0
        self._last_autodeliver = 0.0
        self.net = self._build_net(self.bus.endpoint(self.cfg.callsign))

        ctk.set_appearance_mode(self.cfg.appearance)
        ctk.set_default_color_theme("blue")

        self.title(f"{__app_name__} — ARDOS Control Layer  v{__version__}")
        self.geometry("1040x700")
        self.minsize(900, 580)
        self._apply_icon()

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_tabs()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.tray = None
        self._start_tray()
        self.log(f"{__app_name__} v{__version__} started. Station: {self.cfg.callsign}")
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
        bar.grid_rowconfigure(7, weight=1)

        ctk.CTkLabel(bar, text="GUARDIAN", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, padx=20, pady=(20, 0)
        )
        ctk.CTkLabel(bar, text="ARDOS control layer", text_color=GREY).grid(
            row=1, column=0, padx=20, pady=(0, 16)
        )

        self.lbl_call = ctk.CTkLabel(bar, text=self.cfg.callsign, font=ctk.CTkFont(size=18, weight="bold"))
        self.lbl_call.grid(row=2, column=0, padx=20, pady=(0, 12))

        self.dot_radio = self._status_row(bar, 3, "Radio")
        self.dot_vara = self._status_row(bar, 4, "VARA")
        self.dot_ptt = self._status_row(bar, 5, "PTT")

        ctk.CTkLabel(bar, text="Appearance").grid(row=8, column=0, padx=20, pady=(10, 0), sticky="w")
        self.appearance_menu = ctk.CTkOptionMenu(
            bar, values=["System", "Dark", "Light"], command=self._set_appearance
        )
        self.appearance_menu.set(self.cfg.appearance)
        self.appearance_menu.grid(row=9, column=0, padx=20, pady=(0, 20), sticky="ew")

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
        for name in ("Dashboard", "Mail", "Radio", "VARA", "Routing", "Net", "Mesh", "Messages", "Log"):
            self.tabs.add(name)
        self._build_dashboard(self.tabs.tab("Dashboard"))
        self._build_mail_tab(self.tabs.tab("Mail"))
        self._build_radio_tab(self.tabs.tab("Radio"))
        self._build_vara_tab(self.tabs.tab("VARA"))
        self._build_routing_tab(self.tabs.tab("Routing"))
        self._build_net_tab(self.tabs.tab("Net"))
        self._build_mesh_tab(self.tabs.tab("Mesh"))
        self._build_messages_tab(self.tabs.tab("Messages"))
        self._build_log_tab(self.tabs.tab("Log"))

    # ---- Dashboard ---------------------------------------------------- #
    def _build_dashboard(self, tab) -> None:
        tab.grid_columnconfigure((0, 1), weight=1)

        radio_card = ctk.CTkFrame(tab)
        radio_card.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        ctk.CTkLabel(radio_card, text="Radio", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=14, pady=(12, 4))
        self.db_radio = ctk.CTkLabel(radio_card, text="Not connected", justify="left", text_color=GREY)
        self.db_radio.pack(anchor="w", padx=14, pady=(0, 12))

        vara_card = ctk.CTkFrame(tab)
        vara_card.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")
        ctk.CTkLabel(vara_card, text="VARA FM", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=14, pady=(12, 4))
        self.db_vara = ctk.CTkLabel(vara_card, text="Not connected", justify="left", text_color=GREY)
        self.db_vara.pack(anchor="w", padx=14, pady=(0, 12))

        info = ctk.CTkFrame(tab)
        info.grid(row=1, column=0, columnspan=2, padx=10, pady=10, sticky="nsew")
        ctk.CTkLabel(info, text="Station", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=14, pady=(12, 4))
        self.db_station = ctk.CTkLabel(info, text="", justify="left")
        self.db_station.pack(anchor="w", padx=14, pady=(0, 12))
        self._refresh_station_card()

    def _refresh_station_card(self) -> None:
        c = self.cfg
        self.db_station.configure(
            text=(
                f"Callsign:   {c.callsign}\n"
                f"Operator:   {c.operator_name or '-'}\n"
                f"Radio:      {c.radio or '-'}  (backend: {c.radio_backend})\n"
                f"rigctld:    {c.rigctld_host}:{c.rigctld_port}\n"
                f"VARA {c.vara_mode}:    {c.vara_host}  cmd {c.vara_cmd_port} / data {c.vara_data_port}\n"
                f"Burst modem:{c.active_modem()}\n"
                f"Payload:    {c.payload_backend}\n"
                f"Routes:     {len(self.routes)} configured"
            )
        )

    # ---- Radio tab ---------------------------------------------------- #
    def _build_radio_tab(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)
        self._field(tab, 0, "Callsign", "callsign")
        self._field(tab, 1, "Operator name", "operator_name")

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
        self._field(tab, 6, "CAT / PTT COM port", "cat_port")
        self._field(tab, 7, "rigctld host", "rigctld_host")
        self._field(tab, 8, "rigctld port", "rigctld_port")

        self.autostart_chk = ctk.CTkCheckBox(tab, text="Auto-start rigctld on connect")
        if self.cfg.rigctld_autostart:
            self.autostart_chk.select()
        self.autostart_chk.grid(row=9, column=1, padx=14, pady=6, sticky="w")

        ctk.CTkLabel(tab, text="VOX PTT line").grid(row=10, column=0, padx=14, pady=8, sticky="w")
        self.ptt_menu = ctk.CTkOptionMenu(tab, values=["RTS", "DTR"])
        self.ptt_menu.set(self.cfg.ptt_line)
        self.ptt_menu.grid(row=10, column=1, padx=14, pady=8, sticky="ew")

        btns = ctk.CTkFrame(tab, fg_color="transparent")
        btns.grid(row=11, column=0, columnspan=2, padx=10, pady=14, sticky="ew")
        ctk.CTkButton(btns, text="Save", command=self._save_config).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Connect radio", command=self._connect_radio).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Disconnect", fg_color=GREY, command=self._disconnect_radio).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Test PTT (2s)", fg_color=AMBER, command=self._test_ptt).pack(side="left", padx=6)

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

        btns = ctk.CTkFrame(tab, fg_color="transparent")
        btns.grid(row=5, column=0, columnspan=2, padx=10, pady=14, sticky="ew")
        ctk.CTkButton(btns, text="Save", command=self._save_config).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Connect VARA", command=self._connect_vara).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Disconnect", fg_color=GREY, command=self._disconnect_vara).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="LISTEN ON", command=lambda: self._vara_cmd(lambda: self.vara.listen(True))).pack(side="left", padx=6)

        ctk.CTkLabel(tab, text="VARA notifications:").grid(row=6, column=0, padx=14, pady=(10, 2), sticky="w")
        self.vara_box = ctk.CTkTextbox(tab, height=200)
        self.vara_box.grid(row=7, column=0, columnspan=2, padx=14, pady=(0, 14), sticky="nsew")
        tab.grid_rowconfigure(7, weight=1)
        self._update_modem_label()

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
        for i in range(6):
            editor.grid_columnconfigure(i, weight=1)
        ctk.CTkLabel(editor, text="Destination / group").grid(row=0, column=0, padx=6, pady=4, sticky="w")
        ctk.CTkLabel(editor, text="Preferred next hop").grid(row=0, column=1, padx=6, pady=4, sticky="w")
        ctk.CTkLabel(editor, text="Backup (or ANY)").grid(row=0, column=2, padx=6, pady=4, sticky="w")
        self.r_dest = ctk.CTkEntry(editor, placeholder_text="OK1CCC")
        self.r_pref = ctk.CTkEntry(editor, placeholder_text="OK1DDD")
        self.r_back = ctk.CTkEntry(editor, placeholder_text="OK1EEE / ANY")
        self.r_dest.grid(row=1, column=0, padx=6, pady=4, sticky="ew")
        self.r_pref.grid(row=1, column=1, padx=6, pady=4, sticky="ew")
        self.r_back.grid(row=1, column=2, padx=6, pady=4, sticky="ew")
        ctk.CTkButton(editor, text="Add / Update", command=self._add_route).grid(row=1, column=3, padx=6, pady=4)
        ctk.CTkButton(editor, text="Remove", fg_color=GREY, command=self._remove_route).grid(row=1, column=4, padx=6, pady=4)

        self.route_box = ctk.CTkTextbox(tab, font=ctk.CTkFont(family="Consolas", size=13))
        self.route_box.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")
        self._refresh_routes()

    # ---- Net tab (live session orchestration) ------------------------- #
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
        ctk.CTkLabel(compose, text="Next hop (blank = route)").grid(row=1, column=2, padx=8, pady=4, sticky="w")
        self.n_next = ctk.CTkEntry(compose, placeholder_text="OK1DDD")
        self.n_next.grid(row=1, column=3, padx=8, pady=4, sticky="ew")

        ctk.CTkLabel(compose, text="Priority").grid(row=2, column=0, padx=8, pady=4, sticky="w")
        self.n_prio = ctk.CTkOptionMenu(compose, values=[p.name for p in Priority])
        self.n_prio.set(Priority.ROUTINE.name)
        self.n_prio.grid(row=2, column=1, padx=8, pady=4, sticky="w")

        ctk.CTkLabel(compose, text="Message").grid(row=3, column=0, padx=8, pady=4, sticky="nw")
        self.n_body = ctk.CTkEntry(compose, placeholder_text="message text / body…")
        self.n_body.grid(row=3, column=1, columnspan=3, padx=8, pady=4, sticky="ew")

        ctk.CTkLabel(compose, text="Payload transport").grid(row=4, column=0, padx=8, pady=4, sticky="w")
        self.payload_menu = ctk.CTkOptionMenu(
            compose, values=["vara_p2p", "winlink_manual"], command=self._set_payload_backend)
        self.payload_menu.set(self.cfg.payload_backend)
        self.payload_menu.grid(row=4, column=1, padx=8, pady=4, sticky="w")
        ctk.CTkLabel(compose, text="vara_p2p = Guardian sends it · winlink_manual = you send via Winlink",
                     text_color=GREY).grid(row=4, column=2, columnspan=2, padx=8, pady=4, sticky="w")

        actions = ctk.CTkFrame(compose, fg_color="transparent")
        actions.grid(row=5, column=0, columnspan=4, padx=4, pady=8, sticky="ew")
        ctk.CTkButton(actions, text="Send over net", command=self._net_send).pack(side="left", padx=6)
        self.sim_chk = ctk.CTkCheckBox(actions, text="Simulate next-hop reply (loopback)")
        self.sim_chk.select()
        self.sim_chk.pack(side="left", padx=12)
        self.sim_note = ctk.CTkLabel(actions, text="Loopback channel (simulation).", text_color=GREY)
        self.sim_note.pack(side="left", padx=6)

        # Control channel: loopback simulation vs real audio over the radio.
        chan = ctk.CTkFrame(tab)
        chan.grid(row=1, column=0, padx=10, pady=(0, 8), sticky="ew")
        chan.grid_columnconfigure(5, weight=1)
        ctk.CTkLabel(chan, text="Control channel").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        self.channel_seg = ctk.CTkSegmentedButton(chan, values=["loopback", "audio"], command=self._set_control_channel)
        self.channel_seg.set(self.cfg.control_channel)
        self.channel_seg.grid(row=0, column=1, padx=8, pady=8)
        ctk.CTkLabel(chan, text="Audio in").grid(row=0, column=2, padx=(14, 4), pady=8, sticky="e")
        self.audio_in_menu = ctk.CTkOptionMenu(chan, values=["(default)"], width=180)
        self.audio_in_menu.grid(row=0, column=3, padx=4, pady=8)
        ctk.CTkLabel(chan, text="out").grid(row=1, column=2, padx=(14, 4), pady=(0, 8), sticky="e")
        self.audio_out_menu = ctk.CTkOptionMenu(chan, values=["(default)"], width=180)
        self.audio_out_menu.grid(row=1, column=3, padx=4, pady=(0, 8))
        ctk.CTkButton(chan, text="Refresh devices", width=120, command=self._refresh_audio_devices).grid(
            row=0, column=4, padx=8, pady=8)
        self.channel_status = ctk.CTkLabel(chan, text="", text_color=GREY, justify="left")
        self.channel_status.grid(row=1, column=4, columnspan=2, padx=8, pady=(0, 8), sticky="w")
        self._refresh_audio_devices()

        ctk.CTkLabel(tab, text="Sessions").grid(row=2, column=0, padx=14, pady=(4, 0), sticky="w")
        self.sessions_box = ctk.CTkTextbox(tab, height=140, font=ctk.CTkFont(family="Consolas", size=12))
        self.sessions_box.grid(row=3, column=0, padx=10, pady=(0, 8), sticky="nsew")

        ctk.CTkLabel(tab, text="On-air channel monitor").grid(row=4, column=0, padx=14, pady=(4, 0), sticky="w")
        self.channel_box = ctk.CTkTextbox(tab, height=140, font=ctk.CTkFont(family="Consolas", size=12))
        self.channel_box.grid(row=5, column=0, padx=10, pady=(0, 10), sticky="nsew")
        tab.grid_rowconfigure(3, weight=1)

    def _net_send(self) -> None:
        final = self.n_final.get().strip().upper()
        if not final:
            self.log("Net send: enter a final destination")
            return
        next_hop = self.n_next.get().strip().upper()
        msg_id = self.mailstore.next_id(self.cfg.callsign)
        resolved = next_hop or (self.routes.next_hop(final) or final)
        if self.sim_chk.get():
            self._ensure_partner(resolved)
        msg = self.net.send_message(
            final_dest=final, body=self.n_body.get(), msg_id=msg_id,
            priority=Priority[self.n_prio.get()], next_hop=next_hop or None,
        )
        self.log(f"Net: started session #{msg.msg_id} -> {msg.next_hop} (final {final})")

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
            self.channel_status.configure(text=f"{len(inputs)} in / {len(outputs)} out devices", text_color=GREY)

    def _set_control_channel(self, mode: str) -> None:
        if mode == "audio":
            self._start_audio_channel()
        else:
            self._start_loopback_channel()

    def _start_loopback_channel(self) -> None:
        if getattr(self, "audio_transport", None) is not None:
            self._safe(self.audio_transport.stop)
            self.audio_transport = None
        self.cfg.control_channel = "loopback"
        self.net = self._build_net(self.bus.endpoint(self.cfg.callsign))
        self.sim_chk.configure(state="normal")
        self.sim_chk.select()
        self.channel_seg.set("loopback")
        self.sim_note.configure(text="Loopback channel (simulation).")
        self.channel_status.configure(text="Loopback (simulation) active.", text_color=GREY)
        self.log("Control channel: loopback (simulation)")

    def _start_audio_channel(self) -> None:
        in_dev = None if self.audio_in_menu.get() == "(default)" else self.audio_in_menu.get()
        out_dev = None if self.audio_out_menu.get() == "(default)" else self.audio_out_menu.get()
        self.cfg.audio_input = in_dev or ""
        self.cfg.audio_output = out_dev or ""
        modem = make_modem(self.cfg.active_modem())
        transport = AudioControlTransport(
            modem=modem, ptt=self._radio_ptt,
            sample_rate=modem.fs if hasattr(modem, "fs") else 48000,
            input_device=in_dev, output_device=out_dev, on_log=self.log,
        )
        try:
            transport.start()
        except Exception as exc:  # noqa: BLE001 - no audio backend / bad device
            self.log(f"Audio channel failed: {exc} — staying on loopback")
            self.channel_seg.set("loopback")
            self.channel_status.configure(text=f"Audio failed: {exc}", text_color=RED)
            return
        self.audio_transport = transport
        self.cfg.control_channel = "audio"
        self.net = self._build_net(transport)
        self.sim_chk.deselect()
        self.sim_chk.configure(state="disabled")
        self.channel_seg.set("audio")
        self.sim_note.configure(text="LIVE audio over the radio.")
        self.channel_status.configure(text=f"Audio active ({modem.name}).", text_color=GREEN)
        self.log(f"Control channel: audio ({modem.name}) in={in_dev or 'default'} out={out_dev or 'default'}")

    def _radio_ptt(self, on: bool) -> None:
        try:
            self.radio.set_ptt(on)
        except Exception as exc:  # noqa: BLE001
            self.log(f"PTT error: {exc}")

    def _make_payload_backend(self):
        return make_backend(
            self.cfg.payload_backend, vara=self.vara,
            prompt=self._winlink_prompt, on_log=self.log,
        )

    def _payload_for_net(self):
        """Which payload backend the live orchestrator should use.

        On the real audio channel, use the configured backend. In loopback
        simulation, vara_p2p has no VARA so we run pure control-flow simulation
        (payload=None, partner auto-completes); winlink_manual is a UI-only
        dialog and is safe to preview in loopback.
        """
        if self.cfg.control_channel == "audio":
            return self._make_payload_backend()
        if self.cfg.payload_backend == "winlink_manual":
            return self._make_payload_backend()
        return None

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

    def _ensure_partner(self, callsign: str) -> None:
        """Spin up a simulated remote station that completes the handshake."""
        callsign = callsign.strip().upper()
        if not callsign or callsign == self.cfg.callsign or callsign in self.partners:
            return
        self.partners[callsign] = Orchestrator(
            callsign, self.bus.endpoint(callsign), auto_complete=True,
            on_event=self._on_session_event,
        )

    def _on_session_event(self, message, event: str) -> None:
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
        if not hasattr(self, "sessions_box"):
            return
        self.sessions_box.delete("1.0", "end")
        rows = []
        for o in [self.net, *self.partners.values()]:
            for m in o.sessions.values():
                rows.append((m.msg_id, o.callsign, m.direction, m.next_hop, m.final_dest, m.state.value, m.error))
        if not rows:
            self.sessions_box.insert("end", "(no sessions yet)\n")
            return
        self.sessions_box.insert("end", f"{'ID':<6}{'STATION':<9}{'DIR':<4}{'NEXT':<9}{'FINAL':<9}{'STATE':<13}NOTE\n")
        for mid, stn, d, nh, fd, st, err in sorted(rows):
            self.sessions_box.insert("end", f"{mid:<6}{stn:<9}{d:<4}{nh:<9}{fd:<9}{st:<13}{err}\n")

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
        resolved = self.routes.next_hop(mail.final_dest) or mail.final_dest
        if self.cfg.control_channel == "loopback" and self.sim_chk.get():
            self._ensure_partner(resolved)
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
        resolved = self.routes.next_hop(mail.final_dest) or mail.final_dest
        if self.cfg.control_channel == "loopback" and self.sim_chk.get():
            self._ensure_partner(resolved)
        self.net.send_message(
            final_dest=mail.final_dest, body=mail.subject, msg_id=mail.msg_id,
            priority=Priority(mail.priority), payload_bytes=mail.to_bundle(),
        )
        self.log(f"Forwarding transit #{msg_id} toward {mail.final_dest}")
        self._refresh_mail_list()

    # ---- Mesh tab (smart routing + scanning) -------------------------- #
    def _build_mesh_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(3, weight=1)

        opts = ctk.CTkFrame(tab)
        opts.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        ctk.CTkLabel(opts, text="Smart routing", font=ctk.CTkFont(size=15, weight="bold")).pack(
            anchor="w", padx=10, pady=(8, 4))
        row = ctk.CTkFrame(opts, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(0, 8))
        self.auto_route_chk = ctk.CTkCheckBox(
            row, text="Auto-route (discover next hop via ROUTE_QUERY)", command=self._apply_mesh_opts)
        if self.cfg.auto_route:
            self.auto_route_chk.select()
        self.auto_route_chk.pack(side="left", padx=6)
        self.auto_relay_chk = ctk.CTkCheckBox(
            row, text="Auto-relay (forward messages for others — mesh)", command=self._apply_mesh_opts)
        if self.cfg.auto_relay:
            self.auto_relay_chk.select()
        self.auto_relay_chk.pack(side="left", padx=16)

        row2 = ctk.CTkFrame(opts, fg_color="transparent")
        row2.pack(fill="x", padx=8, pady=(0, 8))
        self.auto_deliver_chk = ctk.CTkCheckBox(
            row2, text="Auto-deliver waiting mail when the next hop is heard", command=self._apply_mesh_opts)
        if self.cfg.auto_deliver:
            self.auto_deliver_chk.select()
        self.auto_deliver_chk.pack(side="left", padx=6)
        self.beacon_chk = ctk.CTkCheckBox(
            row2, text="Send presence beacon (so others can deliver to me)", command=self._apply_mesh_opts)
        if self.cfg.beacon_enabled:
            self.beacon_chk.select()
        self.beacon_chk.pack(side="left", padx=16)

        ctk.CTkLabel(tab, text="Heard stations").grid(row=1, column=0, padx=14, pady=(4, 0), sticky="w")
        self.heard_box = ctk.CTkTextbox(tab, height=150, font=ctk.CTkFont(family="Consolas", size=12))
        self.heard_box.grid(row=2, column=0, padx=10, pady=(0, 8), sticky="nsew")
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
        if not hasattr(self, "heard_box"):
            return
        now = time.monotonic()
        self.heard.prune(now)
        stations = self.heard.active(now)
        self.heard_box.delete("1.0", "end")
        self.heard_box.insert("end", f"{'STATION':<10}{'AGE':<7}{'SEEN':<6}{'LAST':<12}REACHES\n")
        if not stations:
            self.heard_box.insert("end", "(nothing heard yet)\n")
        for s in stations:
            reaches = ",".join(sorted(s.reaches)) or "-"
            self.heard_box.insert("end", f"{s.callsign:<10}{int(s.age(now)):<7}{s.count:<6}{s.last_frame:<12}{reaches}\n")

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
        self.cfg.appearance = self.appearance_menu.get()
        self.cfg.rigctld_autostart = bool(self.autostart_chk.get())
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

    def _connect_radio(self) -> None:
        # Auto-start rigctld if the operator asked us to (Hamlib backend only).
        if self.backend_menu.get() == "hamlib" and self.autostart_chk.get():
            try:
                model = int(self._entries["rig_model"].get() or 0)
            except ValueError:
                model = 0
            msg = self.rigctld.start(
                model, self._entries["cat_port"].get().strip(),
                int(self._entries["rigctld_port"].get() or 4532),
                self.cfg.cat_baud,
            )
            self.log(msg)
        try:
            self.radio.open()
            self.log(f"Radio connected via {self.radio.name}")
        except Exception as exc:  # noqa: BLE001 - surface any backend error
            self.log(f"Radio connect failed: {exc}")

    def _disconnect_radio(self) -> None:
        self.radio.close()
        self.log("Radio disconnected")

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
        self.routes.add(Route(dest, pref, self.r_back.get().strip()))
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
        header = f"{'DESTINATION':<18}{'PREFERRED':<14}{'BACKUP':<14}\n"
        self.route_box.insert("end", header)
        self.route_box.insert("end", "-" * 46 + "\n")
        if not len(self.routes):
            self.route_box.insert("end", "(no routes configured)\n")
        for r in self.routes:
            self.route_box.insert("end", f"{r.destination:<18}{r.preferred:<14}{r.backup or '-':<14}\n")

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
    def _poll(self) -> None:
        rs = self.radio.get_state()
        self._set_dot(self.dot_radio, rs.connected)
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

        self.after(POLL_MS, self._poll)

    def _net_loop(self) -> None:
        """Drive the control-net state machine and deliver queued frames."""
        now = time.monotonic()
        self.net.tick(now)
        for partner in self.partners.values():
            partner.tick(now)
        if self.audio_transport is not None:
            self.audio_transport.pump()      # deliver RX frames on this thread
        for _ in range(8):
            if self.bus.idle:
                break
            self.bus.pump()
        if self.scanner.enabled:
            self.scanner.tick(now)
            self._refresh_channels()
        self._maybe_beacon(now)
        self._auto_deliver_scan(now)
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
