"""
plugins/whatsapp_plugin.py
Plugin WhatsApp: monitora messaggi non letti su WhatsApp Web via Playwright.

Setup richiesto (una tantum):
  1. Installa dipendenze:
       pip install playwright
       playwright install chromium
  2. Prima esecuzione: imposta headless=false in config.json e avvia la companion
     → Chromium si apre su WhatsApp Web → scansiona il QR col telefono
     → la sessione viene salvata nella cartella 'session_dir'
  3. Dopo la scansione riporta headless=true e riavvia

Config (config.json > plugins > whatsapp):
  {
    "enabled": true,
    "session_dir": "whatsapp_session",
    "poll_interval_sec": 30,
    "headless": true,
    "monitor_chats": []
  }

Architettura threading:
  Tutto il codice Playwright gira nel thread 'whatsapp-browser'.
  Il DOM viene scansionato ogni poll_interval_sec secondi dal browser thread
  stesso, che deposita i RawEvent in _event_queue.
  poll() (chiamato dal plugin manager in un thread diverso) si limita a
  drenare la coda — nessuna chiamata Playwright cross-thread.

Limitazioni:
  - Usa selettori DOM di WhatsApp Web (data-testid) — potrebbero cambiare con
    aggiornamenti di WhatsApp. In caso di rot: aggiornare i selettori qui sotto.
  - Il telefono deve essere connesso a Internet (requisito WhatsApp Web).
  - external_id basato su nome chat + anteprima msg → non garantisce unicità
    assoluta in caso di messaggi identici consecutivi.
"""
import logging
import queue
import threading
import time
from typing import Optional

from plugins.base import Plugin, RawEvent
from notification_packet import NotifSource, NotifPriority


logger = logging.getLogger(__name__)

# Selettori DOM WhatsApp Web (versione maggio 2026)
_SEL_APP_READY    = '[data-testid="chatlist-header"]'
_SEL_CHAT_LIST    = '[data-testid="chat-list"]'
_SEL_UNREAD_BADGE = '[data-testid="icon-unread-count"]'
_SEL_CHAT_CELL    = '[data-testid="cell-frame-container"]'
_SEL_CHAT_TITLE   = '[data-testid="cell-frame-title"]'
_SEL_LAST_MSG     = '[data-testid="last-msg-status"] ~ span, [data-testid="last-msg-status"]'

# JavaScript per rilevare se una cella è un gruppo o un DM.
# WhatsApp Web usa data-icon (SVG inline) — non data-testid — per le icone
# avatar di default. I gruppi CON foto custom non hanno icone, ma nel preview
# mostrano "Mittente: testo" (span vuoto con testo che termina con ':').
_JS_IS_GROUP = """el => {
    // 1. Icone avatar default (gruppi/canali senza foto custom)
    const iconSels = [
        '[data-icon="default-group"]',
        '[data-icon="channel"]',
        '[data-icon="community"]',
        '[data-testid="default-group"]',   // fallback versioni future
        '[data-testid="icon-channel"]',
    ];
    if (iconSels.some(s => !!el.querySelector(s))) return true;
    // 2. Gruppi CON foto: l'anteprima mostra uno span leaf col solo mittente
    //    seguito da ':' (es. "Mario:", "You:"). I DM non hanno questo span.
    const secondary = el.querySelector('[data-testid="cell-frame-secondary"]');
    if (secondary) {
        const spans = secondary.querySelectorAll('span');
        for (const s of spans) {
            if (s.childElementCount === 0) {
                const t = s.textContent.trim();
                if (t && t.endsWith(':')) return true;
            }
        }
    }
    return false;
}"""


