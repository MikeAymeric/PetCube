"""
plugins/discord_plugin.py
Plugin Discord: notifica menzioni, DM e canali configurati.

Setup richiesto:
  1. Discord Developer Portal (discord.com/developers) → Applications → New Application
  2. Bot → "Add Bot" → Token → "Reset Token" → copiare in config.json: discord.bot_token
  3. Bot → Privileged Gateway Intents → attivare "Message Content Intent"
  4. OAuth2 → URL Generator → scopes: [bot] → permissions: [Read Messages/View Channels]
  5. Aprire il link OAuth2 generato e invitare il bot ai server desiderati

Triggers configurabili (config.json):
  - @menzioni del tuo account personale (user_id) in qualsiasi server
  - @here e @everyone in canali visibili al bot
  - Messaggi in channel ID specifici (monitor_channel_ids)
"""
import asyncio
import logging
import queue
import threading
from typing import Optional

from plugins.base import Plugin, RawEvent
from notification_packet import NotifSource, NotifPriority


logger = logging.getLogger(__name__)


class DiscordPlugin(Plugin):
    """
    Plugin Discord basato su discord.py 2.x.

    Il client Discord gira in un thread daemon dedicato con il suo event loop asyncio.
    Gli eventi rilevanti vengono accodati in una queue thread-safe; poll() svuota
    la coda nel thread di polling del plugin manager.

    Thread safety: il bot thread legge _seen_set (read-only, GIL-safe in CPython).
    Il thread di poll è l'unico writer di seen_ids — nessun lock aggiuntivo serve.
    """

    @property
    def name(self) -> str:
        return "discord"

    def __init__(self, config: dict):
        super().__init__(config)
        self._token: str = config.get("bot_token", "")
        self._user_id: Optional[int] = (
            int(config["user_id"]) if config.get("user_id") else None
        )
        self._monitor_channel_ids: set[int] = {
            int(x) for x in config.get("monitor_channel_ids", [])
        }
        # Queue thread-safe: bot thread produce, poll thread consuma
        self._event_queue: queue.Queue[RawEvent] = queue.Queue()
        self._bot_loop: Optional[asyncio.AbstractEventLoop] = None
        # discord.Client — non importato a livello modulo per evitare ImportError
        # se discord.py non è installato e il plugin è disabilitato
        self._client = None
        self._bot_thread: Optional[threading.Thread] = None

        if not self._token:
            logger.error(
                "Discord: 'bot_token' mancante in config.json. "
                "Consulta il docstring del plugin per il setup."
            )
            return
        if not self._user_id:
            logger.warning(
                "Discord: 'user_id' non configurato — le @menzioni personali "
                "non verranno rilevate. Aggiungilo in config.json."
            )

        self._start_bot()

    # ------------------------------------------------------------------
    # Thread management
    # ------------------------------------------------------------------

    def _start_bot(self) -> None:
        """Avvia il client Discord in un thread daemon dedicato."""
        self._bot_thread = threading.Thread(
            target=self._run_bot_loop,
            name="discord-bot",
            daemon=True,
        )
        self._bot_thread.start()

    def _run_bot_loop(self) -> None:
        """Thread entry: esegue asyncio.run() per il client Discord."""
        try:
            import discord
        except ImportError:
            logger.error(
                "Libreria discord.py non installata. "
                "Eseguire: pip install discord.py"
            )
            return

        intents = discord.Intents.default()
        intents.message_content = True  # Privileged intent — abilitare nel Dev Portal

        async def runner() -> None:
            async with discord.Client(intents=intents) as client:
                self._client = client
                self._bot_loop = asyncio.get_running_loop()

                @client.event
                async def on_ready() -> None:
                    logger.info(
                        f"💬 Discord bot connesso: {client.user} "
                        f"(id={client.user.id})"
                    )

                @client.event
                async def on_disconnect() -> None:
                    logger.warning("Discord bot disconnesso, riconnessione in corso...")

                @client.event
                async def on_message(message: "discord.Message") -> None:
                    if message.author == client.user:
                        return
                    msg_id = str(message.id)
                    # Pre-check: lettura GIL-safe dal thread asyncio
                    if msg_id in self._seen_set:
                        return
                    raw = self._classify_message(message, client.user, msg_id)
                    if raw is not None:
                        self._event_queue.put(raw)

                try:
                    await client.start(self._token)
                except discord.LoginFailure:
                    logger.error(
                        "Discord: token non valido. "
                        "Controlla 'bot_token' in config.json."
                    )

        try:
            asyncio.run(runner())
        except Exception as e:
            logger.error(f"Discord bot errore fatale: {e}")

    # ------------------------------------------------------------------
    # Event classification
    # ------------------------------------------------------------------

    def _classify_message(
        self,
        message: "discord.Message",
        bot_user: "discord.ClientUser",
        msg_id: str,
    ) -> Optional[RawEvent]:
        """
        Classifica un messaggio Discord.
        Ritorna RawEvent se rientra in un trigger, None altrimenti.
        """
        try:
            import discord
        except ImportError:
            return None

        author = message.author.display_name
        content = (message.content or "").strip()[:80]

        # @Menzione personale dell'utente
        if self._user_id and any(m.id == self._user_id for m in message.mentions):
            text = (
                f"{author} ti ha menzionato: {content}"
                if content
                else f"Menzione da {author}"
            )
            logger.debug(f"💬 Discord menzione da {author!r}")
            return RawEvent(
                source=NotifSource.DISCORD,
                priority=NotifPriority.HIGH,
                text=text,
                external_id=msg_id,
            )

        # @here o @everyone
        if message.mention_everyone:
            channel_name = getattr(message.channel, "name", "canale")
            text = (
                f"#{channel_name} — {author}: {content}"
                if content
                else f"@here/@everyone in #{channel_name} da {author}"
            )
            logger.debug(f"💬 Discord @everyone/@here in #{channel_name}")
            return RawEvent(
                source=NotifSource.DISCORD,
                priority=NotifPriority.NORMAL,
                text=text,
                external_id=msg_id,
            )

        # Canale monitorato esplicitamente in config
        if message.channel.id in self._monitor_channel_ids:
            channel_name = getattr(message.channel, "name", "canale")
            text = (
                f"#{channel_name} — {author}: {content}"
                if content
                else f"Messaggio in #{channel_name} da {author}"
            )
            logger.debug(f"💬 Discord canale #{channel_name} da {author!r}")
            return RawEvent(
                source=NotifSource.DISCORD,
                priority=NotifPriority.NORMAL,
                text=text,
                external_id=msg_id,
            )

        return None

    # ------------------------------------------------------------------
    # Plugin interface
    # ------------------------------------------------------------------

    def poll(self) -> list[RawEvent]:
        """
        Svuota la coda eventi Discord accumulati dal bot thread.
        Dedup definitivo qui: unico thread che scrive su seen_ids.
        """
        events: list[RawEvent] = []
        try:
            while True:
                raw: RawEvent = self._event_queue.get_nowait()
                if raw.external_id not in self.seen_ids:
                    self.seen_ids.add(raw.external_id)
                    events.append(raw)
                    logger.info(
                        f"💬 Discord {raw.priority.name}: {raw.text!r}"
                    )
        except queue.Empty:
            pass
        return events

    def shutdown(self) -> None:
        """Chiude ordinatamente il client Discord e salva lo storico."""
        if self._client is not None and self._bot_loop is not None:
            if not self._bot_loop.is_closed():
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        self._client.close(), self._bot_loop
                    )
                    future.result(timeout=5)
                    logger.info("Discord bot chiuso.")
                except Exception as e:
                    logger.warning(f"Discord shutdown: {e}")
        super().shutdown()
