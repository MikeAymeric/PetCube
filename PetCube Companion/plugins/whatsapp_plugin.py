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
    "headless": true
  }

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


class WhatsAppPlugin(Plugin):
    """
    Plugin WhatsApp tramite Playwright (Chromium persistente).

    Il browser gira in un thread daemon dedicato; poll() legge la queue
    degli eventi rilevati durante l'ultima scansione DOM.
    """

    @property
    def name(self) -> str:
        return "whatsapp"

    def __init__(self, config: dict):
        super().__init__(config)
        self._session_dir: str = config.get("session_dir", "whatsapp_session")
        self._headless: bool = bool(config.get("headless", True))
        self._event_queue: queue.Queue[RawEvent] = queue.Queue()
        self._page = None
        self._connected = False
        self._browser_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

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
                # Attesa autenticazione: QR scan o caricamento sessione salvata
                page.wait_for_selector(_SEL_APP_READY, timeout=180_000)
                logger.info("✅ WhatsApp Web connesso.")

                with self._lock:
                    self._page = page
                    self._connected = True

                # Mantieni il browser vivo finché il thread non viene terminato
                while True:
                    time.sleep(5)
                    if page.is_closed():
                        break

            except Exception as e:
                logger.error(f"WhatsApp browser errore: {e}")
            finally:
                with self._lock:
                    self._connected = False
                    self._page = None

    # ------------------------------------------------------------------
    # Plugin interface
    # ------------------------------------------------------------------

    def poll(self) -> list[RawEvent]:
        with self._lock:
            if not self._connected or self._page is None:
                return []
            page = self._page

        events: list[RawEvent] = []
        try:
            # Trova tutte le chat con badge non letto
            badges = page.query_selector_all(
                f'{_SEL_CHAT_LIST} {_SEL_UNREAD_BADGE}'
            )
            for badge in badges:
                try:
                    # Risale al contenitore della chat
                    cell = badge.evaluate_handle(
                        "el => el.closest('[data-testid=\"cell-frame-container\"]')"
                    ).as_element()
                    if cell is None:
                        continue

                    title_el = cell.query_selector(_SEL_CHAT_TITLE)
                    if title_el is None:
                        continue
                    chat_name = (title_el.inner_text() or "?").strip()

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
                    events.append(RawEvent(
                        source=NotifSource.WHATSAPP,
                        priority=NotifPriority.NORMAL,
                        text=preview,
                        external_id=msg_id,
                    ))
                    logger.info(f"💬 WhatsApp nuovo messaggio: {preview!r}")

                except Exception as e:
                    logger.debug(f"WhatsApp: errore parsing chat badge: {e}")
                    continue

        except Exception as e:
            logger.warning(f"WhatsApp poll error: {e}")

        return events

    def shutdown(self) -> None:
        with self._lock:
            self._connected = False
        super().shutdown()
