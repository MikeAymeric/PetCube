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
import socket
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Optional

from playwright_env import setup_playwright_browsers_path
setup_playwright_browsers_path()

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
import achievements as achv
import pomodoro_history as pomo_history
import setup_wizard
import valhalla as vlh
from valhalla_online import ValhallaBattleClient
from config_schema import (
    PLUGIN_FIELDS as _PLUGIN_FIELDS,
    PLUGIN_DISPLAY_NAME as _PLUGIN_DISPLAY_NAME,
    PLUGIN_ORDER,
    value_to_str as _value_to_str_impl,
    parse_field_value as _parse_field_value_impl,
    generate_device_id,
    device_tag,
)
from version import APP_VERSION
from notification_packet import (
    NotifPacket, NotifSource, NotifCategory, NotifPriority,
    compute_seed_hash,
)


# ── Single instance lock ────────────────────────────────────────
# Usiamo un socket TCP su localhost come mutex: se il bind fallisce
# un'altra istanza è già in esecuzione. Il socket si libera da solo
# alla chiusura del processo (anche in caso di crash), niente lock
# file da pulire manualmente.
_SINGLE_INSTANCE_PORT = 47591


def _acquire_single_instance_lock() -> Optional[socket.socket]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", _SINGLE_INSTANCE_PORT))
    except OSError:
        sock.close()
        return None
    sock.listen(1)
    return sock


