"""
gui.py
GUI per PetCube Companion. Dashboard CustomTkinter + tray icon.

Lancia con:
    python gui.py

Per avvio CLI (no GUI), continua a usare:
    python main.py
"""
import asyncio
import json
import logging
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from tkinter import filedialog
from typing import Optional

import customtkinter as ctk

try:
    from PIL import Image, ImageDraw
    import pystray
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

from companion_engine import CompanionEngine, load_config
import firmware_updater as fw_upd
import app_updater as app_upd
from version import APP_VERSION
from notification_packet import (
    NotifPacket, NotifSource, NotifCategory, NotifPriority,
    compute_seed_hash,
)


# ── Dark theme palette (Discord/VS Code style) ─────────────────
BG_PRIMARY    = "#1e1e1e"
BG_SECONDARY  = "#252526"
BG_TERTIARY   = "#2d2d30"
ACCENT        = "#0e639c"
ACCENT_HOVER  = "#1177bb"
SUCCESS       = "#4ec9b0"
WARNING       = "#dcdcaa"
ERROR         = "#f48771"
TEXT_PRIMARY  = "#cccccc"
TEXT_DIM      = "#858585"
BORDER        = "#3e3e42"

CONFIG_PATH = Path("config.json")

SOURCE_LABEL = {
    NotifSource.DISCORD:   "💬 Discord",
    NotifSource.GMAIL:     "📧 Gmail",
    NotifSource.CALENDAR:  "📅 Calendar",
    NotifSource.SLACK:     "💼 Slack",
    NotifSource.TRELLO:    "📋 HacknPlan",
    NotifSource.GITHUB:    "🐙 GitHub",
    NotifSource.TELEGRAM:  "✈ Telegram",
    NotifSource.WHATSAPP:  "📱 WhatsApp",
    NotifSource.GENERIC:   "❔ Other",
}

CATEGORY_LABEL = {
    NotifCategory.LODE:        "Lode",
    NotifCategory.OPPORTUNITA: "Opportunità",
    NotifCategory.ROUTINE:     "Routine",
    NotifCategory.CURIOSITA:   "Curiosità",
    NotifCategory.SCADENZA:    "Scadenza",
    NotifCategory.CRITICA:     "Critica",
    NotifCategory.AIUTO:       "Aiuto",
    NotifCategory.CRISI:       "Crisi",
}

# (key, label, field_type)
# field_type: "text" | "password" | "int" | "int_nullable" | "list_int" | "list_str"
_PLUGIN_FIELDS: dict[str, list[tuple[str, str, str]]] = {
    "calendar": [
        ("poll_interval_sec",  "Polling (sec)",       "int"),
        ("lookahead_minutes",  "Preavviso (min)",      "int"),
        ("credentials_file",   "File credenziali",    "text"),
    ],
    "discord": [
        ("bot_token",          "Bot Token",            "password"),
        ("user_id",            "User ID (o vuoto)",    "int_nullable"),
        ("poll_interval_sec",  "Polling (sec)",        "int"),
        ("monitor_channel_ids","Channel IDs (virgola)","list_int"),
    ],
    "gmail": [
        ("poll_interval_sec",  "Polling (sec)",        "int"),
        ("credentials_file",   "File credenziali",    "text"),
        ("login_hint",         "Login hint (email)",  "text"),
    ],
    "hacknplan": [
        ("poll_interval_sec",  "Polling (sec)",        "int"),
        ("lookahead_hours",    "Preavviso (ore)",      "int"),
        ("api_key",            "API Key",              "password"),
        ("target_user_id",     "Target User ID (o vuoto)", "int_nullable"),
    ],
    "telegram": [
        ("api_id",            "API ID (my.telegram.org)",  "int"),
        ("api_hash",          "API Hash",                  "password"),
        ("phone_number",      "Numero di telefono",        "text"),
        ("session_file",      "File sessione",             "text"),
        ("poll_interval_sec", "Polling (sec)",             "int"),
        ("monitor_chat_ids",  "Chat IDs extra (virgola)",  "list_int"),
    ],
    "whatsapp": [
        ("session_dir",       "Dir sessione browser",                     "text"),
        ("poll_interval_sec", "Polling (sec)",                            "int"),
        ("monitor_chats",     "Chat da monitorare (virgola, vuoto=tutte)", "list_str"),
    ],
    "slack":   [],
    "github":  [],
    "trello":  [],
}

# ── Test console mappings ──────────────────────────────────────
_TEST_SOURCES = ["Discord", "Gmail", "Calendar", "Slack", "HacknPlan", "GitHub",
                 "Telegram", "WhatsApp", "Generic"]
_TEST_SOURCE_MAP: dict[str, NotifSource] = {
    "Discord":   NotifSource.DISCORD,
    "Gmail":     NotifSource.GMAIL,
    "Calendar":  NotifSource.CALENDAR,
    "Slack":     NotifSource.SLACK,
    "HacknPlan": NotifSource.TRELLO,
    "GitHub":    NotifSource.GITHUB,
    "Telegram":  NotifSource.TELEGRAM,
    "WhatsApp":  NotifSource.WHATSAPP,
    "Generic":   NotifSource.GENERIC,
}

_TEST_CATEGORIES = ["Lode", "Opportunità", "Routine", "Scadenza", "Critica", "Crisi", "Curiosità", "Aiuto"]
_TEST_CATEGORY_MAP: dict[str, NotifCategory] = {
    "Lode":        NotifCategory.LODE,
    "Opportunità": NotifCategory.OPPORTUNITA,
    "Routine":     NotifCategory.ROUTINE,
    "Scadenza":    NotifCategory.SCADENZA,
    "Critica":     NotifCategory.CRITICA,
    "Crisi":       NotifCategory.CRISI,
    "Curiosità":   NotifCategory.CURIOSITA,
    "Aiuto":       NotifCategory.AIUTO,
}

_TEST_PRIORITIES = ["Low", "Normal", "High"]
_TEST_PRIORITY_MAP: dict[str, NotifPriority] = {
    "Low":    NotifPriority.LOW,
    "Normal": NotifPriority.NORMAL,
    "High":   NotifPriority.HIGH,
}

_PLUGIN_DISPLAY_NAME = {
    "calendar":  "Calendar",
    "discord":   "Discord",
    "gmail":     "Gmail",
    "hacknplan": "HacknPlan",
    "slack":     "Slack",
    "github":    "GitHub",
    "trello":    "Trello",
    "telegram":  "Telegram",
    "whatsapp":  "WhatsApp",
}


