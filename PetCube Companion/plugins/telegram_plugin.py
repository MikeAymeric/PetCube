"""
plugins/telegram_plugin.py
Plugin Telegram: notifica DM in arrivo e messaggi in chat monitorate.
Usa Telethon come user client (non bot).

Setup richiesto (una tantum):
  1. Vai su https://my.telegram.org → "API development tools"
  2. Crea un'app → prendi api_id e api_hash
  3. Configura config.json: telegram.api_id, api_hash, phone_number
  4. Esegui `python setup_telegram_session.py` per autenticarti (OTP)
     → genera il file di sessione (es. telegram_session.session)
  5. Abilita il plugin e riavvia la companion

Config (config.json > plugins > telegram):
  {
    "enabled": true,
    "api_id": 12345,
    "api_hash": "abc...",
    "phone_number": "+39...",
    "session_file": "telegram_session",
    "poll_interval_sec": 10,
    "monitor_chat_ids": []          // [] = solo DM; [chat_id, ...] = anche questi
  }

Comportamento:
  - DM da qualsiasi contatto → priority HIGH
  - Messaggio nei chat_ids configurati → priority NORMAL
  - Menzione (@username) in gruppo → priority HIGH
"""
import asyncio
import logging
import os
import queue
import threading
from typing import Optional

from plugins.base import Plugin, RawEvent
from notification_packet import NotifSource, NotifPriority


logger = logging.getLogger(__name__)


class TelegramPlugin(Plugin):
    """
    Plugin Telegram basato su Telethon.

    Il client Telethon gira in un thread daemon dedicato con il suo event loop asyncio.
    I nuovi messaggi vengono accodati in una queue thread-safe; poll() svuota
    la coda nel thread di polling del plugin manager.
    """

    @property
    def name(self) -> str:
        return "telegram"

    def __init__(self, config: dict):
        super().__init__(config)
        self._api_id: Optional[int] = int(config["api_id"]) if config.get("api_id") else None
        self._api_hash: str = config.get("api_hash", "")
        self._phone: str = config.get("phone_number", "")
        self._session_file: str = config.get("session_file", "telegram_session")
        self._monitor_chat_ids: set[int] = {
            int(x) for x in config.get("monitor_chat_ids", [])
        }
        self._event_queue: queue.Queue[RawEvent] = queue.Queue()
        self._client_loop: Optional[asyncio.AbstractEventLoop] = None
        self._client = None
        self._bot_thread: Optional[threading.Thread] = None

        if not self._api_id or not self._api_hash:
            logger.error(
                "Telegram: 'api_id' o 'api_hash' mancanti. "
                "Ottienili su https://my.telegram.org"
            )
            return

        session_path = self._session_file + ".session"
        if not os.path.exists(session_path):
            logger.error(
                f"Telegram: file sessione '{session_path}' non trovato. "
                "Esegui 'python setup_telegram_session.py' per autenticarti."
            )
            return

        self._start_client()

    # ------------------------------------------------------------------
    # Thread management
    # ------------------------------------------------------------------

    def _start_client(self) -> None:
        self._bot_thread = threading.Thread(
            target=self._run_client_loop,
            name="telegram-client",
            daemon=True,
        )
        self._bot_thread.start()

    def _run_client_loop(self) -> None:
        try:
            from telethon import TelegramClient, events as tg_events
        except ImportError:
            logger.error(
                "Libreria Telethon non installata. "
                "Eseguire: pip install telethon"
            )
            return

        async def runner() -> None:
            client = TelegramClient(self._session_file, self._api_id, self._api_hash)
            self._client = client
            self._client_loop = asyncio.get_running_loop()

            await client.start(phone=self._phone)
            me = await client.get_me()
            my_username = (me.username or "").lower() if me else ""
            logger.info(
                f"📱 Telegram connesso come {getattr(me, 'first_name', '?')} "
                f"(@{my_username})"
            )

            @client.on(tg_events.NewMessage(incoming=True))
            async def on_message(event) -> None:
                msg_id = str(event.message.id)
                if msg_id in self._seen_set:
                    return

                sender = await event.get_sender()
                text = (event.message.text or event.message.message or "").strip()
                chat_id = event.chat_id

                # Determina se il messaggio va notificato
                if event.is_private:
                    # DM personale → sempre HIGH
                    sender_name = getattr(sender, "first_name", None) or "?"
                    preview = f"{sender_name}: {text[:60]}" if text else f"DM da {sender_name}"
                    priority = NotifPriority.HIGH
                elif chat_id in self._monitor_chat_ids:
                    sender_name = getattr(sender, "first_name", None) or getattr(sender, "title", "?")
                    chat = await event.get_chat()
                    chat_name = getattr(chat, "title", "Gruppo")
                    preview = f"{sender_name} in {chat_name}: {text[:50]}" if text else f"Messaggio in {chat_name}"
                    priority = NotifPriority.NORMAL
                    # Upgrade a HIGH se menzione diretta
                    if my_username and f"@{my_username}" in text.lower():
                        priority = NotifPriority.HIGH
                else:
                    return  # chat non monitorata

                raw = RawEvent(
                    source=NotifSource.TELEGRAM,
                    priority=priority,
                    text=preview,
                    external_id=msg_id,
                )
                self._event_queue.put(raw)

            try:
                await client.run_until_disconnected()
            except Exception as e:
                logger.warning(f"Telegram disconnesso: {e}")

        try:
            asyncio.run(runner())
        except Exception as e:
            logger.error(f"Telegram client errore fatale: {e}")

    # ------------------------------------------------------------------
    # Plugin interface
    # ------------------------------------------------------------------

    def poll(self) -> list[RawEvent]:
        events: list[RawEvent] = []
        try:
            while True:
                raw: RawEvent = self._event_queue.get_nowait()
                if raw.external_id not in self.seen_ids:
                    self.seen_ids.add(raw.external_id)
                    events.append(raw)
                    logger.info(f"📱 Telegram {raw.priority.name}: {raw.text!r}")
        except queue.Empty:
            pass
        return events

    def shutdown(self) -> None:
        if self._client is not None and self._client_loop is not None:
            if not self._client_loop.is_closed():
                try:
                    fut = asyncio.run_coroutine_threadsafe(
                        self._client.disconnect(), self._client_loop
                    )
                    fut.result(timeout=5)
                    logger.info("Telegram client disconnesso.")
                except Exception as e:
                    logger.warning(f"Telegram shutdown: {e}")
        super().shutdown()