def _notify_existing_instance() -> None:
    """Chiede all'istanza già in esecuzione di portarsi in primo piano."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(("127.0.0.1", _SINGLE_INSTANCE_PORT))
            s.sendall(b"show")
    except OSError:
        pass


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


class PomodoroChart(ctk.CTkFrame):
    """Grafico a barre dello storico sessioni Pomodoro completate
    (ultimi N giorni), aggiornato ad ogni sincronizzazione BLE."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.canvas = tk.Canvas(self, bg=BG_PRIMARY, highlightthickness=0, height=110)
        self.canvas.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self._history: list[tuple[str, int]] = []

    def draw(self, history: list[tuple[str, int]]) -> None:
        self._history = history
        self._redraw()

    def _redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        if not self._history:
            return
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 10 or h < 10:
            return

        n = len(self._history)
        max_count = max((count for _, count in self._history), default=0) or 1
        margin_bottom = 16
        margin_top = 14
        usable_h = h - margin_bottom - margin_top
        bar_w = w / n

        for i, (day_iso, count) in enumerate(self._history):
            x0 = i * bar_w + bar_w * 0.15
            x1 = (i + 1) * bar_w - bar_w * 0.15
            bar_h = (count / max_count) * usable_h if count else 0
            y1 = h - margin_bottom
            y0 = y1 - bar_h
            color = SUCCESS if count > 0 else BORDER
            if count > 0:
                c.create_rectangle(x0, y0, x1, y1, fill=color, outline="")
                c.create_text((x0 + x1) / 2, y0 - 7, text=str(count),
                               fill=TEXT_PRIMARY, font=("Arial", 9))
            else:
                c.create_line(x0, y1, x1, y1, fill=color, width=2)
            day_label = datetime.strptime(day_iso, "%Y-%m-%d").strftime("%d/%m")
            c.create_text((x0 + x1) / 2, h - margin_bottom / 2, text=day_label,
                           fill=TEXT_DIM, font=("Arial", 8))


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

        # Konami code easter egg
        self._konami_unlocked: bool = False
        self._konami_idx: int = 0
        self._vlh_test_section = None   # CTkFrame rivelato dal Konami
        self._vlh_test_el_var  = ctk.StringVar(value="Fire")
        self._vlh_test_evo_var = ctk.StringVar(value="3")
        self._vlh_test_line_var = ctk.StringVar(value="STR")
        self._vlh_test_fv_var  = ctk.StringVar(value="STD")

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
        self._fw_btn_reset: Optional[ctk.CTkButton] = None
        self._fw_btn_flash: Optional[ctk.CTkButton] = None
        self._fw_progressbar: Optional[ctk.CTkProgressBar] = None
        self._fw_lbl_progress: Optional[ctk.CTkLabel] = None
        self._fw_log: Optional[ctk.CTkTextbox] = None
        self._fw_port_menu: Optional[ctk.CTkOptionMenu] = None

        # Dashboard: grafico storico sessioni Pomodoro
        self._pomo_chart: Optional["PomodoroChart"] = None

        # Achievements tab state
        self._achv_mask: int = achv.load_cached_mask()
        self._achv_rows: dict[int, dict] = {}
        self._achv_lbl_progress: Optional[ctk.CTkLabel] = None
        self._achv_lbl_status: Optional[ctk.CTkLabel] = None
        self._achv_btn_refresh: Optional[ctk.CTkButton] = None
        self._achv_btn_reset: Optional[ctk.CTkButton] = None

        # Valhalla tab state
        self._vlh_entries: list[vlh.ValhallaEntry] = vlh.load_valhalla()
        self._vlh_selected: Optional[int] = None       # index in _vlh_entries
        self._vlh_sprites: list[dict] = []             # canvas sprite state
        self._vlh_canvas: Optional[tk.Canvas] = None
        self._vlh_detail_frame: Optional[ctk.CTkFrame] = None
        self._vlh_detail_win: Optional[ctk.CTkToplevel] = None
        self._vlh_battle_client: Optional[ValhallaBattleClient] = None
        self._vlh_polling_started = False

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
        self.bind_all("<KeyPress>", self._on_konami_key)
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
        self.stop_btn.grid(row=0, column=4, padx=5, pady=10)

        self.sync_now_btn = ctk.CTkButton(
            header, text="🔄 Sincronizza ora", width=140,
            fg_color=BG_TERTIARY, hover_color="#444",
            command=self._on_sync_now,
        )
        self.sync_now_btn.grid(row=0, column=5, padx=(5, 15), pady=10)

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
        achv_tab = tabview.add("Achievements")
        vlh_tab = tabview.add("Valhalla")
        fw_tab = tabview.add("Aggiornamenti")

        self._build_dashboard_tab(dash_tab)
        self._build_settings_tab(settings_tab)
        self._build_test_tab(test_tab)
        self._build_achievements_tab(achv_tab)
        self._build_valhalla_tab(vlh_tab)
        self._build_firmware_tab(fw_tab)

        tabview.set("Dashboard")
        self._tabview = tabview

    def _build_dashboard_tab(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=3)
        parent.grid_rowconfigure(1, weight=2)
        parent.grid_rowconfigure(2, weight=0)

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

        # ── STORICO SESSIONI POMODORO ──
        pomo_card = ctk.CTkFrame(parent, fg_color=BG_SECONDARY, corner_radius=8)
        pomo_card.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        pomo_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            pomo_card, text="SESSIONI POMODORO (ultimi 14 giorni)",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_DIM, anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=15, pady=(10, 5))

        self._pomo_chart = PomodoroChart(pomo_card, fg_color=BG_PRIMARY, corner_radius=6)
        self._pomo_chart.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        self._pomo_chart.draw(pomo_history.get_recent_history())

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
            ("username",          "Username",          "text"),
            ("wifi_fallback_url", "WiFi Fallback URL",  "text"),
        ]
        for row_idx, (key, label, _) in enumerate(device_fields):
            sv = ctk.StringVar(value=str(device_cfg.get(key, "")))
            self._sv_device[key] = sv
            self._build_field_row(dev_frame, row_idx, label, sv, "text")

        self._device_id = device_cfg.get("device_id") or generate_device_id()
        self._tag_label = ctk.CTkLabel(
            dev_frame, text="", anchor="w",
            text_color=TEXT_DIM, font=ctk.CTkFont(size=11),
        )
        self._tag_label.grid(row=len(device_fields), column=0, columnspan=2,
                              padx=12, pady=(0, 8), sticky="w")
        self._sv_device["username"].trace_add("write", lambda *_: self._update_tag_label())
        self._update_tag_label()

        # Sezione Display (luminosità schermo, FW >= v30)
        self._build_section_header(scroll, "DISPLAY")
        display_frame = ctk.CTkFrame(scroll, fg_color=BG_TERTIARY, corner_radius=8)
        display_frame.pack(fill="x", padx=10, pady=(0, 10))
        display_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            display_frame, text="Luminosità schermo", anchor="w",
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12), width=180,
        ).grid(row=0, column=0, padx=(12, 8), pady=8, sticky="w")

        self._sv_brightness = ctk.IntVar(value=255)
        self._brightness_value_label = ctk.CTkLabel(
            display_frame, text="255", width=40,
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12),
        )

        def _on_brightness_slide(value):
            self._brightness_value_label.configure(text=str(int(float(value))))

        self._brightness_slider = ctk.CTkSlider(
            display_frame, from_=10, to=255, number_of_steps=49,
            variable=self._sv_brightness, command=_on_brightness_slide,
            fg_color=BG_PRIMARY, progress_color=ACCENT, button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
        )
        self._brightness_slider.grid(row=0, column=1, padx=8, pady=8, sticky="ew")
        self._brightness_value_label.grid(row=0, column=2, padx=(0, 8), pady=8)

        self._brightness_btn_apply = ctk.CTkButton(
            display_frame, text="Applica al PetCube", width=160,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._on_apply_brightness,
        )
        self._brightness_btn_apply.grid(row=1, column=0, columnspan=2, padx=12, pady=(0, 4), sticky="w")

        self._brightness_status_label = ctk.CTkLabel(
            display_frame, text="", anchor="w",
            text_color=TEXT_DIM, font=ctk.CTkFont(size=11),
        )
        self._brightness_status_label.grid(row=2, column=0, columnspan=3, padx=12, pady=(0, 8), sticky="w")

        # Sezione Plugin
        self._build_section_header(scroll, "PLUGIN")
        plugins_cfg = self.config_data.get("plugins", {})
        for plugin_name in PLUGIN_ORDER:
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

        # Sezione Valhalla
        self._build_section_header(scroll, "VALHALLA")
        vlh_frame = ctk.CTkFrame(scroll, fg_color=BG_TERTIARY, corner_radius=8)
        vlh_frame.pack(fill="x", padx=10, pady=(0, 10))
        vlh_frame.grid_columnconfigure(1, weight=1)

        vlh_cfg = self.config_data.get("valhalla", {})
        sv_firebase = ctk.StringVar(value=str(vlh_cfg.get("firebase_url", "")))
        self._sv_valhalla_firebase = sv_firebase
        self._build_field_row(vlh_frame, 0, "Firebase URL", sv_firebase, "text")

        ctk.CTkLabel(
            vlh_frame,
            text="URL del tuo Firebase Realtime Database (es. https://mio-db.firebaseio.com) "
                 "per le battaglie online nel Valhalla.",
            text_color=TEXT_DIM, font=ctk.CTkFont(size=10), wraplength=500, anchor="w",
        ).grid(row=1, column=0, columnspan=2, padx=(12, 8), pady=(0, 8), sticky="w")

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
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_row, text="🧙  Wizard di configurazione", width=210,
            fg_color=BG_TERTIARY, hover_color="#444",
            font=ctk.CTkFont(size=13),
            command=self._open_setup_wizard,
        ).pack(side="left")

    def _update_tag_label(self) -> None:
        tag = device_tag(self._sv_device["username"].get(), self._device_id)
        self._tag_label.configure(text=f"ID PetCube: {tag}")

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

    _value_to_str = staticmethod(_value_to_str_impl)
    _parse_field_value = staticmethod(_parse_field_value_impl)

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
        dev["device_id"] = dev.get("device_id") or self._device_id
        self._device_id = dev["device_id"]
        self._update_tag_label()

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

        # Valhalla
        if hasattr(self, "_sv_valhalla_firebase"):
            raw.setdefault("valhalla", {})["firebase_url"] = self._sv_valhalla_firebase.get().strip()

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
        self._device_id = device_cfg.get("device_id") or generate_device_id()
        self._update_tag_label()

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

        # Valhalla
        if hasattr(self, "_sv_valhalla_firebase"):
            self._sv_valhalla_firebase.set(raw.get("valhalla", {}).get("firebase_url", ""))

        self._show_settings_message("↺ Configurazione ricaricata.", TEXT_PRIMARY)

    def _open_setup_wizard(self) -> None:
        def on_done(new_config: dict) -> None:
            self.config_data = new_config
            self._settings_reload()
            self._show_settings_message("✓ Configurazione aggiornata dal wizard.", SUCCESS)

        setup_wizard.open_wizard(self, self.config_data, on_done)

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

        # ── Sezione segreta Valhalla (rivelata dal Konami code) ──
        parent.grid_rowconfigure(2, weight=0)
        sec = ctk.CTkFrame(parent, fg_color="#1a1a2e", corner_radius=8,
                           border_width=1, border_color="#7b2d8b")
        self._vlh_test_section = sec
        # NON chiamare .grid() qui — viene mostrata solo dopo il Konami code

        sec.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            sec, text="☠  GENERATORE VALHALLA  ☠",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#d4af37", anchor="w",
        ).grid(row=0, column=0, columnspan=2, padx=15, pady=(12, 8), sticky="w")

        seg_kw2 = dict(
            fg_color="#2a1a3e",
            selected_color="#7b2d8b", selected_hover_color="#9b3dab",
            unselected_color="#2a1a3e", unselected_hover_color="#3a2a4e",
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=11),
        )

        ctk.CTkLabel(sec, text="Elemento", anchor="w", width=90,
                     text_color=TEXT_DIM, font=ctk.CTkFont(size=11, weight="bold"),
                     ).grid(row=1, column=0, padx=(15, 8), pady=4, sticky="w")
        ctk.CTkSegmentedButton(
            sec, values=["Fire", "Water"],
            variable=self._vlh_test_el_var, **seg_kw2,
        ).grid(row=1, column=1, padx=(0, 15), pady=4, sticky="w")

        ctk.CTkLabel(sec, text="Evo Stage", anchor="w", width=90,
                     text_color=TEXT_DIM, font=ctk.CTkFont(size=11, weight="bold"),
                     ).grid(row=2, column=0, padx=(15, 8), pady=4, sticky="w")
        ctk.CTkSegmentedButton(
            sec, values=["0","1","2","3","4","5"],
            variable=self._vlh_test_evo_var, **seg_kw2,
        ).grid(row=2, column=1, padx=(0, 15), pady=4, sticky="w")

        ctk.CTkLabel(sec, text="Linea", anchor="w", width=90,
                     text_color=TEXT_DIM, font=ctk.CTkFont(size=11, weight="bold"),
                     ).grid(row=3, column=0, padx=(15, 8), pady=4, sticky="w")
        ctk.CTkSegmentedButton(
            sec, values=["STR", "ENG", "INT"],
            variable=self._vlh_test_line_var, **seg_kw2,
        ).grid(row=3, column=1, padx=(0, 15), pady=4, sticky="w")

        ctk.CTkLabel(sec, text="Variante", anchor="w", width=90,
                     text_color=TEXT_DIM, font=ctk.CTkFont(size=11, weight="bold"),
                     ).grid(row=4, column=0, padx=(15, 8), pady=4, sticky="w")
        ctk.CTkSegmentedButton(
            sec, values=["STD", "Light", "Dark"],
            variable=self._vlh_test_fv_var, **seg_kw2,
        ).grid(row=4, column=1, padx=(0, 15), pady=4, sticky="w")

        ctk.CTkButton(
            sec, text="💀  Spawna nel Valhalla",
            width=200, height=36,
            fg_color="#7b2d8b", hover_color="#9b3dab",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._on_vlh_test_spawn,
        ).grid(row=5, column=0, columnspan=2, padx=15, pady=(10, 14), sticky="w")

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

    # ── Konami code easter egg ───────────────────────────────────

    # ↑↑↓↓←→←→BA + Enter (Enter sostituisce Start, che non esiste su tastiera)
    _KONAMI_SEQ = ["Up","Up","Down","Down","Left","Right","Left","Right","b","a","Return"]

    def _on_konami_key(self, event) -> None:
        expected = self._KONAMI_SEQ[self._konami_idx]
        if event.keysym == expected:
            self._konami_idx += 1
            if self._konami_idx == len(self._KONAMI_SEQ):
                self._konami_idx = 0
                self._vlh_konami_unlock()
        else:
            # Se il tasto sbagliato è lo stesso con cui inizia la sequenza,
            # conta già come primo passo (es. ↑↑↑ non azzera dopo il terzo ↑)
            self._konami_idx = 1 if event.keysym == self._KONAMI_SEQ[0] else 0

    def _vlh_konami_unlock(self) -> None:
        if self._vlh_test_section is None:
            return
        if not self._konami_unlocked:
            self._konami_unlocked = True
            self._vlh_test_section.grid(
                row=2, column=0, sticky="ew", pady=(10, 0)
            )
        # Flash del bordo per feedback visivo
        def _flash(color: str, times: int) -> None:
            if not self._vlh_test_section.winfo_exists():
                return
            self._vlh_test_section.configure(border_color=color)
            if times > 0:
                self.after(150, lambda: _flash(
                    "#d4af37" if color == "#7b2d8b" else "#7b2d8b", times - 1
                ))
        _flash("#d4af37", 5)

    def _on_vlh_test_spawn(self) -> None:
        import random, time as _time
        el       = self._vlh_test_el_var.get()
        evo      = int(self._vlh_test_evo_var.get())
        line_map = {"STR": 0, "ENG": 1, "INT": 2}
        fv_map   = {"STD": 0, "Light": 1, "Dark": 2}
        lv       = line_map[self._vlh_test_line_var.get()]
        fv       = fv_map[self._vlh_test_fv_var.get()] if evo >= 5 else -1

        # Stats proporzionali all'evo stage + rumore random
        base = 20 + evo * 12
        entry = vlh.ValhallaEntry(
            element=el,
            evo_stage=evo,
            line_variant=lv,
            final_variant=fv,
            stat_str=min(99, base + random.randint(-10, 20)),
            stat_int=min(99, base + random.randint(-10, 20)),
            stat_eng=min(99, base + random.randint(-10, 20)),
            stat_hap=min(100, base + random.randint(-5, 15)),
            sessions=random.randint(10 + evo * 5, 50 + evo * 20),
            battles_won=random.randint(0, 10 + evo * 3),
            battles_lost=random.randint(0, 8 + evo * 2),
            deaths_total=vlh.load_deaths_cache() + 1,
            owner=self.config_data.get("device", {}).get("username", "") or "TEST",
            death_timestamp=_time.time(),
        )
        vlh.add_entry(entry)
        vlh.save_deaths_cache(entry.deaths_total)

        # Ricarica il canvas Valhalla
        self._vlh_entries = vlh.load_valhalla()
        self._vlh_init_sprites()
        self._vlh_lbl_count.configure(text=f"{len(self._vlh_entries)} creature")

        # Log in test console
        ts = datetime.now().strftime("%H:%M:%S")
        log_line = f"{ts}  [VALHALLA]  Spawned: {entry.name} (evo={evo} {el})  stats={entry.stat_str}/{entry.stat_int}/{entry.stat_eng}/{entry.stat_hap}\n"
        if self._test_log:
            self._test_log.configure(state="normal")
            self._test_log.insert("end", log_line)
            self._test_log.see("end")
            self._test_log.configure(state="disabled")

    # ═══════════════════════════════════════════════════════════
    # Achievements Tab
    # ═══════════════════════════════════════════════════════════

    def _build_achievements_tab(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        # ── Header: refresh button + progress ──
        header_card = ctk.CTkFrame(parent, fg_color=BG_SECONDARY, corner_radius=8)
        header_card.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        header_card.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(
            header_card, text="ACHIEVEMENTS",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_DIM, anchor="w",
        ).grid(row=0, column=0, columnspan=4, sticky="ew", padx=15, pady=(10, 6))

        self._achv_btn_refresh = ctk.CTkButton(
            header_card, text="🔄  Aggiorna via BLE", width=180,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, font=ctk.CTkFont(size=12),
            command=self._achv_on_refresh,
        )
        self._achv_btn_refresh.grid(row=1, column=0, padx=(12, 8), pady=(0, 10), sticky="w")

        self._achv_lbl_progress = ctk.CTkLabel(
            header_card, text="", text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=13, weight="bold"), anchor="w",
        )
        self._achv_lbl_progress.grid(row=1, column=1, padx=4, pady=(0, 10), sticky="w")

        self._achv_lbl_status = ctk.CTkLabel(
            header_card, text="", text_color=TEXT_DIM,
            font=ctk.CTkFont(size=12), anchor="w",
        )
        self._achv_lbl_status.grid(row=1, column=2, padx=(20, 8), pady=(0, 10), sticky="e")

        self._achv_btn_reset = ctk.CTkButton(
            header_card, text="🗑  Reset achievements", width=180,
            fg_color="#5a1a1a", hover_color="#7a2a2a", font=ctk.CTkFont(size=12),
            command=self._achv_on_reset,
        )
        self._achv_btn_reset.grid(row=1, column=3, padx=(0, 12), pady=(0, 10), sticky="e")

        # ── Lista achievement, raggruppati per categoria ──
        scroll = ctk.CTkScrollableFrame(parent, fg_color=BG_PRIMARY, corner_radius=0)
        scroll.grid(row=1, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        row = 0
        for category in achv.CATEGORY_ORDER:
            ctk.CTkLabel(
                scroll, text=category.upper(),
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=TEXT_DIM, anchor="w",
            ).grid(row=row, column=0, sticky="ew", padx=4, pady=(12 if row else 0, 4))
            row += 1

            for a in achv.ACHIEVEMENTS:
                if a.category != category:
                    continue
                card = ctk.CTkFrame(scroll, fg_color=BG_SECONDARY, corner_radius=8)
                card.grid(row=row, column=0, sticky="ew", pady=(0, 4))
                card.grid_columnconfigure(1, weight=1)

                icon_lbl = ctk.CTkLabel(
                    card, text=a.icon, font=ctk.CTkFont(size=22), width=40,
                )
                icon_lbl.grid(row=0, column=0, rowspan=2, padx=(12, 10), pady=8)

                title_lbl = ctk.CTkLabel(
                    card, text=a.title, font=ctk.CTkFont(size=13, weight="bold"),
                    text_color=TEXT_DIM, anchor="w",
                )
                title_lbl.grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=(8, 0))

                desc_lbl = ctk.CTkLabel(
                    card, text=a.description, font=ctk.CTkFont(size=11),
                    text_color=TEXT_DIM, anchor="w",
                )
                desc_lbl.grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=(0, 8))

                status_lbl = ctk.CTkLabel(
                    card, text="🔒", font=ctk.CTkFont(size=16), width=30,
                )
                status_lbl.grid(row=0, column=2, rowspan=2, padx=(0, 12))

                self._achv_rows[a.id] = {
                    "card": card, "icon": icon_lbl, "title": title_lbl,
                    "desc": desc_lbl, "status": status_lbl,
                }
                row += 1

        self._achv_refresh_display()

    def _achv_refresh_display(self) -> None:
        """Aggiorna l'aspetto di ogni riga in base alla bitmask corrente."""
        for achv_id, widgets in self._achv_rows.items():
            unlocked = achv.is_unlocked(self._achv_mask, achv_id)
            if unlocked:
                widgets["icon"].configure(text_color=TEXT_PRIMARY)
                widgets["title"].configure(text_color=TEXT_PRIMARY)
                widgets["desc"].configure(text_color=TEXT_DIM)
                widgets["status"].configure(text="✅", text_color=SUCCESS)
            else:
                widgets["icon"].configure(text_color=TEXT_DIM)
                widgets["title"].configure(text_color=TEXT_DIM)
                widgets["desc"].configure(text_color=TEXT_DIM)
                widgets["status"].configure(text="🔒", text_color=TEXT_DIM)

        if self._achv_lbl_progress:
            count = achv.unlocked_count(self._achv_mask)
            self._achv_lbl_progress.configure(
                text=f"🏆  {count} / {achv.ACHIEVEMENTS_COUNT} sbloccati"
            )

    def _achv_on_refresh(self) -> None:
        if self._achv_btn_refresh:
            self._achv_btn_refresh.configure(state="disabled", text="Scansione...")
        if self._achv_lbl_status:
            self._achv_lbl_status.configure(text="Scansione BLE in corso...", text_color=TEXT_DIM)

        def run():
            loop = asyncio.new_event_loop()
            try:
                addr = loop.run_until_complete(fw_upd.scan_for_petcube(timeout=10.0))
                mask, sessions, snapshot = loop.run_until_complete(fw_upd.read_achievements_ble(addr)) if addr else (None, None, None)
                self.after(0, lambda: self._achv_refresh_done(addr, mask, sessions=sessions, snapshot=snapshot))
            except Exception as e:
                self.after(0, lambda: self._achv_refresh_done(None, None, str(e)))
            finally:
                loop.close()

        threading.Thread(target=run, daemon=True).start()

    def _achv_refresh_done(self, addr: Optional[str], mask: Optional[int], err: str = "", sessions: Optional[int] = None, snapshot: Optional[dict] = None) -> None:
        if self._achv_btn_refresh:
            self._achv_btn_refresh.configure(state="normal", text="🔄  Aggiorna via BLE")
        if not self._achv_lbl_status:
            return
        if err:
            self._achv_lbl_status.configure(text=f"Errore: {err}", text_color=ERROR)
            return
        if mask is None:
            self._achv_lbl_status.configure(
                text="Nessun PetCube trovato o caratteristica non disponibile (FW < v25?)",
                text_color=ERROR,
            )
            return
        self._on_achv_mask_update(mask)
        if sessions is not None:
            self._on_pomodoro_session_count_update(sessions)
        if snapshot is not None:
            self._vlh_check_new_death(snapshot)
        ts = datetime.now().strftime("%H:%M:%S")
        self._achv_lbl_status.configure(text=f"Aggiornato {ts}  ({addr})", text_color=SUCCESS)

    def _on_achv_mask_update(self, mask: int) -> None:
        """Chiamato (sul thread GUI) quando una nuova bitmask è disponibile."""
        if mask == self._achv_mask:
            return
        newly_unlocked = mask & ~self._achv_mask
        self._achv_mask = mask
        achv.save_cached_mask(mask)
        self._achv_refresh_display()
        for a in achv.ACHIEVEMENTS:
            if newly_unlocked & (1 << a.id):
                self._show_achievement_toast(a)

    def _show_achievement_toast(self, a: "achv.Achievement") -> None:
        """Mostra una notifica desktop (toast/balloon dalla tray icon) per un
        achievement appena sbloccato."""
        title = f"{a.icon}  Achievement sbloccato!"
        message = f"{a.title} — {a.description}"
        if HAS_TRAY and self.tray_icon:
            try:
                self.tray_icon.notify(message, title)
            except Exception as e:
                logging.getLogger(__name__).debug(f"Notifica achievement fallita: {e}")

    def _on_pomodoro_session_count_update(self, total: int) -> None:
        """Chiamato (sul thread GUI) con il contatore lifetime sessioni
        Pomodoro letto via BLE (STATS, FW >= v30): aggiorna lo storico
        locale e ridisegna il grafico in Dashboard."""
        pomo_history.record_session_count(total)
        self._refresh_pomodoro_chart()

    def _refresh_pomodoro_chart(self) -> None:
        if not self._pomo_chart:
            return
        self._pomo_chart.draw(pomo_history.get_recent_history())

    def _achv_on_reset(self) -> None:
        if not messagebox.askyesno(
            "Reset achievements",
            "Questa operazione azzera TUTTI gli achievement e i contatori "
            "lifetime salvati sul PetCube (sessioni, vittorie, decessi, ecc.), "
            "senza toccare la partita in corso o il registro.\n\n"
            "L'operazione non è reversibile. Continuare?",
            icon="warning",
        ):
            return

        if self._achv_btn_reset:
            self._achv_btn_reset.configure(state="disabled", text="Reset in corso...")
        if self._achv_lbl_status:
            self._achv_lbl_status.configure(text="Reset achievement in corso...", text_color=TEXT_DIM)

        def run():
            loop = asyncio.new_event_loop()
            try:
                addr = loop.run_until_complete(fw_upd.scan_for_petcube(timeout=10.0))
                ok = loop.run_until_complete(fw_upd.reset_achievements_ble(addr)) if addr else False
                self.after(0, lambda: self._achv_reset_done(addr, ok))
            except Exception as e:
                self.after(0, lambda: self._achv_reset_done(None, False, str(e)))
            finally:
                loop.close()

        threading.Thread(target=run, daemon=True).start()

    def _achv_reset_done(self, addr: Optional[str], ok: bool, err: str = "") -> None:
        if self._achv_btn_reset:
            self._achv_btn_reset.configure(state="normal", text="🗑  Reset achievements")
        if not self._achv_lbl_status:
            return
        if err:
            self._achv_lbl_status.configure(text=f"Errore: {err}", text_color=ERROR)
            return
        if not addr:
            self._achv_lbl_status.configure(text="Nessun PetCube trovato.", text_color=ERROR)
            return
        if not ok:
            self._achv_lbl_status.configure(text="Reset fallito (FW < v28?).", text_color=ERROR)
            return
        self._achv_mask = 0
        achv.save_cached_mask(0)
        self._achv_refresh_display()
        ts = datetime.now().strftime("%H:%M:%S")
        self._achv_lbl_status.configure(text=f"Achievement azzerati {ts}  ({addr})", text_color=SUCCESS)

    # ═══════════════════════════════════════════════════════════
    # Valhalla Tab
    # ═══════════════════════════════════════════════════════════

    # Colori per le creature nel canvas
    _VLH_FIRE_FILL   = "#c04010"
    _VLH_FIRE_DARK   = "#e06030"
    _VLH_WATER_FILL  = "#1060a0"
    _VLH_WATER_DARK  = "#30a0c0"
    _VLH_LIGHT_RING  = "#d4af37"
    _VLH_DARK_RING   = "#7b2d8b"
    _VLH_BG          = "#2a4a20"   # fallback se l'immagine non è disponibile
    # Metà inferiore del canvas riservata ai mostri (0.0–1.0)
    _VLH_MEADOW_TOP  = 0.50

    def _build_valhalla_tab(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        # ── Header ──
        header = ctk.CTkFrame(parent, fg_color=BG_SECONDARY, corner_radius=8)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        header.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(
            header, text="⚔  VALHALLA",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#c0a030", anchor="w",
        ).grid(row=0, column=0, padx=(15, 20), pady=10, sticky="w")

        ctk.CTkLabel(
            header,
            text="Le creature cadute riposano qui in eterno. Clicca su una per sfidarla online.",
            font=ctk.CTkFont(size=11), text_color=TEXT_DIM, anchor="w",
        ).grid(row=0, column=1, columnspan=2, pady=10, sticky="w")

        self._vlh_lbl_count = ctk.CTkLabel(
            header, text="", text_color=TEXT_DIM,
            font=ctk.CTkFont(size=11), anchor="e",
        )
        self._vlh_lbl_count.grid(row=0, column=3, padx=(0, 15), pady=10, sticky="e")

        # ── Body (canvas + side panel) ──
        body = ctk.CTkFrame(parent, fg_color=BG_PRIMARY, corner_radius=0)
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        # Carica l'immagine di sfondo Valhalla (pixel art)
        self._vlh_bg_source: Optional[object] = None  # PIL Image originale
        self._vlh_bg_photo:  Optional[object] = None  # ImageTk.PhotoImage in uso
        self._vlh_bg_last_size: tuple = (0, 0)
        self._vlh_sprite_imgs: dict = {}   # name → ImageTk.PhotoImage (evita GC)
        try:
            import sys as _sys
            from PIL import Image as _PILImage
            _base = Path(getattr(_sys, "_MEIPASS", Path(__file__).resolve().parent.parent / "Sprite"))
            if getattr(_sys, "_MEIPASS", None):
                _bg_path = _base / "Valhalla_BG.png"
            else:
                _bg_path = Path(__file__).resolve().parent.parent / "Sprite" / "Valhalla_BG.png"
            self._vlh_bg_source = _PILImage.open(_bg_path).convert("RGBA")
        except Exception:
            self._vlh_bg_source = None

        # Canvas principale — sfondo pixel art con creature animate
        self._vlh_canvas = tk.Canvas(
            body, bg=self._VLH_BG, highlightthickness=0,
        )
        self._vlh_canvas.grid(row=0, column=0, sticky="nsew")
        self._vlh_canvas.bind("<Configure>", self._vlh_on_canvas_resize)

        # Avvia animazione
        self._vlh_init_sprites()
        self.after(200, self._vlh_animate)
        self._vlh_refresh_count_label()

    def _vlh_refresh_count_label(self) -> None:
        n = len(self._vlh_entries)
        if hasattr(self, "_vlh_lbl_count") and self._vlh_lbl_count:
            self._vlh_lbl_count.configure(
                text=f"{n} {'creatura' if n == 1 else 'creature'} nel Valhalla"
            )

    def _vlh_init_sprites(self) -> None:
        import random, math
        self._vlh_sprites = []
        # Spawn posizioni temporanee — saranno ricalibrate al primo frame
        # con dimensioni canvas reali. Usiamo valori normalizzati (0-1)
        # per x e y così scalano correttamente.
        for i, entry in enumerate(self._vlh_entries):
            angle = random.uniform(0, 2 * math.pi)
            speed = random.uniform(0.3, 0.7)
            self._vlh_sprites.append({
                "idx":    i,
                "x":      random.uniform(0.05, 0.95),   # fraz. larghezza canvas
                "y":      random.uniform(0.55, 0.90),   # fraz. altezza (metà inf.)
                "vx":     math.cos(angle) * speed,
                "vy":     math.sin(angle) * speed * 0.5,  # moto verticale più lento
                "radius": 28 + min(entry.evo_stage, 5) * 3,
                "tag":    f"creature_{i}",
                "_px_ready": False,  # flag: coordinate ancora normalizzate
                "anim_frame": 0,     # 0=happy1  1=happy2
                "anim_tick":  0,     # incrementato ad ogni frame, flip ogni 6 tick (~480ms)
            })

    def _vlh_animate(self) -> None:
        c = self._vlh_canvas
        if not c.winfo_exists():
            return
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 10 or h < 10:
            self.after(200, self._vlh_animate)
            return

        c.delete("all")
        self._vlh_draw_background(c, w, h)

        meadow_top = h * self._VLH_MEADOW_TOP   # limite superiore del prato

        for spr in self._vlh_sprites:
            r = spr["radius"]

            # Prima volta: converti coordinate normalizzate → pixel reali
            if not spr.get("_px_ready"):
                spr["x"] = spr["x"] * w
                spr["y"] = meadow_top + spr["y"] * (h - meadow_top)
                spr["_px_ready"] = True

            # Bounce: pareti laterali
            if spr["x"] - r < 8:
                spr["vx"] = abs(spr["vx"])
            elif spr["x"] + r > w - 8:
                spr["vx"] = -abs(spr["vx"])
            # Bounce: bordo superiore del prato (non salgono nella metà dell'immagine)
            if spr["y"] - r < meadow_top + 4:
                spr["vy"] = abs(spr["vy"])
            # Bounce: fondo canvas
            elif spr["y"] + r > h - 8:
                spr["vy"] = -abs(spr["vy"])

            spr["x"] += spr["vx"]
            spr["y"] += spr["vy"]

            # Avanza il frame dell'animazione ogni 6 tick (~480ms a 80ms/tick)
            spr["anim_tick"] += 1
            if spr["anim_tick"] >= 6:
                spr["anim_tick"] = 0
                spr["anim_frame"] ^= 1

            self._vlh_draw_sprite(c, spr)

        self.after(80, self._vlh_animate)

    def _vlh_draw_background(self, c: tk.Canvas, w: int, h: int) -> None:
        if self._vlh_bg_source is not None:
            # Ridimensiona l'immagine solo se le dimensioni canvas sono cambiate
            try:
                from PIL import Image as _PILImage, ImageTk as _ImageTk
                if self._vlh_bg_last_size != (w, h):
                    scaled = self._vlh_bg_source.resize((w, h), _PILImage.NEAREST)
                    self._vlh_bg_photo = _ImageTk.PhotoImage(scaled)
                    self._vlh_bg_last_size = (w, h)
                c.create_image(0, 0, anchor="nw", image=self._vlh_bg_photo)
                return
            except Exception:
                pass
        # Fallback: sfondo verde prato + cielo semplice
        c.create_rectangle(0, 0, w, h * self._VLH_MEADOW_TOP,
                            fill="#87ceeb", outline="")
        c.create_rectangle(0, h * self._VLH_MEADOW_TOP, w, h,
                            fill=self._VLH_BG, outline="")

    def _vlh_load_sprite(self, name: str):
        """Estrae happy1/happy2 dallo spritesheet (griglia 3×4 di celle 16×16),
        li scala 4× con NEAREST e ritorna [h1_norm, h1_flip, h2_norm, h2_flip] o None."""
        if name in self._vlh_sprite_imgs:
            return self._vlh_sprite_imgs[name]
        try:
            import sys as _sys
            from PIL import Image as _PILImage
            from PIL import ImageTk as _ImageTkLocal
            if getattr(_sys, "_MEIPASS", None):
                base = Path(_sys._MEIPASS)
            else:
                base = Path(__file__).resolve().parent.parent / "Sprite"
            for candidate in (f"{name}.png", f"{name.lower()}.png"):
                path = base / candidate
                if path.exists():
                    sheet = _PILImage.open(path).convert("RGBA")
                    CELL = 16
                    # happy1: indice 3 → row=1 col=0 → crop (0,16,16,32)
                    # happy2: indice 7 → row=2 col=1 → crop (16,32,32,48)
                    coords = [(0, CELL, CELL, CELL*2), (CELL, CELL*2, CELL*2, CELL*3)]
                    photos = []
                    for (l, t, r2, b) in coords:
                        cell = sheet.crop((l, t, r2, b))
                        scaled = cell.resize((CELL * 4, CELL * 4), _PILImage.NEAREST)
                        photos.append(_ImageTkLocal.PhotoImage(scaled))
                        photos.append(_ImageTkLocal.PhotoImage(
                            scaled.transpose(_PILImage.FLIP_LEFT_RIGHT)
                        ))
                    # ordine: [h1_norm, h1_flip, h2_norm, h2_flip]
                    self._vlh_sprite_imgs[name] = photos
                    return photos
        except Exception:
            pass
        self._vlh_sprite_imgs[name] = None  # non ritentare
        return None

    def _vlh_draw_sprite(self, c: tk.Canvas, spr: dict) -> None:
        x, y, r = spr["x"], spr["y"], spr["radius"]
        idx  = spr["idx"]
        tag  = spr["tag"]
        if idx >= len(self._vlh_entries):
            return
        entry = self._vlh_entries[idx]

        selected = (self._vlh_selected == idx)

        # Glow selezione
        if selected:
            c.create_oval(x - r - 6, y - r - 8, x + r + 6, y + r + 8,
                           fill="", outline="#ffffffaa", width=4, tags=(tag,))

        # Prova a usare la sprite pixel art; fallback all'ovale colorato
        photos = self._vlh_load_sprite(entry.name)
        if photos is not None:
            # Seleziona frame happy1/happy2 e normale/specchiato in base alla direzione
            frame_idx  = spr.get("anim_frame", 0)        # 0 = happy1, 1 = happy2
            flipped    = spr.get("vx", 0) > 0            # specchia quando si muove a destra
            photo_idx  = frame_idx * 2 + (1 if flipped else 0)
            c.create_image(x, y, image=photos[photo_idx], anchor="center", tags=(tag,))
        else:
            if entry.element == "Fire":
                fill = self._VLH_FIRE_FILL
            else:
                fill = self._VLH_WATER_FILL
            ring_color = ""
            if entry.final_variant == 1:
                ring_color = self._VLH_LIGHT_RING
            elif entry.final_variant == 2:
                ring_color = self._VLH_DARK_RING
            outline_c = "#ffffff" if selected else (ring_color if ring_color else "#555555")
            c.create_oval(x - r, y - r, x + r, y + r,
                           fill=fill, outline=outline_c, width=2 if selected else 1, tags=(tag,))
            c.create_text(x, y - 4, text=entry.display_icon,
                           font=("Segoe UI Emoji", max(10, r // 3)), fill="#ffffff",
                           anchor="center", tags=(tag,))

        # Nome sotto
        c.create_text(x, y + r + 12, text=entry.name,
                       font=("Arial", 8), fill="#cccccc",
                       anchor="center", tags=(tag,))

        # Bind click
        c.tag_bind(tag, "<Button-1>", lambda e, i=idx: self._vlh_select(i))
        c.tag_bind(tag, "<Enter>",    lambda e, t=tag: self._vlh_canvas.config(cursor="hand2"))
        c.tag_bind(tag, "<Leave>",    lambda e: self._vlh_canvas.config(cursor=""))

    def _vlh_on_canvas_resize(self, event) -> None:
        w, h = event.width, event.height
        meadow_top = h * self._VLH_MEADOW_TOP
        for spr in self._vlh_sprites:
            r = spr["radius"]
            if not spr.get("_px_ready"):
                continue
            spr["x"] = max(r + 8, min(w - r - 8, spr["x"]))
            # clamp Y dentro la metà inferiore
            spr["y"] = max(meadow_top + r + 4, min(h - r - 8, spr["y"]))

    def _vlh_select(self, idx: int) -> None:
        self._vlh_selected = idx
        self._vlh_show_detail(idx)

    def _vlh_show_detail(self, idx: int) -> None:
        """Apre una finestra flottante con le statistiche della creatura selezionata."""
        # Chiudi eventuale finestra precedente senza toccare il canvas
        if self._vlh_detail_win and self._vlh_detail_win.winfo_exists():
            self._vlh_detail_win.destroy()

        entry = self._vlh_entries[idx]
        from datetime import datetime as _dt
        death_ts = _dt.fromtimestamp(entry.death_timestamp).strftime("%d/%m/%Y %H:%M")

        win = ctk.CTkToplevel(self)
        self._vlh_detail_win = win
        win.title(entry.name)
        win.resizable(False, False)
        win.configure(fg_color=BG_SECONDARY)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self._vlh_deselect)

        # Centra sulla finestra principale
        self.update_idletasks()
        ww, wh = 280, 400
        x = self.winfo_x() + (self.winfo_width()  - ww) // 2
        y = self.winfo_y() + (self.winfo_height() - wh) // 2
        win.geometry(f"{ww}x{wh}+{x}+{y}")

        f = win   # per comodità usa win direttamente come contenitore

        ctk.CTkLabel(
            f, text=f"{entry.display_icon}  {entry.name}",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=TEXT_PRIMARY, anchor="w",
        ).pack(fill="x", padx=12, pady=(12, 2))

        ctk.CTkLabel(
            f, text=f"{entry.stage_label}  •  {entry.element}",
            font=ctk.CTkFont(size=11), text_color=TEXT_DIM, anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 8))

        ctk.CTkFrame(f, fg_color=BORDER, height=1, corner_radius=0).pack(fill="x", padx=8)

        stat_rows = [
            ("⚔  STR",  entry.stat_str),
            ("🧠  INT",  entry.stat_int),
            ("⚡  ENG",  entry.stat_eng),
            ("😊  HAP",  entry.stat_hap),
            ("🍅  Sessioni", entry.sessions),
            ("🏅  Vittorie FW", entry.battles_won),
            ("💀  Sconfitte FW", entry.battles_lost),
        ]
        for label, value in stat_rows:
            row = ctk.CTkFrame(f, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=1)
            ctk.CTkLabel(row, text=label, font=ctk.CTkFont(size=11),
                          text_color=TEXT_DIM, anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=str(value), font=ctk.CTkFont(size=11, weight="bold"),
                          text_color=TEXT_PRIMARY, anchor="e").pack(side="right")

        ctk.CTkFrame(f, fg_color=BORDER, height=1, corner_radius=0).pack(fill="x", padx=8, pady=4)

        if entry.valhalla_wins > 0 or entry.valhalla_losses > 0:
            row2 = ctk.CTkFrame(f, fg_color="transparent")
            row2.pack(fill="x", padx=12, pady=1)
            ctk.CTkLabel(row2, text="⚔  Valhalla W/L",
                          font=ctk.CTkFont(size=11), text_color=TEXT_DIM, anchor="w").pack(side="left")
            ctk.CTkLabel(row2, text=f"{entry.valhalla_wins}/{entry.valhalla_losses}",
                          font=ctk.CTkFont(size=11, weight="bold"),
                          text_color=SUCCESS, anchor="e").pack(side="right")

        ctk.CTkLabel(
            f, text=f"Morto il {death_ts}",
            font=ctk.CTkFont(size=10), text_color=TEXT_DIM,
        ).pack(padx=12, pady=(4, 6))

        ctk.CTkButton(
            f, text="⚔  Combatti online", height=36,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=lambda i=idx: self._vlh_open_fight_dialog(i),
        ).pack(fill="x", padx=12, pady=(0, 4))

        ctk.CTkButton(
            f, text="✕  Chiudi", height=28,
            fg_color=BG_TERTIARY, hover_color="#444",
            font=ctk.CTkFont(size=11),
            command=self._vlh_deselect,
        ).pack(fill="x", padx=12, pady=(0, 10))

    def _vlh_deselect(self) -> None:
        self._vlh_selected = None
        if self._vlh_detail_win and self._vlh_detail_win.winfo_exists():
            self._vlh_detail_win.destroy()
        self._vlh_detail_win = None

    def _vlh_open_fight_dialog(self, idx: int) -> None:
        entry = self._vlh_entries[idx]
        firebase_url = self.config_data.get("valhalla", {}).get("firebase_url", "").strip()
        username     = self._sv_device.get("username", ctk.StringVar()).get().strip()

        if not firebase_url:
            messagebox.showwarning(
                "Valhalla online",
                "Per giocare online configura l'URL Firebase in Impostazioni → Valhalla.\n"
                "Inserisci il tuo Firebase Realtime Database URL (es. "
                "https://mio-progetto-default-rtdb.firebaseio.com).",
            )
            return

        if not username:
            messagebox.showwarning(
                "Valhalla online",
                "Imposta prima il tuo username in Impostazioni → Device.",
            )
            return

        dlg = ctk.CTkToplevel(self)
        dlg.title("Combatti nel Valhalla")
        dlg.geometry("380x250")
        dlg.resizable(False, False)
        dlg.configure(fg_color=BG_PRIMARY)
        dlg.grab_set()
        dlg.attributes("-topmost", True)

        ctk.CTkLabel(
            dlg,
            text=f"⚔  {entry.name}  vs  ???",
            font=ctk.CTkFont(size=14, weight="bold"), text_color=TEXT_PRIMARY,
        ).pack(pady=(20, 8))

        ctk.CTkLabel(
            dlg, text="Scegli la modalità di battaglia:",
            font=ctk.CTkFont(size=11), text_color=TEXT_DIM,
        ).pack(pady=(0, 10))

        # Campo username avversario
        sv_target = ctk.StringVar()
        ctk.CTkEntry(
            dlg, textvariable=sv_target,
            placeholder_text="Username avversario (es. Mario#1234)",
            fg_color=BG_SECONDARY, border_color=BORDER,
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12),
            width=320,
        ).pack(padx=20, pady=(0, 8))

        lbl_status = ctk.CTkLabel(
            dlg, text="", font=ctk.CTkFont(size=11), text_color=TEXT_DIM,
        )
        lbl_status.pack()

        btn_frame = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=10)

        def _do_fight(random_mode: bool):
            target = sv_target.get().strip()
            if not random_mode and not target:
                lbl_status.configure(text="Inserisci un username o usa Casuale.", text_color=WARNING)
                return
            lbl_status.configure(text="Invio sfida in corso...", text_color=TEXT_DIM)
            btn_frame.winfo_children()[0].configure(state="disabled")
            btn_frame.winfo_children()[1].configure(state="disabled")

            from config_schema import device_tag
            full_tag = device_tag(username, self._device_id)
            entry.owner = full_tag

            client = ValhallaBattleClient(firebase_url, full_tag)
            if random_mode:
                cid = client.send_random_challenge(entry)
            else:
                cid = client.send_challenge(target, entry)

            if cid:
                lbl_status.configure(
                    text=f"Sfida inviata! ID: {cid}", text_color=SUCCESS
                )
                self.after(2000, dlg.destroy)
            else:
                lbl_status.configure(
                    text="Invio fallito. Controlla la connessione.", text_color=ERROR
                )
                for btn in btn_frame.winfo_children():
                    btn.configure(state="normal")

        ctk.CTkButton(
            btn_frame, text="🎲  Casuale", fg_color=BG_TERTIARY, hover_color="#444",
            font=ctk.CTkFont(size=12),
            command=lambda: _do_fight(True),
        ).pack(side="left", expand=True, fill="x", padx=(0, 4))

        ctk.CTkButton(
            btn_frame, text="⚔  Sfida utente", fg_color=ACCENT, hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(size=12),
            command=lambda: _do_fight(False),
        ).pack(side="left", expand=True, fill="x", padx=(4, 0))

    def _vlh_add_entry_from_snapshot(self, snapshot: dict, owner: str = "") -> None:
        """Aggiunge una nuova entry al Valhalla da un dict snapshot BLE."""
        entry = vlh.ValhallaEntry(
            element=snapshot["element"],
            evo_stage=snapshot["evo_stage"],
            line_variant=snapshot["line_variant"],
            final_variant=snapshot["final_variant"],
            stat_str=snapshot["stat_str"],
            stat_int=snapshot["stat_int"],
            stat_eng=snapshot["stat_eng"],
            stat_hap=snapshot["stat_hap"],
            sessions=snapshot["sessions"],
            battles_won=snapshot["battles_won"],
            battles_lost=snapshot["battles_lost"],
            deaths_total=snapshot["deaths_total"],
            owner=owner,
        )
        vlh.add_entry(entry)
        self._vlh_entries = vlh.load_valhalla()
        # Aggiungi sprite per la nuova creatura (coordinate normalizzate, prato)
        import random, math
        spr = {
            "idx":       len(self._vlh_entries) - 1,
            "x":         random.uniform(0.05, 0.95),
            "y":         random.uniform(0.55, 0.90),
            "vx":        math.cos(random.uniform(0, 6.28)) * 0.5,
            "vy":        math.sin(random.uniform(0, 6.28)) * 0.5 * 0.5,
            "radius":    28 + min(entry.evo_stage, 5) * 3,
            "tag":       f"creature_{len(self._vlh_entries) - 1}",
            "_px_ready": False,
        }
        self._vlh_sprites.append(spr)
        self._vlh_refresh_count_label()
        self._show_achievement_toast(
            type("_", (), {
                "icon": entry.display_icon,
                "title": f"{entry.name} è entrata nel Valhalla",
                "description": f"Stage {entry.stage_label} • {entry.sessions} sessioni • "
                               f"{entry.battles_won}V/{entry.battles_lost}S",
            })()
        )
        self._append_log_line(
            f"⚔ {entry.name} ({entry.element}, {entry.stage_label}) è entrata nel Valhalla!",
            color="#c0a030",
        )

    def _vlh_show_challenge_dialog(self, challenger: str, challenge_id: str,
                                    creature_dict: dict) -> None:
        """Mostra il dialog accetta/rifiuta quando arriva una sfida."""
        try:
            challenger_entry = vlh.ValhallaEntry.from_dict(creature_dict)
        except Exception:
            return

        # Notifica tray
        if HAS_TRAY and self.tray_icon:
            try:
                self.tray_icon.notify(
                    f"Sfida da {challenger}",
                    f"⚔  {challenger} ti ha sfidato a duello con {challenger_entry.name}!",
                )
            except Exception:
                pass

        # Dialog accetta/rifiuta
        dlg = ctk.CTkToplevel(self)
        dlg.title("Sfida ricevuta!")
        dlg.geometry("400x260")
        dlg.resizable(False, False)
        dlg.configure(fg_color=BG_PRIMARY)
        dlg.lift()
        dlg.attributes("-topmost", True)

        ctk.CTkLabel(
            dlg, text="⚔  Sfida ricevuta!",
            font=ctk.CTkFont(size=14, weight="bold"), text_color="#c0a030",
        ).pack(pady=(20, 6))

        ctk.CTkLabel(
            dlg,
            text=f"{challenger} ti ha sfidato a duello\ncon {challenger_entry.name} "
                 f"({challenger_entry.element}, {challenger_entry.stage_label})",
            font=ctk.CTkFont(size=12), text_color=TEXT_PRIMARY, justify="center",
        ).pack(pady=(0, 10))

        # Scegli quale tua creatura mandare
        if self._vlh_entries:
            options = [e.name for e in self._vlh_entries]
        else:
            options = ["Nessuna creatura"]

        ctk.CTkLabel(
            dlg, text="Scegli la tua creatura:",
            font=ctk.CTkFont(size=11), text_color=TEXT_DIM,
        ).pack()

        sv_choice = ctk.StringVar(value=options[0] if options else "")
        ctk.CTkOptionMenu(
            dlg, values=options, variable=sv_choice,
            fg_color=BG_SECONDARY, button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
        ).pack(padx=20, pady=4)

        lbl_result = ctk.CTkLabel(dlg, text="", font=ctk.CTkFont(size=11), text_color=SUCCESS)
        lbl_result.pack()

        firebase_url = self.config_data.get("valhalla", {}).get("firebase_url", "").strip()
        username = self._sv_device.get("username", ctk.StringVar()).get().strip()
        from config_schema import device_tag
        full_tag = device_tag(username, self._device_id)

        def _accept():
            chosen_name = sv_choice.get()
            my_entry = next((e for e in self._vlh_entries if e.name == chosen_name), None)
            if not my_entry:
                lbl_result.configure(text="Nessuna creatura selezionata.", text_color=ERROR)
                return
            my_entry.owner = full_tag
            client = ValhallaBattleClient(firebase_url, full_tag)
            result = client.accept_challenge(challenge_id, my_entry)
            if result:
                winner = result.get("winner", "?")
                attacker_won = result.get("attacker_won", False)
                won = (result.get("winner_owner") == full_tag)
                # Aggiorna W/L
                ei = self._vlh_entries.index(my_entry)
                vlh.update_entry_valhalla_record(
                    ei,
                    my_entry.valhalla_wins + (1 if won else 0),
                    my_entry.valhalla_losses + (0 if won else 1),
                )
                self._vlh_entries = vlh.load_valhalla()
                color = SUCCESS if won else ERROR
                lbl_result.configure(
                    text=f"{'Vittoria!' if won else 'Sconfitta!'} {winner} ha vinto dopo {result.get('turns', '?')} turni.",
                    text_color=color,
                )
                self.after(3000, dlg.destroy)
            else:
                lbl_result.configure(text="Errore durante la battaglia.", text_color=ERROR)

        def _reject():
            if firebase_url:
                client = ValhallaBattleClient(firebase_url, full_tag)
                client.reject_challenge(challenge_id)
            dlg.destroy()

        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=8)
        ctk.CTkButton(
            btn_row, text="✓  Accetta", fg_color=ACCENT, hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(size=12, weight="bold"), command=_accept,
        ).pack(side="left", expand=True, fill="x", padx=(0, 4))
        ctk.CTkButton(
            btn_row, text="✕  Rifiuta", fg_color="#5a1a1a", hover_color="#7a2a2a",
            font=ctk.CTkFont(size=12), command=_reject,
        ).pack(side="left", expand=True, fill="x", padx=(4, 0))

    def _vlh_on_challenge_received(self, challenger: str, challenge_id: str,
                                    creature_dict: dict) -> None:
        """Callback dal thread di polling, smistato sul thread GUI."""
        self.after(0, lambda: self._vlh_show_challenge_dialog(
            challenger, challenge_id, creature_dict
        ))

    def _vlh_start_polling_if_needed(self) -> None:
        if self._vlh_polling_started:
            return
        firebase_url = self.config_data.get("valhalla", {}).get("firebase_url", "").strip()
        username     = self._sv_device.get("username", ctk.StringVar()).get().strip()
        if not firebase_url or not username:
            return
        from config_schema import device_tag
        full_tag = device_tag(username, self._device_id)
        self._vlh_battle_client = ValhallaBattleClient(
            firebase_url, full_tag,
            on_challenge=self._vlh_on_challenge_received,
        )
        self._vlh_battle_client.start_polling(interval_sec=15.0)
        self._vlh_polling_started = True

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

        self._fw_btn_reset = ctk.CTkButton(
            ble_card, text="🗑  Reset di fabbrica", width=170,
            fg_color="#5a1a1a", hover_color="#7a2a2a", font=ctk.CTkFont(size=12),
            state="disabled",
            command=self._fw_on_reset,
        )
        self._fw_btn_reset.grid(row=1, column=3, padx=(0, 12), pady=(0, 10), sticky="e")

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
        if self._fw_btn_reset:
            if locked:
                self._fw_btn_reset.configure(state="disabled")
            else:
                self._fw_btn_reset.configure(
                    state="normal" if self._fw_ble_address is not None else "disabled"
                )

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

    # ── Reset di fabbrica ────────────────────────────────────

    def _fw_on_reset(self) -> None:
        if not self._fw_ble_address:
            return
        if not messagebox.askyesno(
            "Reset di fabbrica",
            "Questa operazione cancella TUTTI i dati salvati sul PetCube "
            "(partita in corso, registro creature e leggende) e riavvia "
            "il dispositivo.\n\nL'operazione non è reversibile. Continuare?",
            icon="warning",
        ):
            return

        self._fw_buttons_lock(True)
        self._fw_btn_reset.configure(text="Reset in corso...")
        self._fw_log_append("Invio richiesta di reset di fabbrica...")

        addr = self._fw_ble_address

        def run():
            loop = asyncio.new_event_loop()
            try:
                ok = loop.run_until_complete(fw_upd.factory_reset_ble(addr))
                self.after(0, lambda: self._fw_reset_done(ok))
            except Exception as e:
                self.after(0, lambda: self._fw_reset_done(False, str(e)))
            finally:
                loop.close()

        threading.Thread(target=run, daemon=True).start()

    def _fw_reset_done(self, ok: bool, err: str = "") -> None:
        self._fw_btn_reset.configure(text="🗑  Reset di fabbrica")
        if ok:
            self._fw_log_append(
                "Reset di fabbrica avviato: il PetCube cancellerà i dati e si riavvierà."
            )
            self._fw_ble_address = None
            self._fw_device_ver = None
            self._fw_lbl_device_ver.configure(
                text="Versione installata:  —", text_color=TEXT_DIM
            )
        else:
            self._fw_log_append(f"ERRORE reset di fabbrica: {err or 'scrittura BLE fallita'}")
        self._fw_buttons_lock(False)

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
            # Lo script di aggiornamento (gia' avviato) chiudera' la Companion,
            # sostituira' l'eseguibile e chiedera' se riavviarla.
            self._fw_log_append("✓ Download completato. La Companion verrà chiusa e aggiornata a breve...")
        else:
            self._fw_log_append("✓ Aggiornamento applicato. Riavvio dell'app...")
            self.after(500, app_upd.restart_from_source)

    # ═══════════════════════════════════════════════════════════
    # Engine control
    # ═══════════════════════════════════════════════════════════

    def _on_start(self) -> None:
        if self.engine and self.engine.is_running():
            return
        self.engine = CompanionEngine(self.config_data, on_achievements_update=self._on_achv_ble_event)
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

    def _vlh_check_new_death(self, snapshot: dict) -> None:
        """Controlla se deaths_total nello snapshot è aumentato rispetto al cache.
        Se sì, aggiunge la creatura caduta al Valhalla."""
        dt_new = snapshot.get("deaths_total", 0)
        if dt_new == 0:
            return  # nessuna morte ancora (snapshot vuoto / FW < v35)
        dt_cached = vlh.load_deaths_cache()
        if dt_new > dt_cached:
            # Nuova morte rilevata
            username = self._sv_device.get("username", ctk.StringVar()).get().strip()
            from config_schema import device_tag
            owner = device_tag(username, self._device_id) if username else ""
            self._vlh_add_entry_from_snapshot(snapshot, owner=owner)
            vlh.save_deaths_cache(dt_new)

    def _on_sync_now(self) -> None:
        """Connessione BLE on-demand: sincronizza orologio, tag identità e
        bitmask achievement del PetCube, indipendentemente dal motore."""
        self.sync_now_btn.configure(state="disabled", text="Sincronizzazione...")
        self._append_log_line("🔄 Sincronizzazione manuale in corso...", color=TEXT_DIM)

        tag = device_tag(self._sv_device["username"].get(), self._device_id)

        def run():
            loop = asyncio.new_event_loop()
            try:
                addr = loop.run_until_complete(fw_upd.scan_for_petcube(timeout=10.0))
                mask, sessions, snapshot = loop.run_until_complete(fw_upd.sync_now_ble(addr, tag)) if addr else (None, None, None)
                self.after(0, lambda: self._sync_now_done(addr, mask, sessions=sessions, snapshot=snapshot))
            except Exception as e:
                self.after(0, lambda: self._sync_now_done(None, None, str(e)))
            finally:
                loop.close()

        threading.Thread(target=run, daemon=True).start()

    def _sync_now_done(self, addr: Optional[str], mask: Optional[int], err: str = "", sessions: Optional[int] = None, snapshot: Optional[dict] = None) -> None:
        self.sync_now_btn.configure(state="normal", text="🔄 Sincronizza ora")
        if err:
            self._append_log_line(f"❌ Sincronizzazione fallita: {err}", color=ERROR)
            return
        if addr is None:
            self._append_log_line("❌ Nessun PetCube trovato via BLE.", color=ERROR)
            return
        ts = datetime.now().strftime("%H:%M:%S")
        self._append_log_line(f"✅ Sincronizzato con {addr} alle {ts} (orologio + identità).", color=SUCCESS)
        if mask is not None:
            self._on_achv_mask_update(mask)
        if sessions is not None:
            self._on_pomodoro_session_count_update(sessions)
        if snapshot is not None:
            self._vlh_check_new_death(snapshot)
        self._vlh_start_polling_if_needed()

    def _on_apply_brightness(self) -> None:
        """Invia la luminosità impostata dal cursore al PetCube via BLE (FW >= v30)."""
        value = int(self._sv_brightness.get())
        self._brightness_btn_apply.configure(state="disabled", text="Applicazione...")
        self._brightness_status_label.configure(text="Scansione BLE in corso...", text_color=TEXT_DIM)

        def run():
            loop = asyncio.new_event_loop()
            try:
                addr = loop.run_until_complete(fw_upd.scan_for_petcube(timeout=10.0))
                ok = loop.run_until_complete(fw_upd.set_brightness_ble(addr, value)) if addr else False
                self.after(0, lambda: self._apply_brightness_done(addr, ok))
            except Exception as e:
                self.after(0, lambda: self._apply_brightness_done(None, False, str(e)))
            finally:
                loop.close()

        threading.Thread(target=run, daemon=True).start()

    def _apply_brightness_done(self, addr: Optional[str], ok: bool, err: str = "") -> None:
        self._brightness_btn_apply.configure(state="normal", text="Applica al PetCube")
        if err:
            self._brightness_status_label.configure(text=f"Errore: {err}", text_color=ERROR)
            return
        if addr is None:
            self._brightness_status_label.configure(text="Nessun PetCube trovato via BLE.", text_color=ERROR)
            return
        if not ok:
            self._brightness_status_label.configure(
                text="Scrittura fallita (caratteristica non disponibile, FW < v30?)", text_color=ERROR,
            )
            return
        ts = datetime.now().strftime("%H:%M:%S")
        self._brightness_status_label.configure(text=f"Applicata alle {ts} ({addr}).", text_color=SUCCESS)

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

    def _on_achv_ble_event(self, mask: int) -> None:
        """Callback dal thread engine quando la connessione BLE legge la bitmask achievement."""
        self.event_queue.put(("achv", mask))

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
                elif kind == "achv":
                    self._on_achv_mask_update(item[1])
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

    def show_window(self) -> None:
        """Riporta la finestra in primo piano (da tray icon o da una
        seconda istanza avviata per errore, vedi _start_single_instance_listener)."""
        self.deiconify()
        self.lift()
        self.focus_force()
        self._tray_visible = True

    def _start_single_instance_listener(self, sock: socket.socket) -> None:
        """Ascolta sul socket di lock: se una seconda istanza viene avviata,
        la richiesta "show" che invia ci fa riportare la finestra in primo piano."""
        def _loop():
            while True:
                try:
                    conn, _ = sock.accept()
                except OSError:
                    return
                try:
                    conn.recv(16)
                finally:
                    conn.close()
                self.after(0, self.show_window)

        threading.Thread(target=_loop, daemon=True).start()

    def _setup_tray(self) -> None:
        img = Image.new("RGB", (64, 64), color=(30, 30, 30))
        d = ImageDraw.Draw(img)
        d.ellipse([10, 10, 54, 54], fill=(78, 201, 176))
        d.text((23, 21), "PC", fill=(30, 30, 30))

        def on_show(icon, item):
            self.after(0, self.show_window)

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

    lock_sock = _acquire_single_instance_lock()
    if lock_sock is None:
        print("PetCube Companion è già in esecuzione — porto la finestra esistente in primo piano.")
        _notify_existing_instance()
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    config_path = Path("config.json")
    if not config_path.exists():
        print("ℹ config.json non trovato — avvio wizard di configurazione iniziale...")
        config = setup_wizard.run_first_setup()
        if config is None:
            print("Configurazione annullata.", file=sys.stderr)
            sys.exit(1)
    else:
        try:
            config = load_config(config_path)
        except FileNotFoundError as e:
            print(f"❌ {e}", file=sys.stderr)
            sys.exit(1)

    app = CompanionGUI(config)
    app._start_single_instance_listener(lock_sock)
    app.mainloop()


if __name__ == "__main__":
    main()