class WhatsAppPlugin(Plugin):
    """
    Plugin WhatsApp tramite Playwright (Chromium persistente).

    Il browser gira in un thread daemon dedicato che esegue sia la connessione
    che la scansione periodica del DOM.  poll() si limita a drenare la coda
    degli eventi — nessuna chiamata Playwright cross-thread.
    """

    @property
    def name(self) -> str:
        return "whatsapp"

    def __init__(self, config: dict):
        super().__init__(config)
        self._session_dir: str = config.get("session_dir", "whatsapp_session")
        self._headless: bool = bool(config.get("headless", True))
        self._poll_interval: int = int(config.get("poll_interval_sec", 30))
        # monitor_chats: lista di nomi (case-insensitive, sottostringa).
        # Vuota = tutti i gruppi. I DM passano sempre.
        # strip('"\'') rimuove virgolette accidentali inserite dalla GUI.
        raw_chats = config.get("monitor_chats", [])
        self._monitor_chats: list[str] = [
            c.strip().strip("\"'").lower()
            for c in raw_chats
            if c.strip().strip("\"'")
        ]

        self._event_queue: queue.Queue[RawEvent] = queue.Queue()
        self._connected = False
        self._stop_event = threading.Event()
        self._browser_thread: Optional[threading.Thread] = None

        if self._monitor_chats:
            logger.info(
                f"WhatsApp: filtro gruppi/canali attivo su {len(self._monitor_chats)} nomi: "
                f"{self._monitor_chats} (i DM passano sempre)"
            )
        else:
            logger.info("WhatsApp: nessun filtro — notifica tutte le chat con messaggi non letti.")

        self._start_browser_thread()

    # ------------------------------------------------------------------
    # Thread management
    # ------------------------------------------------------------------

    def _start_browser_thread(self) -> None:
        self._browser_thread = threading.Thread(
            target=self._run_browser,
            name="whatsapp-browser",
            daemon=True,
        )
        self._browser_thread.start()

    def _run_browser(self) -> None:
        """
        Gira interamente nel thread 'whatsapp-browser'.
        Tutte le chiamate Playwright avvengono qui — mai cross-thread.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error(
                "Playwright non installato. "
                "Eseguire: pip install playwright && playwright install chromium"
            )
            return

        with sync_playwright() as pw:
            try:
                context = pw.chromium.launch_persistent_context(
                    self._session_dir,
                    headless=self._headless,
                    no_viewport=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")

                logger.info(
                    "WhatsApp Web: in attesa di autenticazione "
                    f"({'headless' if self._headless else 'visibile — scansiona QR'})"
                    "..."
                )
                page.wait_for_selector(_SEL_APP_READY, timeout=180_000)
                logger.info("✅ WhatsApp Web connesso.")
                self._connected = True

                # Loop di scansione — tutto dentro questo thread
                while not self._stop_event.is_set():
                    if page.is_closed():
                        break
                    self._scan_page(page)
                    # Attendi poll_interval usando lo stop_event per uscire subito
                    self._stop_event.wait(timeout=self._poll_interval)

            except Exception as e:
                logger.error(f"WhatsApp browser errore: {e}")
            finally:
                self._connected = False

    def _scan_page(self, page) -> None:
        """
        Scansiona il DOM alla ricerca di badge non letti.
        Chiamato esclusivamente dal browser thread.
        """
        try:
            badges = page.query_selector_all(
                f'{_SEL_CHAT_LIST} {_SEL_UNREAD_BADGE}'
            )
            for badge in badges:
                try:
                    cell = badge.evaluate_handle(
                        "el => el.closest('[data-testid=\"cell-frame-container\"]')"
                    ).as_element()
                    if cell is None:
                        continue

                    title_el = cell.query_selector(_SEL_CHAT_TITLE)
                    if title_el is None:
                        continue
                    chat_name = (title_el.inner_text() or "?").strip()

                    # Distingui gruppo/canale da DM tramite JS evaluate
                    is_group: bool = cell.evaluate(_JS_IS_GROUP)
                    logger.debug(
                        f"WhatsApp chat rilevata: {chat_name!r} "
                        f"tipo={'gruppo' if is_group else 'DM'}"
                    )

                    # Filtra per monitor_chats solo su gruppi/canali;
                    # i DM passano sempre indipendentemente dal filtro
                    if self._monitor_chats and is_group:
                        name_lower = chat_name.lower()
                        if not any(f in name_lower for f in self._monitor_chats):
                            logger.debug(
                                f"WhatsApp: gruppo {chat_name!r} escluso dal filtro "
                                f"monitor_chats={self._monitor_chats}"
                            )
                            continue

                    # Tenta di leggere l'ultimo messaggio visibile nel preview
                    last_msg = ""
                    spans = cell.query_selector_all("span[dir]")
                    for span in spans:
                        t = (span.inner_text() or "").strip()
                        if t and len(t) > 2:
                            last_msg = t[:60]
                            break

                    msg_id = f"wa_{chat_name}_{last_msg[:20]}"
                    if msg_id in self.seen_ids:
                        continue

                    self.seen_ids.add(msg_id)
                    preview = f"{chat_name}: {last_msg}" if last_msg else f"Messaggio da {chat_name}"
                    chat_type = "gruppo" if is_group else "DM"
                    self._event_queue.put(RawEvent(
                        source=NotifSource.WHATSAPP,
                        priority=NotifPriority.NORMAL,
                        text=preview,
                        external_id=msg_id,
                    ))
                    logger.info(f"💬 WhatsApp [{chat_type}] nuovo messaggio: {preview!r}")

                except Exception as e:
                    logger.debug(f"WhatsApp: errore parsing chat badge: {e}")
                    continue

        except Exception as e:
            logger.warning(f"WhatsApp scan error: {e}")

    # ------------------------------------------------------------------
    # Plugin interface
    # ------------------------------------------------------------------

    def poll(self) -> list[RawEvent]:
        """Drena la coda eventi prodotta dal browser thread. Nessuna chiamata Playwright."""
        events: list[RawEvent] = []
        try:
            while True:
                events.append(self._event_queue.get_nowait())
        except queue.Empty:
            pass
        return events

    def shutdown(self) -> None:
        self._stop_event.set()
        super().shutdown()
