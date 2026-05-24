"""
plugins/base.py
Interfaccia base che tutti i plugin estendono.
"""
import json
import logging
import os
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from typing import Optional

from notification_packet import NotifSource, NotifPriority


logger = logging.getLogger(__name__)

# Cap massimo di ID memorizzati per plugin (FIFO).
# 5000 è abbondante: con 50 nuovi ID/giorno → ~100 giorni di storia.
SEEN_IDS_MAX = 5000

# Cartella dove salvare gli storici per-plugin
HISTORY_DIR = "history"


@dataclass
class RawEvent:
    """Evento grezzo dal plugin, prima dell'analisi sentiment/urgency."""
    source: NotifSource
    priority: NotifPriority           # priority può essere già nota dal plugin (es. high per work email)
    text: str                          # titolo evento / prima frase del messaggio
    external_id: str                   # ID univoco per dedup (es. event ID di Calendar)


class Plugin(ABC):
    """
    Plugin base. Ogni plugin specializzato (Calendar, Discord, Gmail...) eredita
    da questa classe e implementa `poll()` e `name`.

    Lifecycle:
      - __init__(config_dict): chiamato all'avvio del plugin manager.
        Carica seen_ids da history/{name}.json se esiste.
      - poll() → list[RawEvent]: chiamato periodicamente, ritorna nuovi eventi.
      - persist_seen_ids(): chiamato dopo ogni poll per salvare lo storico.
      - shutdown(): chiamato a chiusura ordinata (opzionale override).

    Convenzione: il plugin tiene traccia di quali external_id ha già visto per
    evitare duplicati. Lo storico è persistito su disco (history/{name}.json)
    così sopravvive ai riavvi della companion.
    """

    def __init__(self, config: dict):
        self.config = config
        # seen_ids è sia un set (per lookup O(1)) sia una deque (per ordine FIFO)
        # Mantenuti in sincronia: aggiungere ID via _track_seen_id().
        self._seen_set: set[str] = set()
        self._seen_order: deque = deque(maxlen=SEEN_IDS_MAX)
        self._history_dirty = False  # diventa True quando ci sono modifiche da salvare
        self._load_seen_ids()
        logger.info(
            f"Plugin {self.name} inizializzato "
            f"({len(self._seen_set)} ID nello storico)."
        )

    @property
    def seen_ids(self):
        """
        Accesso compatibility-style: si comporta come un set per le operazioni
        comuni dei plugin esistenti.
        - `x in plugin.seen_ids`  → membership check
        - `plugin.seen_ids.add(x)` → aggiunge con tracking FIFO
        """
        return _SeenIdsProxy(self)

    def _track_seen_id(self, external_id: str) -> None:
        """Aggiunge un external_id allo storico FIFO (interno)."""
        if external_id in self._seen_set:
            return
        # Se siamo al cap, rimuovi il più vecchio
        if len(self._seen_order) >= SEEN_IDS_MAX:
            oldest = self._seen_order[0]  # peek (deque maxlen evict auto al .append)
            self._seen_set.discard(oldest)
        self._seen_order.append(external_id)
        self._seen_set.add(external_id)
        self._history_dirty = True

    def _history_path(self) -> str:
        """Path del file di storia per questo plugin."""
        return os.path.join(HISTORY_DIR, f"{self.name}.json")

    def _load_seen_ids(self) -> None:
        """Carica lo storico da disco se esiste."""
        path = self._history_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            ids = data.get("seen_ids", [])
            # Carica nell'ordine in cui erano salvati (FIFO)
            for sid in ids[-SEEN_IDS_MAX:]:  # safety cap
                if sid not in self._seen_set:
                    self._seen_order.append(sid)
                    self._seen_set.add(sid)
        except Exception as e:
            logger.warning(f"Errore lettura storico {path}: {e}")

    def persist_seen_ids(self) -> None:
        """
        Salva lo storico su disco. Chiamato dal plugin manager dopo ogni poll.
        Idempotente: se non ci sono modifiche dirty, non fa I/O.
        """
        if not self._history_dirty:
            return
        try:
            os.makedirs(HISTORY_DIR, exist_ok=True)
            path = self._history_path()
            # Write to temp file then rename for atomicity (evita corruzione su crash)
            tmp_path = path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump({"seen_ids": list(self._seen_order)}, f, indent=2)
            os.replace(tmp_path, path)
            self._history_dirty = False
        except Exception as e:
            logger.warning(f"Errore salvataggio storico {self._history_path()}: {e}")

    @property
    @abstractmethod
    def name(self) -> str:
        """Nome del plugin (es. 'calendar')."""
        ...

    @abstractmethod
    def poll(self) -> list[RawEvent]:
        """
        Polls la sorgente esterna e ritorna nuovi eventi grezzi.
        Implementazioni:
          - Devono filtrare già viste via self.seen_ids
          - Aggiungere new IDs a self.seen_ids prima del return
          - Catturare eccezioni transienti, loggarle e ritornare []
        """
        ...

    @property
    def poll_interval_sec(self) -> int:
        """Intervallo tra polling consecutive, in secondi."""
        return int(self.config.get("poll_interval_sec", 60))

    def shutdown(self) -> None:
        """Override opzionale per cleanup risorse. Salva lo storico residuo."""
        self.persist_seen_ids()


class _SeenIdsProxy:
    """
    Proxy che fa apparire `plugin.seen_ids` come un set tradizionale,
    ma in realtà delega add() al tracking FIFO del plugin per garantire
    rotation e dirty flag.
    """
    __slots__ = ("_plugin",)

    def __init__(self, plugin: Plugin):
        self._plugin = plugin

    def __contains__(self, item) -> bool:
        return item in self._plugin._seen_set

    def add(self, item: str) -> None:
        self._plugin._track_seen_id(item)

    def __len__(self) -> int:
        return len(self._plugin._seen_set)

    def __iter__(self):
        return iter(self._plugin._seen_set)
