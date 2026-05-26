"""
plugins/instagram_plugin.py
Plugin Instagram: notifica DM non letti e menzioni nelle storie/commenti.
Usa instagrapi (API privata Instagram).

Setup richiesto:
  1. pip install instagrapi
  2. Configura config.json: instagram.username, instagram.password
  3. Prima connessione: instagrapi tenta il login e salva la sessione in
     session_file. Se Instagram richiede verifica (2FA / challenge),
     eseguire `python setup_instagram_session.py` per completarla manualmente.

Config (config.json > plugins > instagram):
  {
    "enabled": true,
    "username": "tuo_username",
    "password": "tua_password",
    "session_file": "instagram_session.json",
    "poll_interval_sec": 300,
    "monitor_dms": true,
    "monitor_mentions": true
  }

Note:
  - poll_interval_sec consigliato: ≥ 300 (5 min) per evitare rate-limit/ban.
  - instagrapi usa le API private di Instagram. Usa un account secondario
    se vuoi essere cauto, o accetta il rischio di account challenges.
  - Le notifiche "menzione" coprono menzioni nei commenti e nelle storie.
"""
import logging
import os
import time
from typing import Optional

from plugins.base import Plugin, RawEvent
from notification_packet import NotifSource, NotifPriority


logger = logging.getLogger(__name__)


class InstagramPlugin(Plugin):
    """
    Plugin Instagram con polling regolare (no thread separato — instagrapi è sincrono).
    Monitora DM non letti e notifiche di menzione.
    """

    @property
    def name(self) -> str:
        return "instagram"

    def __init__(self, config: dict):
        super().__init__(config)
        self._username: str = config.get("username", "")
        self._password: str = config.get("password", "")
        self._session_file: str = config.get("session_file", "instagram_session.json")
        self._monitor_dms: bool = bool(config.get("monitor_dms", True))
        self._monitor_mentions: bool = bool(config.get("monitor_mentions", True))
        self._client = None

        if not self._username or not self._password:
            logger.error("Instagram: 'username' o 'password' mancanti in config.json.")
            return

        self._connect()

    # ------------------------------------------------------------------
    # Connessione
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        try:
            from instagrapi import Client
        except ImportError:
            logger.error(
                "Libreria instagrapi non installata. "
                "Eseguire: pip install instagrapi"
            )
            return

        cl = Client()
        cl.delay_range = [2, 5]   # ritardo casuale tra request (anti-ban)

        if os.path.exists(self._session_file):
            try:
                cl.load_settings(self._session_file)
                cl.login(self._username, self._password)
                logger.info(f"📸 Instagram: sessione caricata da {self._session_file}")
                self._client = cl
                return
            except Exception as e:
                logger.warning(f"Instagram: sessione salvata non valida ({e}), ri-login...")

        try:
            cl.login(self._username, self._password)
            cl.dump_settings(self._session_file)
            logger.info(f"📸 Instagram: login OK, sessione salvata in {self._session_file}")
            self._client = cl
        except Exception as e:
            logger.error(
                f"Instagram: login fallito — {e}. "
                "Se Instagram richiede verifica, esegui 'python setup_instagram_session.py'."
            )

    # ------------------------------------------------------------------
    # Plugin interface
    # ------------------------------------------------------------------

    def poll(self) -> list[RawEvent]:
        if self._client is None:
            return []

        events: list[RawEvent] = []

        # ── DM non letti ──────────────────────────────────────────────
        if self._monitor_dms:
            try:
                threads = self._client.direct_threads(amount=20)
                for thread in threads:
                    if not getattr(thread, "unread_count", 0):
                        continue
                    thread_id = str(thread.id)
                    if thread_id in self.seen_ids:
                        continue

                    # Recupera il primo messaggio non visto
                    try:
                        msgs = self._client.direct_messages(thread_id, amount=1)
                        last_msg = msgs[0] if msgs else None
                    except Exception:
                        last_msg = None

                    # Identifica mittente
                    users = getattr(thread, "users", [])
                    sender_name = users[0].username if users else "?"

                    if last_msg:
                        raw_text = getattr(last_msg, "text", None) or ""
                        preview = f"{sender_name}: {raw_text[:60]}" if raw_text else f"DM da {sender_name}"
                    else:
                        preview = f"DM da {sender_name}"

                    self.seen_ids.add(thread_id)
                    events.append(RawEvent(
                        source=NotifSource.INSTAGRAM,
                        priority=NotifPriority.NORMAL,
                        text=preview,
                        external_id=thread_id,
                    ))
                    logger.info(f"📸 Instagram DM: {preview!r}")

            except Exception as e:
                logger.warning(f"Instagram poll DM error: {e}")

        # ── Menzioni ──────────────────────────────────────────────────
        if self._monitor_mentions:
            try:
                # news_inbox contiene like, commenti, menzioni ecc.
                inbox = self._client.news_inbox_v1()
                for item in (inbox.get("new", []) + inbox.get("old", []))[:30]:
                    pk = str(getattr(item, "pk", None) or id(item))
                    notif_type = str(getattr(item, "type", ""))
                    if "mention" not in notif_type.lower():
                        continue
                    if pk in self.seen_ids:
                        continue

                    user = getattr(item, "user", None)
                    username = getattr(user, "username", "?") if user else "?"
                    text = getattr(item, "text", None) or f"Menzione da @{username}"
                    preview = f"@{username} ti ha menzionato: {str(text)[:60]}"

                    self.seen_ids.add(pk)
                    events.append(RawEvent(
                        source=NotifSource.INSTAGRAM,
                        priority=NotifPriority.HIGH,
                        text=preview,
                        external_id=pk,
                    ))
                    logger.info(f"📸 Instagram menzione: {preview!r}")

            except Exception as e:
                logger.warning(f"Instagram poll mention error: {e}")

        return events

    def shutdown(self) -> None:
        if self._client is not None:
            try:
                self._client.dump_settings(self._session_file)
            except Exception:
                pass
        super().shutdown()
