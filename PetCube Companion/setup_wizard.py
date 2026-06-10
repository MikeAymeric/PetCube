"""
setup_wizard.py
Wizard guidato (CustomTkinter) per creare o modificare config.json
della Companion App.

Usato in due scenari:
  - Primo avvio (config.json assente): run_first_setup() apre il wizard
    come finestra principale e ritorna il config salvato.
  - Riconfigurazione da app già avviata: open_wizard() apre il wizard
    come finestra modale sopra la GUI principale.
"""
import json
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

from config_schema import (
    PLUGIN_FIELDS, PLUGIN_DISPLAY_NAME, PLUGIN_HELP, PLUGIN_ORDER,
    default_config, value_to_str, parse_field_value,
)

# ── Dark theme palette (coerente con gui.py) ───────────────────
BG_PRIMARY    = "#1e1e1e"
BG_SECONDARY  = "#252526"
BG_TERTIARY   = "#2d2d30"
ACCENT        = "#0e639c"
ACCENT_HOVER  = "#1177bb"
TEXT_PRIMARY  = "#cccccc"
TEXT_DIM      = "#858585"
BORDER        = "#3e3e42"

CONFIG_PATH = Path("config.json")


def _merge_config(base: dict, override: dict) -> dict:
    """Deep-merge di override su base (override vince). Ritorna un nuovo dict."""
    result = dict(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_config(result[key], val)
        else:
            result[key] = val
    return result


class WizardFrame(ctk.CTkFrame):
    """Contenuto del wizard: pagine + navigazione, embeddabile in CTk o CTkToplevel."""

    def __init__(self, master, existing_config: Optional[dict],
                 on_finish: Callable[[dict], None],
                 on_cancel: Callable[[], None]):
        super().__init__(master, fg_color=BG_PRIMARY)
        self._on_finish = on_finish
        self._on_cancel = on_cancel
        self._config = _merge_config(default_config(), existing_config or {})

        device_cfg = self._config["device"]
        transport_cfg = self._config["transport"]
        logging_cfg = self._config["logging"]
        firmware_cfg = self._config["firmware"]

        self._sv_ble_name = ctk.StringVar(value=device_cfg.get("ble_name", "PetCube"))
        self._sv_wifi_url = ctk.StringVar(value=device_cfg.get("wifi_fallback_url", ""))
        self._sv_transport_prefer = ctk.StringVar(value=transport_cfg.get("prefer", "ble"))
        self._sv_transport_timeout = ctk.StringVar(value=str(transport_cfg.get("ble_scan_timeout_sec", 10)))
        self._sv_log_level = ctk.StringVar(value=logging_cfg.get("level", "INFO"))
        self._sv_gh_owner = ctk.StringVar(value=firmware_cfg.get("github_owner", "MikeAymeric"))
        self._sv_gh_repo = ctk.StringVar(value=firmware_cfg.get("github_repo", "PetCube"))

        self._sv_plugins: dict[str, dict[str, ctk.Variable]] = {}
        self._summary_label: Optional[ctk.CTkLabel] = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()
        self._content = ctk.CTkFrame(self, fg_color="transparent")
        self._content.grid(row=1, column=0, sticky="nsew", padx=20, pady=10)
        self._content.grid_columnconfigure(0, weight=1)
        self._content.grid_rowconfigure(0, weight=1)

        self._pages: list[ctk.CTkFrame] = []
        self._page_titles: list[str] = []
        self._build_pages()

        self._build_nav()
        self._index = 0
        self._show_page(0)

    # ── struttura ──────────────────────────────────────────────

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=BG_SECONDARY, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")

        ctk.CTkLabel(
            header, text="Configurazione PetCube Companion",
            font=ctk.CTkFont(size=16, weight="bold"), text_color=TEXT_PRIMARY,
        ).pack(side="left", padx=15, pady=12)

        self._step_label = ctk.CTkLabel(
            header, text="", font=ctk.CTkFont(size=12), text_color=TEXT_DIM,
        )
        self._step_label.pack(side="right", padx=15, pady=12)

    def _build_nav(self) -> None:
        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 15))
        nav.grid_columnconfigure(2, weight=1)

        self._btn_cancel = ctk.CTkButton(
            nav, text="Annulla", width=100, fg_color=BG_TERTIARY, hover_color="#444",
            command=self._on_cancel,
        )
        self._btn_cancel.grid(row=0, column=0, sticky="w")

        self._btn_back = ctk.CTkButton(
            nav, text="◀ Indietro", width=110, fg_color=BG_TERTIARY, hover_color="#444",
            command=self._go_back,
        )
        self._btn_back.grid(row=0, column=1, sticky="w", padx=(10, 0))

        self._btn_next = ctk.CTkButton(
            nav, text="Avanti ▶", width=130, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._go_next,
        )
        self._btn_next.grid(row=0, column=3, sticky="e")

    def _add_page(self, title: str, builder: Callable[[ctk.CTkFrame], None]) -> None:
        page = ctk.CTkFrame(self._content, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        builder(page)
        self._pages.append(page)
        self._page_titles.append(title)

    def _build_pages(self) -> None:
        self._add_page("Benvenuto", self._build_page_welcome)
        self._add_page("Dispositivo e connessione", self._build_page_device)
        self._add_page("Sorgenti di notifica", self._build_page_plugins)
        self._add_page("Log e aggiornamenti", self._build_page_misc)
        self._add_page("Riepilogo", self._build_page_summary)

    # ── pagine ─────────────────────────────────────────────────

    def _build_page_welcome(self, page) -> None:
        ctk.CTkLabel(
            page, text="👋  Benvenuto!",
            font=ctk.CTkFont(size=22, weight="bold"), text_color=TEXT_PRIMARY,
        ).pack(anchor="w", pady=(20, 10))

        text = (
            "Questo wizard ti guida nella configurazione di PetCube Companion:\n\n"
            "  •  Nome BLE e connessione con il cubo\n"
            "  •  Sorgenti di notifica da attivare (Calendar, Gmail, Discord, ...)\n"
            "  •  Livello di log e repository GitHub per gli aggiornamenti\n\n"
            "Puoi sempre modificare queste impostazioni in seguito dalla scheda\n"
            "\"Impostazioni\" della Companion App.\n\n"
            "Premi \"Avanti\" per iniziare."
        )
        ctk.CTkLabel(
            page, text=text, justify="left", anchor="w",
            font=ctk.CTkFont(size=13), text_color=TEXT_DIM,
        ).pack(anchor="w", fill="x")

    def _build_page_device(self, page) -> None:
        ctk.CTkLabel(
            page, text="Dispositivo e connessione",
            font=ctk.CTkFont(size=18, weight="bold"), text_color=TEXT_PRIMARY,
        ).pack(anchor="w", pady=(10, 15))

        frame = ctk.CTkFrame(page, fg_color=BG_TERTIARY, corner_radius=8)
        frame.pack(fill="x")
        frame.grid_columnconfigure(1, weight=1)

        self._field_row(frame, 0, "Nome BLE del cubo", self._sv_ble_name, "text",
                         "Deve corrispondere al nome advertito dal firmware (default \"PetCube\").")
        self._field_row(frame, 1, "WiFi Fallback URL", self._sv_wifi_url, "text",
                         "Opzionale: endpoint HTTP usato se il trasporto è \"wifi\".")

        ctk.CTkLabel(
            frame, text="Modalità trasporto", anchor="w",
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12), width=200,
        ).grid(row=2, column=0, padx=(12, 8), pady=(10, 6), sticky="w")
        ctk.CTkOptionMenu(
            frame, values=["ble", "wifi", "auto"],
            variable=self._sv_transport_prefer,
            fg_color=BG_PRIMARY, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(size=12),
        ).grid(row=2, column=1, padx=(0, 12), pady=(10, 6), sticky="w")

        self._field_row(frame, 3, "BLE scan timeout (sec)", self._sv_transport_timeout, "int")

    def _build_page_plugins(self, page) -> None:
        ctk.CTkLabel(
            page, text="Sorgenti di notifica",
            font=ctk.CTkFont(size=18, weight="bold"), text_color=TEXT_PRIMARY,
        ).pack(anchor="w", pady=(10, 5))
        ctk.CTkLabel(
            page,
            text="Attiva le sorgenti che vuoi inoltrare al cubo. Potrai completare\n"
                 "le credenziali in qualsiasi momento dalla scheda Impostazioni.",
            justify="left", anchor="w", font=ctk.CTkFont(size=12), text_color=TEXT_DIM,
        ).pack(anchor="w", pady=(0, 10))

        scroll = ctk.CTkScrollableFrame(page, fg_color="transparent", height=320)
        scroll.pack(fill="both", expand=True)
        scroll.grid_columnconfigure(0, weight=1)

        plugins_cfg = self._config.get("plugins", {})
        for plugin_name in PLUGIN_ORDER:
            self._build_plugin_card(scroll, plugin_name, plugins_cfg.get(plugin_name, {}))

    def _build_plugin_card(self, parent, plugin_name: str, pcfg: dict) -> None:
        enabled_val = bool(pcfg.get("enabled", False))
        bv = ctk.BooleanVar(value=enabled_val)
        fields_specs = PLUGIN_FIELDS.get(plugin_name, [])

        plugin_vars: dict[str, ctk.Variable] = {"enabled": bv}
        self._sv_plugins[plugin_name] = plugin_vars

        card = ctk.CTkFrame(parent, fg_color=BG_TERTIARY, corner_radius=8)
        card.pack(fill="x", pady=(0, 8))
        card.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=8)
        header.grid_columnconfigure(0, weight=1)

        display_name = PLUGIN_DISPLAY_NAME.get(plugin_name, plugin_name.capitalize())
        ctk.CTkLabel(
            header, text=display_name, anchor="w",
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        help_text = PLUGIN_HELP.get(plugin_name, "")
        if help_text:
            ctk.CTkLabel(
                header, text=help_text, anchor="w", justify="left",
                text_color=TEXT_DIM, font=ctk.CTkFont(size=11), wraplength=480,
            ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        if fields_specs:
            detail_frame = ctk.CTkFrame(card, fg_color="transparent")
            detail_frame.grid_columnconfigure(1, weight=1)

            for row_idx, (key, label, field_type) in enumerate(fields_specs):
                raw_val = pcfg.get(key)
                sv = ctk.StringVar(value=value_to_str(raw_val, field_type))
                plugin_vars[key] = sv
                self._field_row(detail_frame, row_idx, label, sv, field_type)

            def make_toggle(dframe):
                def toggle(val):
                    if val:
                        dframe.grid(row=2, column=0, sticky="ew", padx=0, pady=(0, 8))
                    else:
                        dframe.grid_remove()
                return toggle

            toggle_fn = make_toggle(detail_frame)
            switch = ctk.CTkSwitch(
                header, text="", variable=bv, onvalue=True, offvalue=False,
                command=lambda fn=toggle_fn, b=bv: fn(b.get()),
                fg_color=BORDER, progress_color=ACCENT,
            )
            switch.grid(row=0, column=1, rowspan=2, sticky="e")

            if enabled_val:
                detail_frame.grid(row=2, column=0, sticky="ew", padx=0, pady=(0, 8))
        else:
            switch = ctk.CTkSwitch(
                header, text="", variable=bv, onvalue=True, offvalue=False,
                fg_color=BORDER, progress_color=ACCENT,
            )
            switch.grid(row=0, column=1, rowspan=2, sticky="e")

    def _build_page_misc(self, page) -> None:
        ctk.CTkLabel(
            page, text="Log e aggiornamenti",
            font=ctk.CTkFont(size=18, weight="bold"), text_color=TEXT_PRIMARY,
        ).pack(anchor="w", pady=(10, 15))

        log_frame = ctk.CTkFrame(page, fg_color=BG_TERTIARY, corner_radius=8)
        log_frame.pack(fill="x", pady=(0, 12))
        log_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            log_frame, text="Livello log", anchor="w",
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12), width=200,
        ).grid(row=0, column=0, padx=(12, 8), pady=8, sticky="w")
        ctk.CTkOptionMenu(
            log_frame, values=["DEBUG", "INFO", "WARNING", "ERROR"],
            variable=self._sv_log_level,
            fg_color=BG_PRIMARY, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(size=12),
        ).grid(row=0, column=1, padx=(0, 12), pady=8, sticky="w")

        gh_frame = ctk.CTkFrame(page, fg_color=BG_TERTIARY, corner_radius=8)
        gh_frame.pack(fill="x")
        gh_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            gh_frame,
            text="Repository GitHub usato per controllare gli aggiornamenti del\n"
                 "firmware del cubo e della Companion App stessa.",
            anchor="w", justify="left",
            text_color=TEXT_DIM, font=ctk.CTkFont(size=11), wraplength=480,
        ).grid(row=0, column=0, columnspan=2, padx=12, pady=(10, 4), sticky="w")

        self._field_row(gh_frame, 1, "Owner", self._sv_gh_owner, "text")
        self._field_row(gh_frame, 2, "Repository", self._sv_gh_repo, "text")

    def _build_page_summary(self, page) -> None:
        ctk.CTkLabel(
            page, text="Riepilogo",
            font=ctk.CTkFont(size=18, weight="bold"), text_color=TEXT_PRIMARY,
        ).pack(anchor="w", pady=(10, 15))

        self._summary_label = ctk.CTkLabel(
            page, text="", justify="left", anchor="nw",
            font=ctk.CTkFont(size=12, family="Consolas"), text_color=TEXT_PRIMARY,
        )
        self._summary_label.pack(anchor="nw", fill="both", expand=True)

    # ── helper UI ──────────────────────────────────────────────

    def _field_row(self, parent, row_idx: int, label: str,
                    sv: ctk.StringVar, field_type: str, hint: str = "") -> None:
        ctk.CTkLabel(
            parent, text=label, anchor="w",
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12), width=200,
        ).grid(row=row_idx, column=0, padx=(12, 8), pady=6, sticky="w")

        show_char = "*" if field_type == "password" else ""
        entry = ctk.CTkEntry(
            parent, textvariable=sv,
            fg_color=BG_PRIMARY, border_color=BORDER,
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12),
            show=show_char,
        )
        entry.grid(row=row_idx, column=1, padx=(0, 12), pady=6, sticky="ew")

        if hint:
            ctk.CTkLabel(
                parent, text=hint, anchor="w", justify="left",
                text_color=TEXT_DIM, font=ctk.CTkFont(size=10), wraplength=480,
            ).grid(row=row_idx, column=1, padx=(0, 12), pady=(28, 0), sticky="nw")

    # ── navigazione ────────────────────────────────────────────

    def _show_page(self, index: int) -> None:
        for page in self._pages:
            page.grid_forget()
        self._pages[index].grid(row=0, column=0, sticky="nsew")
        self._index = index
        self._step_label.configure(
            text=f"Passo {index + 1}/{len(self._pages)}  —  {self._page_titles[index]}"
        )
        self._btn_back.configure(state="disabled" if index == 0 else "normal")
        is_last = index == len(self._pages) - 1
        self._btn_next.configure(text="✓  Salva" if is_last else "Avanti ▶")
        if is_last:
            self._refresh_summary()

    def _go_back(self) -> None:
        if self._index > 0:
            self._show_page(self._index - 1)

    def _go_next(self) -> None:
        if self._index < len(self._pages) - 1:
            self._show_page(self._index + 1)
        else:
            self._finish()

    # ── salvataggio ────────────────────────────────────────────

    def _collect_config(self) -> dict:
        cfg = _merge_config(default_config(), self._config)

        cfg["device"]["ble_name"] = self._sv_ble_name.get().strip() or "PetCube"
        cfg["device"]["wifi_fallback_url"] = self._sv_wifi_url.get().strip()

        cfg["transport"]["prefer"] = self._sv_transport_prefer.get()
        try:
            cfg["transport"]["ble_scan_timeout_sec"] = int(self._sv_transport_timeout.get())
        except ValueError:
            cfg["transport"]["ble_scan_timeout_sec"] = 10

        cfg["logging"]["level"] = self._sv_log_level.get()

        cfg["firmware"]["github_owner"] = self._sv_gh_owner.get().strip() or "MikeAymeric"
        cfg["firmware"]["github_repo"] = self._sv_gh_repo.get().strip() or "PetCube"

        plugins_cfg = cfg.setdefault("plugins", {})
        for plugin_name, vars_dict in self._sv_plugins.items():
            pcfg = plugins_cfg.setdefault(plugin_name, {})
            pcfg["enabled"] = bool(vars_dict["enabled"].get())
            for key, var in vars_dict.items():
                if key == "enabled":
                    continue
                specs = PLUGIN_FIELDS.get(plugin_name, [])
                field_spec = next((f for f in specs if f[0] == key), None)
                if field_spec:
                    pcfg[key] = parse_field_value(var.get(), field_spec[2])

        return cfg

    def _refresh_summary(self) -> None:
        cfg = self._collect_config()
        lines = [
            f"Nome BLE:         {cfg['device']['ble_name']}",
            f"Trasporto:        {cfg['transport']['prefer']}  (timeout {cfg['transport']['ble_scan_timeout_sec']}s)",
            f"Log level:        {cfg['logging']['level']}",
            f"Repo aggiornam.:  {cfg['firmware']['github_owner']}/{cfg['firmware']['github_repo']}",
            "",
            "Sorgenti attive:",
        ]
        active = [PLUGIN_DISPLAY_NAME.get(name, name) for name in PLUGIN_ORDER
                  if cfg["plugins"].get(name, {}).get("enabled")]
        if active:
            lines += [f"  ✓ {name}" for name in active]
        else:
            lines.append("  (nessuna)")
        lines.append("")
        lines.append('Premi "Salva" per scrivere config.json.')
        self._summary_label.configure(text="\n".join(lines))

    def _finish(self) -> None:
        self._on_finish(self._collect_config())


