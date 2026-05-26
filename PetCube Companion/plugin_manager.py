"""
plugin_manager.py
Carica i plugin abilitati dal config, li polla in cicli scaglionati,
e produce NotifPacket pronti da inviare al cubo.
"""
import logging
import time
import threading
from typing import Callable

from plugins.base import Plugin, RawEvent
from notification_packet import (
    NotifPacket, NotifSource, NotifPriority,
    compute_seed_hash, truncate_seed
)
from sentiment import analyze


logger = logging.getLogger(__name__)


# Registry: nome plugin → classe Python.
# Aggiungere qui i plugin futuri.
PLUGIN_REGISTRY: dict[str, str] = {
    "calendar":  "plugins.calendar_plugin.CalendarPlugin",
    "gmail":     "plugins.gmail_plugin.GmailPlugin",
    "hacknplan": "plugins.hacknplan_plugin.HacknplanPlugin",
    "discord":   "plugins.discord_plugin.DiscordPlugin",
    "telegram":  "plugins.telegram_plugin.TelegramPlugin",
    "whatsapp":  "plugins.whatsapp_plugin.WhatsAppPlugin",
    "instagram": "plugins.instagram_plugin.InstagramPlugin",
    # "slack":    "plugins.slack_plugin.SlackPlugin",
    # "github":   "plugins.github_plugin.GithubPlugin",
}


def _load_plugin_class(class_path: str):
    """Importa dinamicamente una classe da 'modulo.sottomodulo.ClassName'."""
    module_name, class_name = class_path.rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


class PluginManager:
    """
    Gestisce il ciclo di vita dei plugin abilitati.

    Uso:
        pm = PluginManager(config, on_notification=callback)
        pm.start()
        ...
        pm.stop()
    """

    def __init__(self, config: dict, on_notification: Callable[[NotifPacket], None]):
        self.config = config
        self.on_notification = on_notification
        self.plugins: list[Plugin] = []
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []

        self._load_plugins()

    def _load_plugins(self) -> None:
        """Istanzia i plugin abilitati dal config."""
        plugins_config = self.config.get("plugins", {})
        for plugin_name, plugin_cfg in plugins_config.items():
            if not plugin_cfg.get("enabled", False):
                logger.debug(f"Plugin '{plugin_name}' disabilitato dal config.")
                continue
            if plugin_name not in PLUGIN_REGISTRY:
                logger.warning(f"Plugin '{plugin_name}' nel config ma non implementato.")
                continue
            try:
                cls = _load_plugin_class(PLUGIN_REGISTRY[plugin_name])
                instance = cls(plugin_cfg)
                self.plugins.append(instance)
                logger.info(f"Plugin '{plugin_name}' caricato.")
            except Exception as e:
                logger.error(f"Errore caricamento plugin '{plugin_name}': {e}")

    def _poll_loop(self, plugin: Plugin) -> None:
        """Loop di polling per un singolo plugin."""
        interval = plugin.poll_interval_sec
        logger.info(f"[{plugin.name}] poll loop avviato (intervallo {interval}s).")
        while not self._stop_event.is_set():
            try:
                events = plugin.poll()
                for raw in events:
                    self._dispatch(raw)
                # Persistenza storico: scrive su disco solo se ci sono modifiche
                plugin.persist_seen_ids()
            except Exception as e:
                logger.exception(f"[{plugin.name}] errore in poll: {e}")
            # Attesa scaglionata, interrompibile da stop_event
            if self._stop_event.wait(interval):
                break
        # Save finale a chiusura ordinata
        plugin.persist_seen_ids()
        logger.info(f"[{plugin.name}] poll loop terminato.")

    def _dispatch(self, raw: RawEvent) -> None:
        """Trasforma un RawEvent in NotifPacket e invia alla callback."""
        # Tronca seed alla prima frase o 50 char
        seed = truncate_seed(raw.text, max_len=50)
        sentiment, urgency, category = analyze(seed)

        # Se il plugin ha già impostato una priority HIGH, rispettala anche se
        # l'urgency analizzata dice 'low' (es. Calendar evento imminente <5 min)
        # Logica: max tra priority del plugin e priority derivata dall'urgency
        urgency_priority = NotifPriority.HIGH if urgency == "high" else NotifPriority.NORMAL
        final_priority = NotifPriority(max(int(raw.priority), int(urgency_priority)))

        pkt = NotifPacket(
            source=raw.source,
            priority=final_priority,
            category=category,
            seed_hash=compute_seed_hash(seed),
            seed_length=len(seed),
            timestamp=int(time.time()),
            seed_preview=seed,
        )
        logger.info(
            f"📦 NotifPacket pronto: source={raw.source.name} "
            f"priority={final_priority.name} category={category.name} "
            f"hash={pkt.seed_hash} preview={seed!r}"
        )
        self.on_notification(pkt)

    def start(self) -> None:
        """Avvia i poll loop in background."""
        for plugin in self.plugins:
            t = threading.Thread(
                target=self._poll_loop,
                args=(plugin,),
                name=f"poll-{plugin.name}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        """Ferma tutti i poll loop ordinatamente."""
        logger.info("Arresto plugin manager...")
        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=5)
        for plugin in self.plugins:
            try:
                plugin.shutdown()
            except Exception as e:
                logger.warning(f"shutdown {plugin.name}: {e}")
        logger.info("Plugin manager fermato.")
