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


def _patch_instagrapi_loose_urls() -> None:
    """
    Instagram restituisce a volte URL con schema 'instagram://' (deep-link)
    nel campo video_url di MediaXma (reel/storie condivise nei DM).
    Pydantic v2 rifiuta URL non-http/https con url_scheme ValidationError.

    Soluzione: ricompila i modelli instagrapi interessati sostituendo
    i campi URL-validated con Optional[str], prima di creare il Client.
    """
    _models_to_patch = {
        "MediaXma": ["video_url", "image_url"],
    }
    try:
        import instagrapi.types as _ig
        from pydantic.fields import FieldInfo
        from typing import Optional

        for model_name, fields in _models_to_patch.items():
            model = getattr(_ig, model_name, None)
            if model is None:
                continue
            model_fields = getattr(model, "model_fields", {})
            patched = []
            for field_name in fields:
                if field_name in model_fields:
                    model_fields[field_name] = FieldInfo(
                        default=None, annotation=Optional[str]
                    )
                    model.__annotations__[field_name] = Optional[str]
                    patched.append(field_name)
            if patched:
                model.model_rebuild(force=True)
                logger.debug(
                    f"Instagram: {model_name}.{{{', '.join(patched)}}} "
                    "patched → Optional[str] (accetta qualsiasi URL scheme)"
                )
    except Exception as e:
        logger.debug(f"Instagram: model patch saltato ({e})")


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

        # Patch modelli Pydantic prima di creare il client
        _patch_instagrapi_loose_urls()

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

        # ── DM ────────────────────────────────────────────────────────
        # direct_threads() usa visual_message_return_type=unseen, che è l'unico
        # modo per ricevere last_permanent_item popolato dalla API Instagram.
        # La deduplicazione avviene tramite seen_ids con chiave thread_id+item_id,
        # così lo stesso messaggio non notifica due volte ma i messaggi futuri
        # della stessa chat notificano correttamente.
        if self._monitor_dms:
            try:
                threads = self._client.direct_threads(amount=20)
            except Exception as e:
                if "url_scheme" in str(e) or "ValidationError" in type(e).__name__:
                    logger.warning(
                        f"Instagram: ValidationError URL scheme in direct_threads "
                        f"({type(e).__name__}) — verifica il patch Pydantic."
                    )
                else:
                    logger.warning(f"Instagram poll DM error: {e}")
                threads = []

            my_id = str(getattr(self._client, "user_id", "") or "")

            for thread in threads:
                try:
                    thread_id = str(thread.id)
                    last_item = getattr(thread, "last_permanent_item", None)
                    if last_item is None:
                        continue

                    msg_id = str(getattr(last_item, "item_id", "") or "")
                    if not msg_id:
                        continue

                    # Salta messaggi inviati da noi stessi
                    sender_id = str(getattr(last_item, "user_id", "") or "")
                    if my_id and sender_id == my_id:
                        continue

                    # Deduplicazione: thread + messaggio specifico
                    event_id = f"ig_{thread_id}_{msg_id}"
                    if event_id in self.seen_ids:
                        continue
                    self.seen_ids.add(event_id)

                    # Identifica mittente e costruisci preview
                    users = getattr(thread, "users", [])
                    sender_name = users[0].username if users else "?"

                    raw_text = getattr(last_item, "text", None) or ""
                    item_type = str(getattr(last_item, "item_type", "") or "")
                    _type_labels = {
                        "media_share":    "🖼 ha condiviso un media",
                        "reel_share":     "🎬 ha condiviso un reel",
                        "story_share":    "📖 ha condiviso una storia",
                        "like":           "❤ ha inviato un like",
                        "voice_media":    "🎤 ha inviato un vocale",
                        "animated_media": "✨ ha inviato una GIF",
                    }
                    if raw_text:
                        preview = f"{sender_name}: {raw_text[:60]}"
                    elif item_type in _type_labels:
                        preview = f"{sender_name} {_type_labels[item_type]}"
                    else:
                        preview = f"DM da {sender_name}"

                    events.append(RawEvent(
                        source=NotifSource.INSTAGRAM,
                        priority=NotifPriority.NORMAL,
                        text=preview,
                        external_id=event_id,
                    ))
                    logger.info(f"📸 Instagram DM: {preview!r}")

                except Exception as e:
                    logger.debug(
                        f"Instagram: errore parsing thread "
                        f"{getattr(thread, 'id', '?')}: {e}"
                    )

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