def save_config(cfg: dict, path: Path = CONFIG_PATH) -> None:
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def run_first_setup() -> Optional[dict]:
    """
    Mostra il wizard come finestra principale (config.json assente).
    Ritorna il dict di config salvato, oppure None se annullato.
    """
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")

    root = ctk.CTk()
    root.title("PetCube Companion — Configurazione iniziale")
    root.geometry("780x620")
    root.minsize(700, 560)
    root.configure(fg_color=BG_PRIMARY)
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(0, weight=1)

    result: dict = {"config": None}

    def on_finish(cfg: dict) -> None:
        save_config(cfg)
        result["config"] = cfg
        root.destroy()

    def on_cancel() -> None:
        root.destroy()

    frame = WizardFrame(root, existing_config=None, on_finish=on_finish, on_cancel=on_cancel)
    frame.grid(row=0, column=0, sticky="nsew")
    root.protocol("WM_DELETE_WINDOW", on_cancel)

    root.mainloop()
    return result["config"]


def open_wizard(parent, existing_config: dict, on_done: Callable[[dict], None]) -> None:
    """
    Apre il wizard come finestra modale per riconfigurare un'app già avviata.
    on_done viene chiamato con il nuovo dict di config solo se l'utente salva.
    """
    top = ctk.CTkToplevel(parent)
    top.title("PetCube Companion — Configurazione")
    top.geometry("780x620")
    top.minsize(700, 560)
    top.configure(fg_color=BG_PRIMARY)
    top.grid_columnconfigure(0, weight=1)
    top.grid_rowconfigure(0, weight=1)
    top.transient(parent)
    top.grab_set()

    def on_finish(cfg: dict) -> None:
        save_config(cfg)
        top.grab_release()
        top.destroy()
        on_done(cfg)

    def on_cancel() -> None:
        top.grab_release()
        top.destroy()

    frame = WizardFrame(top, existing_config=existing_config, on_finish=on_finish, on_cancel=on_cancel)
    frame.grid(row=0, column=0, sticky="nsew")
    top.protocol("WM_DELETE_WINDOW", on_cancel)
