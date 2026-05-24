"""
gui.py
GUI per PetCube Companion. Dashboard CustomTkinter + tray icon.

Lancia con:
    python gui.py

Per avvio CLI (no GUI), continua a usare:
    python main.py
"""
import logging
import queue
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import customtkinter as ctk

try:
    from PIL import Image, ImageDraw
    import pystray
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

from companion_engine import CompanionEngine, load_config
from notification_packet import NotifPacket, NotifSource, NotifCategory, NotifPriority


# ── Dark theme palette (Discord/VS Code style) ─────────────────
BG_PRIMARY    = "#1e1e1e"   # editor background
BG_SECONDARY  = "#252526"   # sidebar
BG_TERTIARY   = "#2d2d30"   # cards
ACCENT        = "#0e639c"   # blue accent
ACCENT_HOVER  = "#1177bb"
SUCCESS       = "#4ec9b0"
WARNING       = "#dcdcaa"
ERROR         = "#f48771"
TEXT_PRIMARY  = "#cccccc"
TEXT_DIM      = "#858585"
BORDER        = "#3e3e42"


# Mapping source enum → emoji/etichetta UI
SOURCE_LABEL = {
    NotifSource.DISCORD:  "💬 Discord",
    NotifSource.GMAIL:    "📧 Gmail",
    NotifSource.CALENDAR: "📅 Calendar",
    NotifSource.SLACK:    "💼 Slack",
    NotifSource.TRELLO:   "📋 HacknPlan",
    NotifSource.GITHUB:   "🐙 GitHub",
    NotifSource.GENERIC:  "❔ Other",
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


class CompanionGUI(ctk.CTk):
    def __init__(self, config: dict):
        super().__init__()
        self.config_data = config

        # Window setup
        self.title("PetCube Companion")
        self.geometry("1100x700")
        self.minsize(900, 600)
        self.configure(fg_color=BG_PRIMARY)

        # Set CustomTkinter appearance
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        # Engine
        self.engine: Optional[CompanionEngine] = None

        # Queue per eventi cross-thread (engine → GUI main thread)
        self.event_queue: queue.Queue = queue.Queue()

        # Storico notifiche locale (max 100)
        self.recent_notifications: list[dict] = []

        # Tray icon (opzionale)
        self.tray_icon = None
        self._tray_visible = False

        # Costruisci UI
        self._build_ui()

        # Gestione close window: minimizza in tray invece di chiudere
        self.protocol("WM_DELETE_WINDOW", self._on_close_window)

        # Avvia poller della queue per propagare eventi dal thread engine
        self.after(100, self._poll_event_queue)

        # Avvia tray se disponibile
        if HAS_TRAY:
            self._setup_tray()

    # ═══════════════════════════════════════════════════════════
    # UI Construction
    # ═══════════════════════════════════════════════════════════

    def _build_ui(self) -> None:
        # Grid principale: header (row 0) + body (row 1)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()
        self._build_body()

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=BG_SECONDARY, height=60, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(2, weight=1)

        # Indicatore stato (pallino colorato)
        self.status_dot = ctk.CTkLabel(
            header, text="●", text_color=TEXT_DIM, font=("Arial", 28)
        )
        self.status_dot.grid(row=0, column=0, padx=(15, 5), pady=10)

        # Titolo
        title = ctk.CTkLabel(
            header,
            text="PetCube Companion",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=TEXT_PRIMARY,
        )
        title.grid(row=0, column=1, padx=(0, 20), pady=10, sticky="w")

        # Status testuale (centrale)
        self.status_text = ctk.CTkLabel(
            header,
            text="Stopped",
            font=ctk.CTkFont(size=12),
            text_color=TEXT_DIM,
        )
        self.status_text.grid(row=0, column=2, pady=10, sticky="w")

        # Bottoni Start/Stop
        self.start_btn = ctk.CTkButton(
            header, text="▶ Start",
            width=100, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._on_start,
        )
        self.start_btn.grid(row=0, column=3, padx=5, pady=10)

        self.stop_btn = ctk.CTkButton(
            header, text="■ Stop",
            width=100, fg_color=BG_TERTIARY, hover_color="#444",
            command=self._on_stop,
            state="disabled",
        )
        self.stop_btn.grid(row=0, column=4, padx=(5, 15), pady=10)

    def _build_body(self) -> None:
        body = ctk.CTkFrame(self, fg_color=BG_PRIMARY, corner_radius=0)
        body.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        # Sidebar left
        self._build_sidebar(body)
        # Main area right
        self._build_main_area(body)

    def _build_sidebar(self, parent) -> None:
        sidebar = ctk.CTkFrame(parent, fg_color=BG_SECONDARY, width=260, corner_radius=8)
        sidebar.grid(row=0, column=0, sticky="nsw", padx=(0, 10))
        sidebar.grid_propagate(False)

        # Section: PLUGINS
        ctk.CTkLabel(
            sidebar, text="PLUGINS",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_DIM, anchor="w",
        ).pack(fill="x", padx=15, pady=(15, 5))

        self.plugin_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        self.plugin_frame.pack(fill="x", padx=10)
        self.plugin_labels: dict[str, ctk.CTkLabel] = {}
        # Popola con i plugin configurati come potenzialmente attivi
        plugins_cfg = self.config_data.get("plugins", {})
        for name, cfg in plugins_cfg.items():
            if isinstance(cfg, dict) and cfg.get("enabled"):
                row = ctk.CTkFrame(self.plugin_frame, fg_color="transparent")
                row.pack(fill="x", pady=2)
                dot = ctk.CTkLabel(row, text="●", text_color=TEXT_DIM,
                                   font=("Arial", 14), width=20)
                dot.pack(side="left")
                lbl = ctk.CTkLabel(row, text=name, anchor="w",
                                   text_color=TEXT_PRIMARY,
                                   font=ctk.CTkFont(size=12))
                lbl.pack(side="left", fill="x", expand=True)
                self.plugin_labels[name] = dot

        # Section: TRANSPORT
        ctk.CTkLabel(
            sidebar, text="TRANSPORT",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_DIM, anchor="w",
        ).pack(fill="x", padx=15, pady=(20, 5))

        transport_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        transport_frame.pack(fill="x", padx=10)

        self.transport_dot = ctk.CTkLabel(transport_frame, text="●",
                                          text_color=TEXT_DIM,
                                          font=("Arial", 14), width=20)
        self.transport_dot.pack(side="left")
        self.transport_label = ctk.CTkLabel(
            transport_frame, text="—", anchor="w",
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12),
        )
        self.transport_label.pack(side="left", fill="x", expand=True)

        # Section: STATS
        ctk.CTkLabel(
            sidebar, text="STATISTICHE",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_DIM, anchor="w",
        ).pack(fill="x", padx=15, pady=(20, 5))

        stats_frame = ctk.CTkFrame(sidebar, fg_color=BG_TERTIARY, corner_radius=6)
        stats_frame.pack(fill="x", padx=10, pady=5)

        self.stat_sent_label = ctk.CTkLabel(
            stats_frame, text="Inviate:  0",
            anchor="w", text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=12),
        )
        self.stat_sent_label.pack(fill="x", padx=10, pady=(8, 2))

        self.stat_failed_label = ctk.CTkLabel(
            stats_frame, text="Fallite:  0",
            anchor="w", text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=12),
        )
        self.stat_failed_label.pack(fill="x", padx=10, pady=2)

        self.stat_uptime_label = ctk.CTkLabel(
            stats_frame, text="Uptime:   —",
            anchor="w", text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=12),
        )
        self.stat_uptime_label.pack(fill="x", padx=10, pady=(2, 8))

        # Section: ABOUT (in fondo)
        about = ctk.CTkLabel(
            sidebar,
            text="v0.1 • Lemon Loop Studio",
            text_color=TEXT_DIM,
            font=ctk.CTkFont(size=10),
        )
        about.pack(side="bottom", pady=10)

    def _build_main_area(self, parent) -> None:
        main = ctk.CTkFrame(parent, fg_color=BG_PRIMARY, corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=3)  # Log
        main.grid_rowconfigure(1, weight=2)  # Notifiche

        # ── LOG STREAM ──
        log_card = ctk.CTkFrame(main, fg_color=BG_SECONDARY, corner_radius=8)
        log_card.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        log_card.grid_columnconfigure(0, weight=1)
        log_card.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            log_card, text="LOG STREAM",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_DIM, anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=15, pady=(10, 5))

        self.log_text = ctk.CTkTextbox(
            log_card,
            fg_color=BG_PRIMARY,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="none",
            corner_radius=6,
        )
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.log_text.configure(state="disabled")

        # ── NOTIFICHE RECENTI ──
        notif_card = ctk.CTkFrame(main, fg_color=BG_SECONDARY, corner_radius=8)
        notif_card.grid(row=1, column=0, sticky="nsew")
        notif_card.grid_columnconfigure(0, weight=1)
        notif_card.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            notif_card, text="STORICO NOTIFICHE RECENTI",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_DIM, anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=15, pady=(10, 5))

        # Scrollable frame per la lista
        self.notif_scroll = ctk.CTkScrollableFrame(
            notif_card,
            fg_color=BG_PRIMARY,
            corner_radius=6,
        )
        self.notif_scroll.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.notif_scroll.grid_columnconfigure(0, weight=1)

        # Placeholder
        self._notif_placeholder = ctk.CTkLabel(
            self.notif_scroll,
            text="Nessuna notifica ancora. Avvia il motore con ▶ Start.",
            text_color=TEXT_DIM, font=ctk.CTkFont(size=11),
        )
        self._notif_placeholder.pack(pady=20)

    # ═══════════════════════════════════════════════════════════
    # Engine control
    # ═══════════════════════════════════════════════════════════

    def _on_start(self) -> None:
        if self.engine and self.engine.is_running():
            return
        # Crea nuovo engine (un'istanza viene "consumata" dopo stop, meglio rifare)
        self.engine = CompanionEngine(self.config_data)
        self.engine.add_log_listener(self._on_log_record)
        self.engine.add_event_listener(self._on_event)
        self.engine.start()

        self.start_btn.configure(state="disabled", fg_color=BG_TERTIARY)
        self.stop_btn.configure(state="normal", fg_color="#a1260d", hover_color="#c14a3a")
        self.status_dot.configure(text_color=SUCCESS)
        self.status_text.configure(text="Running", text_color=SUCCESS)

        # Schedule update status periodico
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

        # Reset pallini plugin
        for dot in self.plugin_labels.values():
            dot.configure(text_color=TEXT_DIM)
        self.transport_dot.configure(text_color=TEXT_DIM)
        self.transport_label.configure(text="—")

    # ═══════════════════════════════════════════════════════════
    # Event listeners (chiamati dal thread engine)
    # ═══════════════════════════════════════════════════════════

    def _on_log_record(self, record: logging.LogRecord) -> None:
        """Chiamato dal thread engine. Marshall verso main thread via queue."""
        try:
            formatted = self._log_broadcaster_format(record)
            color = self._color_for_log_level(record.levelno)
            self.event_queue.put(("log", formatted, color))
        except Exception:
            pass

    def _on_event(self, pkt: NotifPacket, send_ok: bool) -> None:
        """Chiamato dal thread engine. Marshall verso main thread via queue."""
        self.event_queue.put(("notif", pkt, send_ok))

    @staticmethod
    def _log_broadcaster_format(record: logging.LogRecord) -> str:
        # Format coerente col CLI: 12:34:56 [LEVEL] name — message
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
        """Svuota la queue degli eventi cross-thread. Ri-schedulato ogni 100ms."""
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
        # Inserisci con tag colore
        tag_name = f"col_{color.lstrip('#')}"
        try:
            self.log_text.tag_config(tag_name, foreground=color)
        except Exception:
            pass
        self.log_text.insert("end", text + "\n", tag_name)
        # Auto-scroll
        self.log_text.see("end")
        # Limit a 1000 righe per evitare growth infinito
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > 1000:
            self.log_text.delete("1.0", "200.0")
        self.log_text.configure(state="disabled")

    def _append_notification(self, pkt: NotifPacket, send_ok: bool) -> None:
        # Rimuovi placeholder se primo elemento
        if self._notif_placeholder and self._notif_placeholder.winfo_exists():
            self._notif_placeholder.destroy()
            self._notif_placeholder = None

        # Crea riga
        row = ctk.CTkFrame(self.notif_scroll, fg_color=BG_TERTIARY, corner_radius=4)
        # Pack al top (newest first)
        row.pack(fill="x", pady=2, padx=2, side="top", anchor="n")
        row.grid_columnconfigure(3, weight=1)

        # Status icon
        status_color = SUCCESS if send_ok else ERROR
        status_char = "✓" if send_ok else "✗"
        ctk.CTkLabel(
            row, text=status_char, text_color=status_color,
            font=ctk.CTkFont(size=14, weight="bold"), width=25,
        ).grid(row=0, column=0, padx=(8, 4), pady=6)

        # Timestamp
        ts = datetime.fromtimestamp(pkt.timestamp).strftime("%H:%M:%S")
        ctk.CTkLabel(
            row, text=ts, text_color=TEXT_DIM,
            font=ctk.CTkFont(family="Consolas", size=11), width=70,
        ).grid(row=0, column=1, padx=4)

        # Source
        source_label = SOURCE_LABEL.get(pkt.source, "?")
        ctk.CTkLabel(
            row, text=source_label, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=11, weight="bold"), width=110, anchor="w",
        ).grid(row=0, column=2, padx=4, sticky="w")

        # Preview
        preview_text = pkt.seed_preview[:60] + ("..." if len(pkt.seed_preview) > 60 else "")
        ctk.CTkLabel(
            row, text=preview_text, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=11), anchor="w",
        ).grid(row=0, column=3, padx=4, sticky="ew")

        # Category + priority pill
        cat_label = CATEGORY_LABEL.get(pkt.category, "?")
        pri_color = ERROR if pkt.priority == NotifPriority.HIGH else (
            WARNING if pkt.priority == NotifPriority.NORMAL else TEXT_DIM
        )
        pill = ctk.CTkLabel(
            row, text=f" {cat_label} ", text_color=pri_color,
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color=BG_PRIMARY, corner_radius=4, width=90,
        )
        pill.grid(row=0, column=4, padx=(4, 8))

        # Mantieni storia + cap 100
        self.recent_notifications.append({"pkt": pkt, "ok": send_ok, "ts": pkt.timestamp})
        if len(self.recent_notifications) > 100:
            self.recent_notifications.pop(0)
            # Rimuovi anche dalla UI (l'ultimo widget pack-side="top")
            children = self.notif_scroll.winfo_children()
            if children:
                children[-1].destroy()

    def _update_status_periodic(self) -> None:
        """Aggiorna pannello status ogni 500ms se engine running."""
        if not self.engine or not self.engine.is_running():
            return
        status = self.engine.get_status()

        # Plugin dots
        active_set = set(status.plugins_active)
        for name, dot in self.plugin_labels.items():
            if name in active_set:
                dot.configure(text_color=SUCCESS)
            else:
                dot.configure(text_color=WARNING)

        # Transport
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

        # Stats
        self.stat_sent_label.configure(text=f"Inviate:  {status.notifications_sent}")
        self.stat_failed_label.configure(text=f"Fallite:  {status.notifications_failed}")
        if status.started_at:
            uptime_sec = int(time.time() - status.started_at)
            h, rem = divmod(uptime_sec, 3600)
            m, s = divmod(rem, 60)
            self.stat_uptime_label.configure(text=f"Uptime:   {h:02d}:{m:02d}:{s:02d}")

        # Ri-schedule
        self.after(500, self._update_status_periodic)

    # ═══════════════════════════════════════════════════════════
    # Window close & Tray
    # ═══════════════════════════════════════════════════════════

    def _on_close_window(self) -> None:
        """X cliccato: minimizza in tray invece di chiudere (se tray disponibile)."""
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
        """Inizializza tray icon con pystray."""
        # Crea un'icona semplice 64x64
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
        # Run in thread separato (pystray blocca)
        import threading as _th
        _th.Thread(target=self.tray_icon.run, daemon=True).start()


def main() -> None:
    # Force UTF-8 stdout (per emoji in log)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # Logging base (così se l'utente lancia da console vede qualcosa)
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