class CompanionGUI(ctk.CTk):
    def __init__(self, config: dict):
        super().__init__()
        self.config_data = config

        self.title("PetCube Companion")
        self.geometry("1100x700")
        self.minsize(900, 600)
        self.configure(fg_color=BG_PRIMARY)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.engine: Optional[CompanionEngine] = None
        self.event_queue: queue.Queue = queue.Queue()
        self.recent_notifications: list[dict] = []
        self.tray_icon = None
        self._tray_visible = False

        # Test console widget state
        self._test_sv_source = ctk.StringVar(value="Discord")
        self._test_sv_category = ctk.StringVar(value="Routine")
        self._test_sv_priority = ctk.StringVar(value="Normal")
        self._test_send_btn: Optional[ctk.CTkButton] = None
        self._test_log: Optional[ctk.CTkTextbox] = None

        # Firmware tab state
        self._fw_ble_address: Optional[str] = None
        self._fw_device_ver: Optional[int] = None
        self._fw_local_info: Optional[fw_upd.FirmwareInfo] = None
        self._fw_github_info: Optional[fw_upd.FirmwareInfo] = None
        self._fw_sv_fw_dir = ctk.StringVar(value=str(Path("firmware").resolve()))
        self._fw_sv_port = ctk.StringVar(value="")
        self._fw_lbl_device_ver: Optional[ctk.CTkLabel] = None
        self._fw_lbl_github_ver: Optional[ctk.CTkLabel] = None
        self._fw_lbl_local_ver: Optional[ctk.CTkLabel] = None
        self._fw_lbl_status: Optional[ctk.CTkLabel] = None
        self._fw_btn_scan: Optional[ctk.CTkButton] = None
        self._fw_btn_check_gh: Optional[ctk.CTkButton] = None
        self._fw_btn_ota: Optional[ctk.CTkButton] = None
        self._fw_btn_flash: Optional[ctk.CTkButton] = None
        self._fw_progressbar: Optional[ctk.CTkProgressBar] = None
        self._fw_lbl_progress: Optional[ctk.CTkLabel] = None
        self._fw_log: Optional[ctk.CTkTextbox] = None
        self._fw_port_menu: Optional[ctk.CTkOptionMenu] = None

        # Companion app self-update state
        self._app_release_info: Optional[app_upd.AppReleaseInfo] = None
        self._app_lbl_version: Optional[ctk.CTkLabel] = None
        self._app_lbl_release: Optional[ctk.CTkLabel] = None
        self._app_btn_check: Optional[ctk.CTkButton] = None
        self._app_btn_update: Optional[ctk.CTkButton] = None

        # Settings widget state
        self._sv_device: dict[str, ctk.StringVar] = {}
        self._sv_plugins: dict[str, dict[str, ctk.Variable]] = {}
        self._sv_transport_prefer = ctk.StringVar(value="ble")
        self._sv_transport_timeout = ctk.StringVar(value="10")
        self._sv_log_level = ctk.StringVar(value="INFO")
        self._plugin_detail_frames: dict[str, ctk.CTkFrame] = {}
        self._settings_msg_label: Optional[ctk.CTkLabel] = None
        self._running_banner: Optional[ctk.CTkLabel] = None

        self._build_ui()
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self._on_close_window)
        self.after(100, self._poll_event_queue)

        if HAS_TRAY:
            self._setup_tray()

    # ═══════════════════════════════════════════════════════════
    # UI Construction
    # ═══════════════════════════════════════════════════════════

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self._build_body()

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=BG_SECONDARY, height=60, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(2, weight=1)

        self.status_dot = ctk.CTkLabel(
            header, text="●", text_color=TEXT_DIM, font=("Arial", 28)
        )
        self.status_dot.grid(row=0, column=0, padx=(15, 5), pady=10)

        ctk.CTkLabel(
            header,
            text="PetCube Companion",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=1, padx=(0, 20), pady=10, sticky="w")

        self.status_text = ctk.CTkLabel(
            header, text="Stopped",
            font=ctk.CTkFont(size=12), text_color=TEXT_DIM,
        )
        self.status_text.grid(row=0, column=2, pady=10, sticky="w")

        self.start_btn = ctk.CTkButton(
            header, text="▶ Start", width=100,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._on_start,
        )
        self.start_btn.grid(row=0, column=3, padx=5, pady=10)

        self.stop_btn = ctk.CTkButton(
            header, text="■ Stop", width=100,
            fg_color=BG_TERTIARY, hover_color="#444",
            command=self._on_stop, state="disabled",
        )
        self.stop_btn.grid(row=0, column=4, padx=(5, 15), pady=10)

    def _build_body(self) -> None:
        body = ctk.CTkFrame(self, fg_color=BG_PRIMARY, corner_radius=0)
        body.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self._build_sidebar(body)
        self._build_main_area(body)

    def _build_sidebar(self, parent) -> None:
        sidebar = ctk.CTkFrame(parent, fg_color=BG_SECONDARY, width=260, corner_radius=8)
        sidebar.grid(row=0, column=0, sticky="nsw", padx=(0, 10))
        sidebar.grid_propagate(False)

        ctk.CTkLabel(
            sidebar, text="PLUGINS",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_DIM, anchor="w",
        ).pack(fill="x", padx=15, pady=(15, 5))

        self.plugin_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        self.plugin_frame.pack(fill="x", padx=10)
        self.plugin_labels: dict[str, ctk.CTkLabel] = {}

        plugins_cfg = self.config_data.get("plugins", {})
        for name, cfg in plugins_cfg.items():
            if isinstance(cfg, dict) and cfg.get("enabled"):
                row = ctk.CTkFrame(self.plugin_frame, fg_color="transparent")
                row.pack(fill="x", pady=2)
                dot = ctk.CTkLabel(row, text="●", text_color=TEXT_DIM,
                                   font=("Arial", 14), width=20)
                dot.pack(side="left")
                ctk.CTkLabel(row, text=name, anchor="w",
                             text_color=TEXT_PRIMARY,
                             font=ctk.CTkFont(size=12)).pack(side="left", fill="x", expand=True)
                self.plugin_labels[name] = dot

        ctk.CTkLabel(
            sidebar, text="TRANSPORT",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_DIM, anchor="w",
        ).pack(fill="x", padx=15, pady=(20, 5))

        transport_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        transport_frame.pack(fill="x", padx=10)

        self.transport_dot = ctk.CTkLabel(transport_frame, text="●",
                                          text_color=TEXT_DIM, font=("Arial", 14), width=20)
        self.transport_dot.pack(side="left")
        self.transport_label = ctk.CTkLabel(
            transport_frame, text="—", anchor="w",
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12),
        )
        self.transport_label.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            sidebar, text="STATISTICHE",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_DIM, anchor="w",
        ).pack(fill="x", padx=15, pady=(20, 5))

        stats_frame = ctk.CTkFrame(sidebar, fg_color=BG_TERTIARY, corner_radius=6)
        stats_frame.pack(fill="x", padx=10, pady=5)

        self.stat_sent_label = ctk.CTkLabel(
            stats_frame, text="Inviate:  0", anchor="w",
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12),
        )
        self.stat_sent_label.pack(fill="x", padx=10, pady=(8, 2))

        self.stat_failed_label = ctk.CTkLabel(
            stats_frame, text="Fallite:  0", anchor="w",
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12),
        )
        self.stat_failed_label.pack(fill="x", padx=10, pady=2)

        self.stat_uptime_label = ctk.CTkLabel(
            stats_frame, text="Uptime:   —", anchor="w",
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12),
        )
        self.stat_uptime_label.pack(fill="x", padx=10, pady=(2, 8))

        ctk.CTkLabel(
            sidebar, text=f"v{APP_VERSION} • Lemon Loop Studio",
            text_color=TEXT_DIM, font=ctk.CTkFont(size=10),
        ).pack(side="bottom", pady=10)

    def _build_main_area(self, parent) -> None:
        main = ctk.CTkFrame(parent, fg_color=BG_PRIMARY, corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=1)

        tabview = ctk.CTkTabview(main, fg_color=BG_SECONDARY, corner_radius=8)
        tabview.grid(row=0, column=0, sticky="nsew")

        dash_tab = tabview.add("Dashboard")
        settings_tab = tabview.add("Impostazioni")
        test_tab = tabview.add("Test")
        fw_tab = tabview.add("Aggiornamenti")

        self._build_dashboard_tab(dash_tab)
        self._build_settings_tab(settings_tab)
        self._build_test_tab(test_tab)
        self._build_firmware_tab(fw_tab)

        tabview.set("Dashboard")
        self._tabview = tabview

    def _build_dashboard_tab(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=3)
        parent.grid_rowconfigure(1, weight=2)

        # ── LOG STREAM ──
        log_card = ctk.CTkFrame(parent, fg_color=BG_SECONDARY, corner_radius=8)
        log_card.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        log_card.grid_columnconfigure(0, weight=1)
        log_card.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            log_card, text="LOG STREAM",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_DIM, anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=15, pady=(10, 5))

        self.log_text = ctk.CTkTextbox(
            log_card, fg_color=BG_PRIMARY, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="none", corner_radius=6,
        )
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.log_text.configure(state="disabled")

        # ── NOTIFICHE RECENTI ──
        notif_card = ctk.CTkFrame(parent, fg_color=BG_SECONDARY, corner_radius=8)
        notif_card.grid(row=1, column=0, sticky="nsew")
        notif_card.grid_columnconfigure(0, weight=1)
        notif_card.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            notif_card, text="STORICO NOTIFICHE RECENTI",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_DIM, anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=15, pady=(10, 5))

        self.notif_scroll = ctk.CTkScrollableFrame(
            notif_card, fg_color=BG_PRIMARY, corner_radius=6,
        )
        self.notif_scroll.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.notif_scroll.grid_columnconfigure(0, weight=1)

        self._notif_placeholder = ctk.CTkLabel(
            self.notif_scroll,
            text="Nessuna notifica ancora. Avvia il motore con ▶ Start.",
            text_color=TEXT_DIM, font=ctk.CTkFont(size=11),
        )
        self._notif_placeholder.pack(pady=20)

    # ═══════════════════════════════════════════════════════════
    # Settings Tab
    # ═══════════════════════════════════════════════════════════

    def _build_settings_tab(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(parent, fg_color=BG_PRIMARY, corner_radius=0)
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        # Banner "riavvio richiesto" (hidden by default)
        self._running_banner = ctk.CTkLabel(
            scroll,
            text="⚠  Il motore è in esecuzione — riavvialo per applicare le modifiche.",
            fg_color="#5a3000", text_color=WARNING,
            font=ctk.CTkFont(size=12), corner_radius=6, anchor="w",
        )

        # Sezione Device
        self._build_section_header(scroll, "DEVICE")
        dev_frame = ctk.CTkFrame(scroll, fg_color=BG_TERTIARY, corner_radius=8)
        dev_frame.pack(fill="x", padx=10, pady=(0, 10))
        dev_frame.grid_columnconfigure(1, weight=1)

        device_cfg = self.config_data.get("device", {})
        device_fields = [
            ("ble_name",          "Nome BLE",          "text"),
            ("wifi_fallback_url", "WiFi Fallback URL",  "text"),
        ]
        for row_idx, (key, label, _) in enumerate(device_fields):
            sv = ctk.StringVar(value=str(device_cfg.get(key, "")))
            self._sv_device[key] = sv
            self._build_field_row(dev_frame, row_idx, label, sv, "text")

        # Sezione Plugin
        self._build_section_header(scroll, "PLUGIN")
        plugins_cfg = self.config_data.get("plugins", {})
        plugin_order = [
            "calendar", "discord", "gmail", "hacknplan",
            "telegram", "whatsapp",
            "slack", "github", "trello",
        ]
        for plugin_name in plugin_order:
            pcfg = plugins_cfg.get(plugin_name, {})
            self._build_plugin_card(scroll, plugin_name, pcfg)

        # Sezione Transport
        self._build_section_header(scroll, "TRANSPORT")
        transport_frame = ctk.CTkFrame(scroll, fg_color=BG_TERTIARY, corner_radius=8)
        transport_frame.pack(fill="x", padx=10, pady=(0, 10))
        transport_frame.grid_columnconfigure(1, weight=1)

        transport_cfg = self.config_data.get("transport", {})
        prefer_val = transport_cfg.get("prefer", "ble")
        self._sv_transport_prefer.set(prefer_val)
        ctk.CTkLabel(
            transport_frame, text="Modalità preferita", anchor="w",
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12), width=180,
        ).grid(row=0, column=0, padx=(12, 8), pady=8, sticky="w")
        ctk.CTkOptionMenu(
            transport_frame,
            values=["ble", "wifi", "auto"],
            variable=self._sv_transport_prefer,
            fg_color=BG_PRIMARY, button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(size=12),
        ).grid(row=0, column=1, padx=(0, 12), pady=8, sticky="w")

        timeout_val = str(transport_cfg.get("ble_scan_timeout_sec", 10))
        self._sv_transport_timeout.set(timeout_val)
        self._build_field_row(transport_frame, 1, "BLE scan timeout (sec)",
                              self._sv_transport_timeout, "int")

        # Sezione Logging
        self._build_section_header(scroll, "LOGGING")
        log_frame = ctk.CTkFrame(scroll, fg_color=BG_TERTIARY, corner_radius=8)
        log_frame.pack(fill="x", padx=10, pady=(0, 10))
        log_frame.grid_columnconfigure(1, weight=1)

        log_cfg = self.config_data.get("logging", {})
        self._sv_log_level.set(log_cfg.get("level", "INFO"))
        ctk.CTkLabel(
            log_frame, text="Livello log", anchor="w",
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12), width=180,
        ).grid(row=0, column=0, padx=(12, 8), pady=8, sticky="w")
        ctk.CTkOptionMenu(
            log_frame,
            values=["DEBUG", "INFO", "WARNING", "ERROR"],
            variable=self._sv_log_level,
            fg_color=BG_PRIMARY, button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(size=12),
        ).grid(row=0, column=1, padx=(0, 12), pady=8, sticky="w")

        # Feedback message label
        self._settings_msg_label = ctk.CTkLabel(
            scroll, text="", text_color=SUCCESS,
            font=ctk.CTkFont(size=12),
        )
        self._settings_msg_label.pack(padx=10, pady=(5, 0), anchor="w")

        # Buttons
        btn_row = ctk.CTkFrame(scroll, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=(5, 15))

        ctk.CTkButton(
            btn_row, text="💾  Salva", width=120,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._settings_save,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_row, text="↺  Ricarica", width=120,
            fg_color=BG_TERTIARY, hover_color="#444",
            font=ctk.CTkFont(size=13),
            command=self._settings_reload,
        ).pack(side="left")

    def _build_section_header(self, parent, title: str) -> None:
        ctk.CTkLabel(
            parent, text=title,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_DIM, anchor="w",
        ).pack(fill="x", padx=15, pady=(15, 5))

    def _build_field_row(self, parent, row_idx: int, label: str,
                         sv: ctk.StringVar, field_type: str) -> ctk.CTkEntry:
        ctk.CTkLabel(
            parent, text=label, anchor="w",
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12), width=180,
        ).grid(row=row_idx, column=0, padx=(12, 8), pady=6, sticky="w")

        show_char = "*" if field_type == "password" else ""
        entry = ctk.CTkEntry(
            parent, textvariable=sv,
            fg_color=BG_PRIMARY, border_color=BORDER,
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12),
            show=show_char,
        )
        entry.grid(row=row_idx, column=1, padx=(0, 12), pady=6, sticky="ew")
        return entry

    def _build_plugin_card(self, parent, plugin_name: str, pcfg: dict) -> None:
        enabled_val = bool(pcfg.get("enabled", False))
        bv = ctk.BooleanVar(value=enabled_val)
        fields_specs = _PLUGIN_FIELDS.get(plugin_name, [])

        plugin_vars: dict[str, ctk.Variable] = {"enabled": bv}
        self._sv_plugins[plugin_name] = plugin_vars

        card = ctk.CTkFrame(parent, fg_color=BG_TERTIARY, corner_radius=8)
        card.pack(fill="x", padx=10, pady=(0, 6))
        card.grid_columnconfigure(0, weight=1)

        # Header row with switch
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=8)
        header.grid_columnconfigure(0, weight=1)

        display_name = _PLUGIN_DISPLAY_NAME.get(plugin_name, plugin_name.capitalize())
        ctk.CTkLabel(
            header, text=display_name, anchor="w",
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        # Details frame (fields shown when enabled)
        if fields_specs:
            detail_frame = ctk.CTkFrame(card, fg_color="transparent")
            detail_frame.grid_columnconfigure(1, weight=1)
            self._plugin_detail_frames[plugin_name] = detail_frame

            for row_idx, (key, label, field_type) in enumerate(fields_specs):
                raw_val = pcfg.get(key)
                sv = ctk.StringVar(value=self._value_to_str(raw_val, field_type))
                plugin_vars[key] = sv
                self._build_field_row(detail_frame, row_idx, label, sv, field_type)

            def make_toggle(pname, dframe):
                def toggle(val):
                    if val:
                        dframe.grid(row=1, column=0, sticky="ew", padx=0, pady=(0, 8))
                    else:
                        dframe.grid_remove()
                return toggle

            toggle_fn = make_toggle(plugin_name, detail_frame)
            switch = ctk.CTkSwitch(
                header, text="", variable=bv,
                onvalue=True, offvalue=False,
                command=lambda fn=toggle_fn, b=bv: fn(b.get()),
                fg_color=BORDER, progress_color=ACCENT,
            )
            switch.grid(row=0, column=1, sticky="e")

            # Set initial visibility
            if enabled_val:
                detail_frame.grid(row=1, column=0, sticky="ew", padx=0, pady=(0, 8))
        else:
            # Plugin with no fields — just a switch
            switch = ctk.CTkSwitch(
                header, text="", variable=bv,
                onvalue=True, offvalue=False,
                fg_color=BORDER, progress_color=ACCENT,
            )
            switch.grid(row=0, column=1, sticky="e")

    # ═══════════════════════════════════════════════════════════
    # Settings persistence helpers
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _value_to_str(val, field_type: str) -> str:
        if val is None:
            return ""
        if field_type in ("list_int", "list_str"):
            if isinstance(val, list):
                return ", ".join(str(v) for v in val)
            return str(val)
        return str(val)

    @staticmethod
    def _parse_field_value(raw: str, field_type: str):
        raw = raw.strip()
        if field_type == "int":
            try:
                return int(raw)
            except ValueError:
                return 0
        if field_type == "int_nullable":
            if not raw:
                return None
            try:
                return int(raw)
            except ValueError:
                return None
        if field_type == "list_int":
            if not raw:
                return []
            result = []
            for part in raw.split(","):
                part = part.strip()
                if part:
                    try:
                        result.append(int(part))
                    except ValueError:
                        pass
            return result
        if field_type == "list_str":
            if not raw:
                return []
            return [part.strip().strip("\"'") for part in raw.split(",")
                    if part.strip().strip("\"'")]
        # text / password
        return raw

    def _settings_save(self) -> None:
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            self._show_settings_message(f"✗ Errore lettura config: {e}", ERROR)
            return

        # Device
        dev = raw.setdefault("device", {})
        for key, sv in self._sv_device.items():
            dev[key] = sv.get()

        # Plugins
        plugins_raw = raw.setdefault("plugins", {})
        for plugin_name, vars_dict in self._sv_plugins.items():
            pcfg = plugins_raw.setdefault(plugin_name, {})
            pcfg["enabled"] = vars_dict["enabled"].get()
            for key, var in vars_dict.items():
                if key == "enabled":
                    continue
                specs = _PLUGIN_FIELDS.get(plugin_name, [])
                field_spec = next((f for f in specs if f[0] == key), None)
                if field_spec:
                    pcfg[key] = self._parse_field_value(var.get(), field_spec[2])

        # Transport
        raw.setdefault("transport", {})["prefer"] = self._sv_transport_prefer.get()
        try:
            raw["transport"]["ble_scan_timeout_sec"] = int(self._sv_transport_timeout.get())
        except ValueError:
            pass

        # Logging
        raw.setdefault("logging", {})["level"] = self._sv_log_level.get()

        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(raw, f, indent=2, ensure_ascii=False)
            self.config_data = raw
        except Exception as e:
            self._show_settings_message(f"✗ Errore scrittura: {e}", ERROR)
            return

        if self.engine and self.engine.is_running():
            self._show_settings_message(
                "✓ Salvato. Riavvia il motore per applicare le modifiche.", WARNING
            )
        else:
            self._show_settings_message("✓ Salvato con successo.", SUCCESS)

    def _settings_reload(self) -> None:
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            self.config_data = raw
        except Exception as e:
            self._show_settings_message(f"✗ Errore lettura config: {e}", ERROR)
            return

        # Device
        device_cfg = raw.get("device", {})
        for key, sv in self._sv_device.items():
            sv.set(str(device_cfg.get(key, "")))

        # Plugins
        plugins_cfg = raw.get("plugins", {})
        for plugin_name, vars_dict in self._sv_plugins.items():
            pcfg = plugins_cfg.get(plugin_name, {})
            enabled = bool(pcfg.get("enabled", False))
            vars_dict["enabled"].set(enabled)

            # Update detail frame visibility
            dframe = self._plugin_detail_frames.get(plugin_name)
            if dframe:
                if enabled:
                    dframe.grid(row=1, column=0, sticky="ew", padx=0, pady=(0, 8))
                else:
                    dframe.grid_remove()

            for key, var in vars_dict.items():
                if key == "enabled":
                    continue
                specs = _PLUGIN_FIELDS.get(plugin_name, [])
                field_spec = next((f for f in specs if f[0] == key), None)
                if field_spec:
                    var.set(self._value_to_str(pcfg.get(key), field_spec[2]))

        # Transport
        transport_cfg = raw.get("transport", {})
        self._sv_transport_prefer.set(transport_cfg.get("prefer", "ble"))
        self._sv_transport_timeout.set(str(transport_cfg.get("ble_scan_timeout_sec", 10)))

        # Logging
        self._sv_log_level.set(raw.get("logging", {}).get("level", "INFO"))

        self._show_settings_message("↺ Configurazione ricaricata.", TEXT_PRIMARY)

    def _show_settings_message(self, msg: str, color: str) -> None:
        if self._settings_msg_label:
            self._settings_msg_label.configure(text=msg, text_color=color)
            self.after(5000, lambda: self._settings_msg_label.configure(text="")
                       if self._settings_msg_label else None)

    def _update_running_banner(self) -> None:
        if not self._running_banner:
            return
        if self.engine and self.engine.is_running():
            self._running_banner.pack(fill="x", padx=10, pady=(10, 0), before=None)
        else:
            try:
                self._running_banner.pack_forget()
            except Exception:
                pass

    # ═══════════════════════════════════════════════════════════
    # Test Console Tab
    # ═══════════════════════════════════════════════════════════

    def _build_test_tab(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        # ── Pannello controlli (top) ──
        ctrl = ctk.CTkFrame(parent, fg_color=BG_SECONDARY, corner_radius=8)
        ctrl.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ctrl.grid_columnconfigure(1, weight=1)

        seg_kw = dict(
            fg_color=BG_TERTIARY,
            selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            unselected_color=BG_TERTIARY,
            unselected_hover_color="#3a3a3e",
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=11),
        )

        # Source
        ctk.CTkLabel(ctrl, text="Source", anchor="w", width=90,
                     text_color=TEXT_DIM, font=ctk.CTkFont(size=11, weight="bold"),
                     ).grid(row=0, column=0, padx=(15, 8), pady=(14, 6), sticky="w")
        ctk.CTkSegmentedButton(
            ctrl, values=_TEST_SOURCES, variable=self._test_sv_source, **seg_kw,
        ).grid(row=0, column=1, padx=(0, 15), pady=(14, 6), sticky="ew")

        # Category — prima riga (4 voci)
        ctk.CTkLabel(ctrl, text="Category", anchor="w", width=90,
                     text_color=TEXT_DIM, font=ctk.CTkFont(size=11, weight="bold"),
                     ).grid(row=1, column=0, padx=(15, 8), pady=4, sticky="w")
        ctk.CTkSegmentedButton(
            ctrl, values=_TEST_CATEGORIES[:4], variable=self._test_sv_category, **seg_kw,
        ).grid(row=1, column=1, padx=(0, 15), pady=4, sticky="ew")

        # Category — seconda riga (4 voci)
        ctk.CTkLabel(ctrl, text="", width=90,
                     ).grid(row=2, column=0, padx=(15, 8), pady=4)
        self._test_cat_seg2 = ctk.CTkSegmentedButton(
            ctrl, values=_TEST_CATEGORIES[4:], variable=self._test_sv_category, **seg_kw,
        )
        self._test_cat_seg2.grid(row=2, column=1, padx=(0, 15), pady=4, sticky="ew")

        # Priority
        ctk.CTkLabel(ctrl, text="Priority", anchor="w", width=90,
                     text_color=TEXT_DIM, font=ctk.CTkFont(size=11, weight="bold"),
                     ).grid(row=3, column=0, padx=(15, 8), pady=4, sticky="w")
        ctk.CTkSegmentedButton(
            ctrl, values=_TEST_PRIORITIES, variable=self._test_sv_priority, **seg_kw,
        ).grid(row=3, column=1, padx=(0, 15), pady=4, sticky="w")

        # Preview text
        ctk.CTkLabel(ctrl, text="Preview", anchor="w", width=90,
                     text_color=TEXT_DIM, font=ctk.CTkFont(size=11, weight="bold"),
                     ).grid(row=4, column=0, padx=(15, 8), pady=4, sticky="w")
        self._test_preview_entry = ctk.CTkEntry(
            ctrl, placeholder_text="Testo anteprima (opzionale — lascia vuoto per auto)",
            fg_color=BG_PRIMARY, border_color=BORDER,
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12),
        )
        self._test_preview_entry.grid(row=4, column=1, padx=(0, 15), pady=4, sticky="ew")

        # Bottone Invia + messaggio feedback
        btn_row = ctk.CTkFrame(ctrl, fg_color="transparent")
        btn_row.grid(row=5, column=0, columnspan=2, padx=15, pady=(10, 14), sticky="w")

        self._test_send_btn = ctk.CTkButton(
            btn_row, text="▶  Invia notifica fake",
            width=180, height=36,
            fg_color=BG_TERTIARY, hover_color=BG_TERTIARY,
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
            command=self._on_test_send,
        )
        self._test_send_btn.pack(side="left", padx=(0, 12))

        self._test_feedback_lbl = ctk.CTkLabel(
            btn_row, text="Avvia il motore per abilitare l'invio.",
            text_color=TEXT_DIM, font=ctk.CTkFont(size=11),
        )
        self._test_feedback_lbl.pack(side="left")

        # ── Log invii test (bottom) ──
        log_card = ctk.CTkFrame(parent, fg_color=BG_SECONDARY, corner_radius=8)
        log_card.grid(row=1, column=0, sticky="nsew")
        log_card.grid_columnconfigure(0, weight=1)
        log_card.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            log_card, text="LOG INVII TEST",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_DIM, anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=15, pady=(10, 5))

        self._test_log = ctk.CTkTextbox(
            log_card,
            fg_color=BG_PRIMARY, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="none", corner_radius=6,
            state="disabled",
        )
        self._test_log.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

    def _on_test_send(self) -> None:
        if not self.engine or not self.engine.is_running():
            self._test_feedback_lbl.configure(
                text="⚠  Motore non in esecuzione.", text_color=WARNING
            )
            return

        source_key   = self._test_sv_source.get()
        category_key = self._test_sv_category.get()
        priority_key = self._test_sv_priority.get()

        source   = _TEST_SOURCE_MAP.get(source_key,   NotifSource.GENERIC)
        category = _TEST_CATEGORY_MAP.get(category_key, NotifCategory.ROUTINE)
        priority = _TEST_PRIORITY_MAP.get(priority_key, NotifPriority.NORMAL)

        raw_preview = self._test_preview_entry.get().strip()
        seed_text = raw_preview if raw_preview else f"[TEST] {source_key} — {category_key}"

        pkt = NotifPacket(
            source=source,
            priority=priority,
            category=category,
            seed_hash=compute_seed_hash(seed_text),
            seed_length=len(seed_text),
            timestamp=int(time.time()),
            seed_preview=seed_text,
        )

        self.engine.inject_notification(pkt)

        ts = datetime.now().strftime("%H:%M:%S")
        log_line = (
            f"{ts}  [{priority_key.upper():6}]  "
            f"{source_key:<10}  {category_key:<12}  \"{seed_text}\"\n"
        )
        self._test_log.configure(state="normal")
        self._test_log.insert("end", log_line)
        self._test_log.see("end")
        self._test_log.configure(state="disabled")

        self._test_feedback_lbl.configure(
            text=f"✓  Inviato: {source_key} / {category_key} / {priority_key}",
            text_color=SUCCESS,
        )
        self.after(4000, lambda: self._test_feedback_lbl.configure(
            text="", text_color=TEXT_DIM
        ) if self._test_feedback_lbl.winfo_exists() else None)

    # ═══════════════════════════════════════════════════════════
    # Firmware Tab
    # ═══════════════════════════════════════════════════════════

    def _build_firmware_tab(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(4, weight=1)

        # ── 1. Dispositivo (BLE scan + versione corrente) ──
        ble_card = ctk.CTkFrame(parent, fg_color=BG_SECONDARY, corner_radius=8)
        ble_card.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ble_card.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(
            ble_card, text="DISPOSITIVO",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_DIM, anchor="w",
        ).grid(row=0, column=0, columnspan=3, sticky="ew", padx=15, pady=(10, 6))

        self._fw_btn_scan = ctk.CTkButton(
            ble_card, text="🔍  Scansiona BLE", width=160,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, font=ctk.CTkFont(size=12),
            command=self._fw_on_scan,
        )
        self._fw_btn_scan.grid(row=1, column=0, padx=(12, 8), pady=(0, 10), sticky="w")

        self._fw_lbl_device_ver = ctk.CTkLabel(
            ble_card, text="Versione installata:  —",
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12), anchor="w",
        )
        self._fw_lbl_device_ver.grid(row=1, column=1, padx=4, pady=(0, 10), sticky="w")

        self._fw_lbl_status = ctk.CTkLabel(
            ble_card, text="", text_color=TEXT_DIM,
            font=ctk.CTkFont(size=12), anchor="w",
        )
        self._fw_lbl_status.grid(row=1, column=2, padx=(20, 12), pady=(0, 10), sticky="w")

        # ── 2. GitHub Releases ──
        gh_card = ctk.CTkFrame(parent, fg_color=BG_SECONDARY, corner_radius=8)
        gh_card.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        gh_card.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(
            gh_card, text="GITHUB RELEASES",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_DIM, anchor="w",
        ).grid(row=0, column=0, columnspan=4, sticky="ew", padx=15, pady=(10, 6))

        self._fw_btn_check_gh = ctk.CTkButton(
            gh_card, text="☁  Controlla aggiornamenti", width=200,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, font=ctk.CTkFont(size=12),
            command=self._fw_on_check_github,
        )
        self._fw_btn_check_gh.grid(row=1, column=0, padx=(12, 8), pady=(0, 10), sticky="w")

        self._fw_lbl_github_ver = ctk.CTkLabel(
            gh_card, text="Ultima release:  —",
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12), anchor="w",
        )
        self._fw_lbl_github_ver.grid(row=1, column=1, padx=4, pady=(0, 10), sticky="w")

        self._fw_btn_ota = ctk.CTkButton(
            gh_card, text="⚡  Scarica e installa via BLE", width=220,
            fg_color="#5a3a00", hover_color="#7a5010",
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
            command=self._fw_on_ota,
        )
        self._fw_btn_ota.grid(row=1, column=2, padx=(20, 12), pady=(0, 10), sticky="e")

        # ── 3. Companion App self-update ──
        app_card = ctk.CTkFrame(parent, fg_color=BG_SECONDARY, corner_radius=8)
        app_card.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        app_card.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(
            app_card, text="COMPANION APP",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_DIM, anchor="w",
        ).grid(row=0, column=0, columnspan=4, sticky="ew", padx=15, pady=(10, 6))

        self._app_lbl_version = ctk.CTkLabel(
            app_card, text=f"Versione installata:  v{APP_VERSION}",
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12), anchor="w",
        )
        self._app_lbl_version.grid(row=1, column=0, padx=(15, 12), pady=(0, 10), sticky="w")

        self._app_btn_check = ctk.CTkButton(
            app_card, text="☁  Controlla aggiornamenti", width=200,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, font=ctk.CTkFont(size=12),
            command=self._app_on_check,
        )
        self._app_btn_check.grid(row=1, column=1, padx=(0, 8), pady=(0, 10), sticky="w")

        self._app_lbl_release = ctk.CTkLabel(
            app_card, text="Ultima release:  —",
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12), anchor="w",
        )
        self._app_lbl_release.grid(row=1, column=2, padx=4, pady=(0, 10), sticky="w")

        self._app_btn_update = ctk.CTkButton(
            app_card, text="⬇  Aggiorna e riavvia", width=180,
            fg_color="#5a3a00", hover_color="#7a5010",
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
            command=self._app_on_update,
        )
        self._app_btn_update.grid(row=1, column=3, padx=(20, 12), pady=(0, 10), sticky="e")

        # ── 4. Progress bar (download + OTA transfer / app update) ──
        prog_card = ctk.CTkFrame(parent, fg_color=BG_SECONDARY, corner_radius=8)
        prog_card.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        prog_card.grid_columnconfigure(0, weight=1)

        self._fw_progressbar = ctk.CTkProgressBar(
            prog_card, fg_color=BG_TERTIARY, progress_color=ACCENT,
            height=14, corner_radius=6,
        )
        self._fw_progressbar.set(0)
        self._fw_progressbar.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))

        self._fw_lbl_progress = ctk.CTkLabel(
            prog_card, text="", text_color=TEXT_DIM,
            font=ctk.CTkFont(family="Consolas", size=11), anchor="w",
        )
        self._fw_lbl_progress.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))

        # ── 5. Log + USB fallback ──
        log_card = ctk.CTkFrame(parent, fg_color=BG_SECONDARY, corner_radius=8)
        log_card.grid(row=4, column=0, sticky="nsew")
        log_card.grid_columnconfigure(0, weight=1)
        log_card.grid_rowconfigure(2, weight=1)

        # Intestazione + USB fallback controls
        usb_row = ctk.CTkFrame(log_card, fg_color="transparent")
        usb_row.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        usb_row.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(
            usb_row, text="LOG",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_DIM, anchor="w", width=40,
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            usb_row, text="USB fallback:", anchor="w",
            text_color=TEXT_DIM, font=ctk.CTkFont(size=11), width=90,
        ).grid(row=0, column=1, padx=(20, 4), sticky="w")

        self._fw_port_menu = ctk.CTkOptionMenu(
            usb_row,
            values=self._fw_get_ports(),
            variable=self._fw_sv_port,
            fg_color=BG_PRIMARY, button_color=BG_TERTIARY,
            button_hover_color="#444",
            font=ctk.CTkFont(size=11), width=130,
        )
        self._fw_port_menu.grid(row=0, column=2, padx=(0, 4))

        ctk.CTkButton(
            usb_row, text="↺", width=28,
            fg_color=BG_TERTIARY, hover_color="#444",
            font=ctk.CTkFont(size=11),
            command=self._fw_refresh_ports,
        ).grid(row=0, column=3, padx=(0, 4), sticky="w")

        self._fw_btn_flash = ctk.CTkButton(
            usb_row, text="⚡ Flash USB", width=110,
            fg_color=BG_TERTIARY, hover_color="#555",
            font=ctk.CTkFont(size=11),
            command=self._fw_on_flash_usb,
        )
        self._fw_btn_flash.grid(row=0, column=4, padx=(4, 0))

        self._fw_log = ctk.CTkTextbox(
            log_card,
            fg_color=BG_PRIMARY, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="none", corner_radius=6, state="disabled",
        )
        self._fw_log.grid(row=2, column=0, sticky="nsew", padx=10, pady=(4, 10))

    # ── Firmware helpers ──────────────────────────────────────

    def _fw_get_ports(self) -> list[str]:
        ports = fw_upd.list_serial_ports()
        return ports if ports else ["—"]

    def _fw_refresh_ports(self) -> None:
        ports = self._fw_get_ports()
        if self._fw_port_menu:
            self._fw_port_menu.configure(values=ports)
        if ports and ports[0] != "—" and not self._fw_sv_port.get():
            self._fw_sv_port.set(ports[0])

    def _fw_update_status_label(self) -> None:
        if not self._fw_lbl_status:
            return
        dev = self._fw_device_ver
        gh = self._fw_github_info
        if dev is None:
            self._fw_lbl_status.configure(text="", text_color=TEXT_DIM)
            return
        if gh and gh.version > dev:
            self._fw_lbl_status.configure(
                text=f"⬆  v{dev} → v{gh.version}  (aggiornamento disponibile)",
                text_color=WARNING,
            )
        elif gh:
            self._fw_lbl_status.configure(
                text=f"✓  Firmware aggiornato (v{dev})",
                text_color=SUCCESS,
            )
        else:
            self._fw_lbl_status.configure(
                text=f"Versione corrente: v{dev}",
                text_color=TEXT_PRIMARY,
            )

    def _fw_log_append(self, text: str) -> None:
        if not self._fw_log:
            return
        self._fw_log.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        self._fw_log.insert("end", f"{ts}  {text}\n")
        self._fw_log.see("end")
        self._fw_log.configure(state="disabled")

    def _fw_set_progress(self, done: int, total: int, label: str = "") -> None:
        if self._fw_progressbar:
            ratio = done / total if total > 0 else 0.0
            self._fw_progressbar.set(ratio)
        if self._fw_lbl_progress:
            pct = int((done / total * 100)) if total > 0 else 0
            kb_done = done // 1024
            kb_total = total // 1024
            text = f"{label}  {kb_done} / {kb_total} KB  ({pct}%)" if label else ""
            self._fw_lbl_progress.configure(text=text)

    def _fw_buttons_lock(self, locked: bool) -> None:
        state = "disabled" if locked else "normal"
        for btn in (self._fw_btn_scan, self._fw_btn_check_gh, self._fw_btn_flash,
                    self._app_btn_check):
            if btn:
                btn.configure(state=state)
        if self._fw_btn_ota:
            if locked:
                self._fw_btn_ota.configure(state="disabled")
            else:
                # Riabilita OTA solo se c'è una release disponibile E il dispositivo è connesso
                can_ota = (
                    self._fw_ble_address is not None
                    and self._fw_github_info is not None
                )
                self._fw_btn_ota.configure(state="normal" if can_ota else "disabled")
        if self._app_btn_update:
            if locked:
                self._app_btn_update.configure(state="disabled")
            else:
                can_update = (
                    self._app_release_info is not None
                    and app_upd.is_update_available(self._app_release_info)
                )
                self._app_btn_update.configure(state="normal" if can_update else "disabled")

    # ── BLE Scan ─────────────────────────────────────────────

    def _fw_on_scan(self) -> None:
        self._fw_buttons_lock(True)
        self._fw_btn_scan.configure(text="Scansione...")
        self._fw_lbl_device_ver.configure(
            text="Versione installata:  ricerca in corso...", text_color=TEXT_DIM
        )
        self._fw_log_append("Scansione BLE avviata (timeout 10s)...")

        def run():
            loop = asyncio.new_event_loop()
            try:
                addr = loop.run_until_complete(fw_upd.scan_for_petcube(timeout=10.0))
                ver = loop.run_until_complete(fw_upd.read_fw_version_ble(addr)) if addr else None
                self.after(0, lambda: self._fw_scan_done(addr, ver))
            except Exception as e:
                self.after(0, lambda: self._fw_scan_done(None, None, str(e)))
            finally:
                loop.close()

        threading.Thread(target=run, daemon=True).start()

    def _fw_scan_done(self, addr: Optional[str], ver: Optional[int], err: str = "") -> None:
        self._fw_btn_scan.configure(text="🔍  Scansiona BLE")
        self._fw_buttons_lock(False)
        if err:
            self._fw_log_append(f"ERRORE: {err}")
            self._fw_lbl_device_ver.configure(
                text="Versione installata:  errore di scansione", text_color=ERROR
            )
            return
        self._fw_ble_address = addr
        self._fw_device_ver = ver
        if addr and ver is not None:
            self._fw_lbl_device_ver.configure(
                text=f"Versione installata:  v{ver}  ({addr})", text_color=SUCCESS
            )
            self._fw_log_append(f"PetCube trovato @ {addr}  —  FW v{ver}")
        elif addr:
            self._fw_lbl_device_ver.configure(
                text=f"Trovato @ {addr}  (versione non disponibile)", text_color=WARNING
            )
            self._fw_log_append(f"PetCube @ {addr} — caratteristica VERSION non presente (FW < v14?)")
        else:
            self._fw_lbl_device_ver.configure(
                text="Versione installata:  nessun PetCube trovato", text_color=ERROR
            )
            self._fw_log_append("Nessun dispositivo trovato. Assicurati che il PetCube sia in stato Idle.")
        self._fw_update_status_label()

    # ── GitHub check ─────────────────────────────────────────

    def _fw_on_check_github(self) -> None:
        self._fw_buttons_lock(True)
        self._fw_btn_check_gh.configure(text="Controllo...")
        self._fw_lbl_github_ver.configure(text="Ultima release:  connessione a GitHub...", text_color=TEXT_DIM)
        self._fw_log_append("Controllo release su GitHub...")

        cfg_fw = self.config_data.get("firmware", {})
        owner = cfg_fw.get("github_owner", "MikeAymeric")
        repo  = cfg_fw.get("github_repo",  "PetCube")

        def run():
            try:
                info = fw_upd.check_github_release(owner, repo)
                self.after(0, lambda: self._fw_github_done(info))
            except Exception as e:
                self.after(0, lambda: self._fw_github_done(None, str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _fw_github_done(self, info: Optional[fw_upd.FirmwareInfo], err: str = "") -> None:
        self._fw_btn_check_gh.configure(text="☁  Controlla aggiornamenti")
        self._fw_buttons_lock(False)
        self._fw_github_info = info
        if err:
            self._fw_lbl_github_ver.configure(
                text=f"Ultima release:  errore ({err})", text_color=ERROR
            )
            self._fw_log_append(f"GitHub non raggiungibile: {err}")
        elif info:
            self._fw_lbl_github_ver.configure(
                text=f"Ultima release:  {info.label()}", text_color=SUCCESS
            )
            self._fw_log_append(f"Trovata release {info.tag_name}  —  download_url pronto")
        else:
            self._fw_lbl_github_ver.configure(
                text="Ultima release:  nessun asset .bin trovato", text_color=WARNING
            )
            self._fw_log_append("Nessun asset .bin trovato nella release più recente.")
        self._fw_update_status_label()

    # ── BLE OTA ──────────────────────────────────────────────

    def _fw_on_ota(self) -> None:
        if not self._fw_ble_address:
            self._fw_log_append("⚠  Scansiona prima il dispositivo BLE.")
            return
        if not self._fw_github_info or not self._fw_github_info.download_url:
            self._fw_log_append("⚠  Controlla prima gli aggiornamenti su GitHub.")
            return

        self._fw_buttons_lock(True)
        self._fw_btn_ota.configure(text="In corso...", state="disabled")
        self._fw_set_progress(0, 1, "Avvio...")
        self._fw_log_append(f"Avvio OTA: {self._fw_github_info.label()}")

        addr = self._fw_ble_address
        info = self._fw_github_info

        def run():
            loop = asyncio.new_event_loop()
            try:
                # Step 1: download
                fw_dir = Path(self._fw_sv_fw_dir.get())
                fw_dir.mkdir(parents=True, exist_ok=True)
                bin_dest = fw_dir / f"petcube_{info.tag_name}.bin"

                self.after(0, lambda: self._fw_log_append(f"Download {info.tag_name}..."))

                def dl_progress(done, total):
                    self.after(0, lambda d=done, t=total: self._fw_set_progress(d, t, "Download"))

                fw_upd.download_firmware(info.download_url, bin_dest, progress_cb=dl_progress)
                self.after(0, lambda: self._fw_log_append(f"Download completato: {bin_dest.name}"))

                # Step 2: BLE OTA
                self.after(0, lambda: self._fw_set_progress(0, 1, "OTA transfer"))

                def ota_progress(done, total):
                    self.after(0, lambda d=done, t=total: self._fw_set_progress(d, t, "OTA transfer"))

                def ota_log(msg):
                    self.after(0, lambda m=msg: self._fw_log_append(m))

                ok = loop.run_until_complete(
                    fw_upd.ota_update_ble(addr, bin_dest, ota_progress, ota_log)
                )
                self.after(0, lambda: self._fw_ota_done(ok, info.version))
            except Exception as e:
                self.after(0, lambda: self._fw_ota_done(False, 0, str(e)))
            finally:
                loop.close()

        threading.Thread(target=run, daemon=True).start()

    def _fw_ota_done(self, ok: bool, new_ver: int, err: str = "") -> None:
        self._fw_btn_ota.configure(text="⚡  Scarica e installa via BLE")
        self._fw_buttons_lock(False)
        if err:
            self._fw_log_append(f"ERRORE OTA: {err}")
            self._fw_set_progress(0, 1, "")
        elif ok:
            self._fw_set_progress(1, 1, "Completato")
            self._fw_log_append("✓ OTA completata. Il dispositivo si sta riavviando con il nuovo firmware.")
            self._fw_device_ver = new_ver
            self._fw_lbl_device_ver.configure(
                text=f"Versione installata:  v{new_ver}  (aggiornato)", text_color=SUCCESS
            )
            self._fw_update_status_label()
        else:
            self._fw_set_progress(0, 1, "")
            self._fw_log_append("✗ OTA fallita. Controlla che il PetCube sia in stato Idle e riprova.")

    # ── USB fallback ─────────────────────────────────────────

    def _fw_on_flash_usb(self) -> None:
        port = self._fw_sv_port.get()
        if not port or port == "—":
            self._fw_log_append("⚠  Seleziona una porta COM per il flash USB.")
            return
        # Usa il .bin scaricato (se esiste) o cerca nella cartella locale
        fw_dir = Path(self._fw_sv_fw_dir.get())
        info = fw_upd.find_local_firmware(fw_dir)
        if not info or not info.bin_path:
            self._fw_log_append("⚠  Nessun .bin trovato. Controlla prima gli aggiornamenti da GitHub.")
            return

        self._fw_buttons_lock(True)
        self._fw_btn_flash.configure(text="Flashing...")
        self._fw_log_append(f"Flash USB: {info.bin_path.name} → {port}")

        def run():
            ok = fw_upd.flash_firmware_usb(
                bin_path=info.bin_path,
                port=port,
                log_cb=lambda m: self.after(0, lambda msg=m: self._fw_log_append(msg)),
            )
            self.after(0, lambda: self._fw_usb_done(ok, info.version))

        threading.Thread(target=run, daemon=True).start()

    def _fw_usb_done(self, ok: bool, new_ver: int) -> None:
        self._fw_btn_flash.configure(text="⚡ Flash USB")
        self._fw_buttons_lock(False)
        if ok:
            self._fw_log_append("✓ Flash USB completato. Riavvia manualmente il dispositivo.")
            self._fw_device_ver = new_ver
            self._fw_lbl_device_ver.configure(
                text=f"Versione installata:  v{new_ver}  (aggiornato)", text_color=SUCCESS
            )
            self._fw_update_status_label()
        else:
            self._fw_log_append("✗ Flash USB fallito.")

    # ── Companion App self-update ─────────────────────────────

    def _app_on_check(self) -> None:
        self._fw_buttons_lock(True)
        self._app_btn_check.configure(text="Controllo...")
        self._app_lbl_release.configure(text="Ultima release:  connessione a GitHub...", text_color=TEXT_DIM)
        self._fw_log_append("Controllo aggiornamenti companion su GitHub...")

        cfg_fw = self.config_data.get("firmware", {})
        owner = cfg_fw.get("github_owner", "MikeAymeric")
        repo  = cfg_fw.get("github_repo",  "PetCube")

        def run():
            try:
                info = app_upd.check_app_release(owner, repo)
                self.after(0, lambda: self._app_check_done(info))
            except Exception as e:
                self.after(0, lambda: self._app_check_done(None, str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _app_check_done(self, info: Optional[app_upd.AppReleaseInfo], err: str = "") -> None:
        self._app_btn_check.configure(text="☁  Controlla aggiornamenti")
        self._app_release_info = info
        self._fw_buttons_lock(False)

        if err:
            self._app_lbl_release.configure(text=f"Ultima release:  errore ({err})", text_color=ERROR)
            self._fw_log_append(f"GitHub non raggiungibile: {err}")
            return
        if not info:
            self._app_lbl_release.configure(
                text="Ultima release:  nessuna release 'companion-v*' trovata", text_color=WARNING
            )
            self._fw_log_append("Nessuna release companion compatibile trovata su GitHub.")
            return

        if app_upd.is_update_available(info):
            self._app_lbl_release.configure(
                text=f"Ultima release:  {info.label()}  —  aggiornamento disponibile!",
                text_color=WARNING,
            )
            self._fw_log_append(f"Nuova versione companion disponibile: v{info.version} (attuale v{APP_VERSION})")
        else:
            self._app_lbl_release.configure(
                text=f"Ultima release:  {info.label()}  —  già aggiornato",
                text_color=SUCCESS,
            )
            self._fw_log_append(f"Companion già aggiornata (v{APP_VERSION}).")

    def _app_on_update(self) -> None:
        info = self._app_release_info
        if not info or not app_upd.is_update_available(info):
            return

        self._fw_buttons_lock(True)
        self._app_btn_update.configure(text="Aggiornamento...")
        self._fw_log_append(f"Avvio aggiornamento companion: v{APP_VERSION} → v{info.version}")
        self._fw_set_progress(0, 1, "Download")

        def run():
            try:
                tmp_dir = Path("_companion_update")
                tmp_dir.mkdir(exist_ok=True)
                dest = tmp_dir / info.asset_name

                def dl_progress(done, total):
                    self.after(0, lambda d=done, t=total: self._fw_set_progress(d, t, "Download"))

                app_upd.download_update(info.download_url, dest, progress_cb=dl_progress)
                self.after(0, lambda: self._fw_log_append(f"Download completato: {dest.name}"))

                log_cb = lambda m: self.after(0, lambda msg=m: self._fw_log_append(msg))

                if info.is_exe:
                    app_upd.apply_exe_update_and_restart(dest, log_cb=log_cb)
                    self.after(0, lambda: self._app_update_done(True, restart_exe=True))
                else:
                    app_upd.apply_source_update(dest, Path("."), log_cb=log_cb)
                    self.after(0, lambda: self._app_update_done(True, restart_exe=False))
            except Exception as e:
                self.after(0, lambda: self._app_update_done(False, err=str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _app_update_done(self, ok: bool, restart_exe: bool = False, err: str = "") -> None:
        if not ok:
            self._app_btn_update.configure(text="⬇  Aggiorna e riavvia")
            self._fw_buttons_lock(False)
            self._fw_set_progress(0, 1, "")
            self._fw_log_append(f"✗ Aggiornamento fallito: {err}")
            return

        self._fw_set_progress(1, 1, "Completato")
        if restart_exe:
            self._fw_log_append("✓ Aggiornamento scaricato. L'app si riavvierà a breve...")
            self.after(1000, self._real_quit)
        else:
            self._fw_log_append("✓ Aggiornamento applicato. Riavvio dell'app...")
            self.after(500, app_upd.restart_from_source)

    # ═══════════════════════════════════════════════════════════
    # Engine control
    # ═══════════════════════════════════════════════════════════

    def _on_start(self) -> None:
        if self.engine and self.engine.is_running():
            return
        self.engine = CompanionEngine(self.config_data)
        self.engine.add_log_listener(self._on_log_record)
        self.engine.add_event_listener(self._on_event)
        self.engine.start()

        self.start_btn.configure(state="disabled", fg_color=BG_TERTIARY)
        self.stop_btn.configure(state="normal", fg_color="#a1260d", hover_color="#c14a3a")
        self.status_dot.configure(text_color=SUCCESS)
        self.status_text.configure(text="Running", text_color=SUCCESS)
        self._update_running_banner()
        if self._test_send_btn:
            self._test_send_btn.configure(state="normal", fg_color=ACCENT,
                                          hover_color=ACCENT_HOVER)

        self.after(500, self._update_status_periodic)

    def _on_stop(self) -> None:
        if not self.engine:
            return
        self._append_log_line("Arresto in corso...", color=WARNING)
        self.engine.stop(timeout=10.0)
        self.engine = None

        self.start_btn.configure(state="normal", fg_color=ACCENT, hover_color=ACCENT_HOVER)
        self.stop_btn.configure(state="disabled", fg_color=BG_TERTIARY)
        self.status_dot.configure(text_color=TEXT_DIM)
        self.status_text.configure(text="Stopped", text_color=TEXT_DIM)
        self._update_running_banner()
        if self._test_send_btn:
            self._test_send_btn.configure(state="disabled", fg_color=BG_TERTIARY,
                                          hover_color=BG_TERTIARY)

        for dot in self.plugin_labels.values():
            dot.configure(text_color=TEXT_DIM)
        self.transport_dot.configure(text_color=TEXT_DIM)
        self.transport_label.configure(text="—")

    # ═══════════════════════════════════════════════════════════
    # Event listeners (chiamati dal thread engine)
    # ═══════════════════════════════════════════════════════════

    def _on_log_record(self, record: logging.LogRecord) -> None:
        try:
            formatted = self._log_broadcaster_format(record)
            color = self._color_for_log_level(record.levelno)
            self.event_queue.put(("log", formatted, color))
        except Exception:
            pass

    def _on_event(self, pkt: NotifPacket, send_ok: bool) -> None:
        self.event_queue.put(("notif", pkt, send_ok))

    @staticmethod
    def _log_broadcaster_format(record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        return f"{ts} [{record.levelname}] {record.name} — {record.getMessage()}"

    @staticmethod
    def _color_for_log_level(levelno: int) -> str:
        if levelno >= logging.ERROR:
            return ERROR
        elif levelno >= logging.WARNING:
            return WARNING
        elif levelno >= logging.INFO:
            return TEXT_PRIMARY
        return TEXT_DIM

    # ═══════════════════════════════════════════════════════════
    # Main thread polling
    # ═══════════════════════════════════════════════════════════

    def _poll_event_queue(self) -> None:
        try:
            while True:
                item = self.event_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._append_log_line(item[1], color=item[2])
                elif kind == "notif":
                    self._append_notification(item[1], item[2])
        except queue.Empty:
            pass
        finally:
            self.after(100, self._poll_event_queue)

    def _append_log_line(self, text: str, color: str = TEXT_PRIMARY) -> None:
        self.log_text.configure(state="normal")
        tag_name = f"col_{color.lstrip('#')}"
        try:
            self.log_text.tag_config(tag_name, foreground=color)
        except Exception:
            pass
        self.log_text.insert("end", text + "\n", tag_name)
        self.log_text.see("end")
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > 1000:
            self.log_text.delete("1.0", "200.0")
        self.log_text.configure(state="disabled")

    def _append_notification(self, pkt: NotifPacket, send_ok: bool) -> None:
        if self._notif_placeholder and self._notif_placeholder.winfo_exists():
            self._notif_placeholder.destroy()
            self._notif_placeholder = None

        row = ctk.CTkFrame(self.notif_scroll, fg_color=BG_TERTIARY, corner_radius=4)
        row.pack(fill="x", pady=2, padx=2, side="top", anchor="n")
        row.grid_columnconfigure(3, weight=1)

        status_color = SUCCESS if send_ok else ERROR
        ctk.CTkLabel(
            row, text="✓" if send_ok else "✗", text_color=status_color,
            font=ctk.CTkFont(size=14, weight="bold"), width=25,
        ).grid(row=0, column=0, padx=(8, 4), pady=6)

        ts = datetime.fromtimestamp(pkt.timestamp).strftime("%H:%M:%S")
        ctk.CTkLabel(
            row, text=ts, text_color=TEXT_DIM,
            font=ctk.CTkFont(family="Consolas", size=11), width=70,
        ).grid(row=0, column=1, padx=4)

        source_label = SOURCE_LABEL.get(pkt.source, "?")
        ctk.CTkLabel(
            row, text=source_label, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=11, weight="bold"), width=110, anchor="w",
        ).grid(row=0, column=2, padx=4, sticky="w")

        preview_text = pkt.seed_preview[:60] + ("..." if len(pkt.seed_preview) > 60 else "")
        ctk.CTkLabel(
            row, text=preview_text, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=11), anchor="w",
        ).grid(row=0, column=3, padx=4, sticky="ew")

        cat_label = CATEGORY_LABEL.get(pkt.category, "?")
        pri_color = ERROR if pkt.priority == NotifPriority.HIGH else (
            WARNING if pkt.priority == NotifPriority.NORMAL else TEXT_DIM
        )
        ctk.CTkLabel(
            row, text=f" {cat_label} ", text_color=pri_color,
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color=BG_PRIMARY, corner_radius=4, width=90,
        ).grid(row=0, column=4, padx=(4, 8))

        self.recent_notifications.append({"pkt": pkt, "ok": send_ok, "ts": pkt.timestamp})
        if len(self.recent_notifications) > 100:
            self.recent_notifications.pop(0)
            children = self.notif_scroll.winfo_children()
            if children:
                children[0].destroy()

    def _update_status_periodic(self) -> None:
        if not self.engine or not self.engine.is_running():
            if self.engine is not None:
                self._append_log_line("⚠️  Engine terminato inaspettatamente.", color=ERROR)
                self.engine = None
                self.start_btn.configure(state="normal", fg_color=ACCENT,
                                         hover_color=ACCENT_HOVER)
                self.stop_btn.configure(state="disabled", fg_color=BG_TERTIARY)
                self.status_dot.configure(text_color=ERROR)
                self.status_text.configure(text="Crashed", text_color=ERROR)
                self._update_running_banner()
                if self._test_send_btn:
                    self._test_send_btn.configure(state="disabled", fg_color=BG_TERTIARY,
                                                  hover_color=BG_TERTIARY)
                for dot in self.plugin_labels.values():
                    dot.configure(text_color=TEXT_DIM)
                self.transport_dot.configure(text_color=TEXT_DIM)
                self.transport_label.configure(text="—")
            return

        status = self.engine.get_status()

        active_set = set(status.plugins_active)
        for name, dot in self.plugin_labels.items():
            dot.configure(text_color=SUCCESS if name in active_set else WARNING)

        mode = status.sender_mode
        if mode == "ble":
            self.transport_dot.configure(text_color=SUCCESS)
            self.transport_label.configure(text="BLE")
        elif mode == "mock":
            self.transport_dot.configure(text_color=WARNING)
            self.transport_label.configure(text="MOCK (no real BLE)")
        elif mode == "wifi":
            self.transport_dot.configure(text_color=SUCCESS)
            self.transport_label.configure(text="WiFi HTTP")
        else:
            self.transport_dot.configure(text_color=TEXT_DIM)
            self.transport_label.configure(text=mode)

        self.stat_sent_label.configure(text=f"Inviate:  {status.notifications_sent}")
        self.stat_failed_label.configure(text=f"Fallite:  {status.notifications_failed}")
        if status.started_at:
            uptime_sec = int(time.time() - status.started_at)
            h, rem = divmod(uptime_sec, 3600)
            m, s = divmod(rem, 60)
            self.stat_uptime_label.configure(text=f"Uptime:   {h:02d}:{m:02d}:{s:02d}")

        self.after(500, self._update_status_periodic)

    # ═══════════════════════════════════════════════════════════
    # Window close & Tray
    # ═══════════════════════════════════════════════════════════

    def _on_close_window(self) -> None:
        if HAS_TRAY and self.tray_icon:
            self.withdraw()
            self._tray_visible = False
        else:
            self._real_quit()

    def _real_quit(self) -> None:
        if self.engine:
            self.engine.stop(timeout=5.0)
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.destroy()

    def _setup_tray(self) -> None:
        img = Image.new("RGB", (64, 64), color=(30, 30, 30))
        d = ImageDraw.Draw(img)
        d.ellipse([10, 10, 54, 54], fill=(78, 201, 176))
        d.text((23, 21), "PC", fill=(30, 30, 30))

        def on_show(icon, item):
            self.deiconify()
            self.lift()
            self.focus_force()
            self._tray_visible = True

        def on_quit(icon, item):
            self.after(0, self._real_quit)

        menu = pystray.Menu(
            pystray.MenuItem("Show", on_show, default=True),
            pystray.MenuItem("Quit", on_quit),
        )
        self.tray_icon = pystray.Icon("PetCubeCompanion", img, "PetCube Companion", menu)
        import threading as _th
        _th.Thread(target=self.tray_icon.run, daemon=True).start()


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    try:
        config = load_config(Path("config.json"))
    except FileNotFoundError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    app = CompanionGUI(config)
    app.mainloop()


if __name__ == "__main__":
    main()
