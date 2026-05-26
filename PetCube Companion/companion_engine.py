"""
companion_engine.py
Motore della PetCube Companion. Espone un'API pulita per essere controllato
da una GUI esterna (o da main.py CLI):

  engine = CompanionEngine(config)
  engine.add_event_listener(callback)   # ricevi NotifPacket dispatched
  engine.add_log_listener(callback)     # ricevi log records live
  engine.start()                         # avvia plugin manager + sender loop in background
  status = engine.get_status()           # dict con stato corrente
  engine.stop()                          # arresto ordinato

Tutto thread-safe: l'engine ha la sua event loop asyncio in un thread dedicato,
le listener vengono chiamate dal thread dell'engine — la GUI deve marshallarle
nel suo main thread (CustomTkinter usa root.after() per questo).
"""
import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from plugin_manager import PluginManager
from ble_sender import Sender
from notification_packet import NotifPacket


logger = logging.getLogger(__name__)


# Tipi callback
EventListener = Callable[[NotifPacket, bool], None]   # (pkt, send_ok)
LogListener   = Callable[[logging.LogRecord], None]    # log record


@dataclass
class EngineStatus:
    """Snapshot dello stato corrente del motore (per visualizzazione GUI)."""
    running: bool = False
    plugins_active: list[str] = field(default_factory=list)
    plugins_loaded: list[str] = field(default_factory=list)
    sender_mode: str = "?"           # "ble" / "mock" / "wifi"
    notifications_sent: int = 0
    notifications_failed: int = 0
    last_notification_at: Optional[float] = None  # epoch
    started_at: Optional[float] = None


class _LogBroadcaster(logging.Handler):
    """Handler logging che ridistribuisce i log record alle listener."""

    def __init__(self):
        super().__init__()
        self._listeners: list[LogListener] = []
        self._lock = threading.Lock()

    def add_listener(self, cb: LogListener) -> None:
        with self._lock:
            self._listeners.append(cb)

    def remove_listener(self, cb: LogListener) -> None:
        with self._lock:
            try:
                self._listeners.remove(cb)
            except ValueError:
                pass

    def emit(self, record: logging.LogRecord) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(record)
            except Exception:
                pass


class CompanionEngine:
    """
    Motore della companion app, controllabile da GUI o CLI.

    Lifecycle:
      engine = CompanionEngine(config)
      engine.start()      # avvia in background
      ...
      engine.stop()       # arresto ordinato

    Una volta fermato, può essere riavviato (start() di nuovo).
    """

    def __init__(self, config: dict):
        self.config = config
        self._event_listeners: list[EventListener] = []
        self._log_broadcaster = _LogBroadcaster()
        self._log_broadcaster.setLevel(logging.INFO)
        self._log_broadcaster.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s",
                              datefmt="%H:%M:%S")
        )
        # Attacca alla root logger
        logging.getLogger().addHandler(self._log_broadcaster)

        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._sender: Optional[Sender] = None
        self._plugin_manager: Optional[PluginManager] = None
        self._pending_queue: Optional[asyncio.Queue] = None
        self._status = EngineStatus()
        self._status_lock = threading.Lock()

    # ── PUBLIC API ─────────────────────────────────────────────────

    def add_event_listener(self, cb: EventListener) -> None:
        """Registra callback per notifiche inviate. Chiamato dal thread engine."""
        self._event_listeners.append(cb)

    def remove_event_listener(self, cb: EventListener) -> None:
        try:
            self._event_listeners.remove(cb)
        except ValueError:
            pass

    def add_log_listener(self, cb: LogListener) -> None:
        """Registra callback per log records live."""
        self._log_broadcaster.add_listener(cb)

    def remove_log_listener(self, cb: LogListener) -> None:
        self._log_broadcaster.remove_listener(cb)

    def start(self) -> None:
        """Avvia il motore in un thread separato."""
        if self._thread and self._thread.is_alive():
            logger.warning("Engine già in esecuzione.")
            return
        self._thread = threading.Thread(target=self._run_thread, daemon=True, name="engine")
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Richiede arresto e aspetta fino a timeout secondi."""
        if not self._thread or not self._thread.is_alive():
            return
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            logger.warning("Engine non si è fermato entro il timeout.")

    def get_status(self) -> EngineStatus:
        """Snapshot dello stato corrente (thread-safe)."""
        with self._status_lock:
            return EngineStatus(
                running=self._status.running,
                plugins_active=list(self._status.plugins_active),
                plugins_loaded=list(self._status.plugins_loaded),
                sender_mode=self._status.sender_mode,
                notifications_sent=self._status.notifications_sent,
                notifications_failed=self._status.notifications_failed,
                last_notification_at=self._status.last_notification_at,
                started_at=self._status.started_at,
            )

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def inject_notification(self, pkt: NotifPacket) -> None:
        """Inietta una notifica fake direttamente nella coda (per test dalla GUI)."""
        if not self.is_running():
            logger.warning("inject_notification: engine non in esecuzione, notifica ignorata.")
            return
        self._on_notification(pkt)

    # ── INTERNAL ───────────────────────────────────────────────────

    def _run_thread(self) -> None:
        """Entry point del thread engine: crea event loop e gira."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_async())
        except Exception:
            logger.exception("Errore fatale nel motore.")
        finally:
            self._loop.close()
            self._loop = None

    async def _run_async(self) -> None:
        """Logica principale del motore (in event loop dedicato)."""
        self._stop_event = asyncio.Event()
        self._pending_queue = asyncio.Queue()
        self._sender = Sender(self.config)
        self._plugin_manager = PluginManager(self.config, self._on_notification)

        # Snapshot iniziale dello status
        plugins_cfg = self.config.get("plugins", {})
        loaded = [name for name, cfg in plugins_cfg.items()
                  if isinstance(cfg, dict) and cfg.get("enabled")]

        with self._status_lock:
            self._status.running = True
            self._status.plugins_loaded = loaded
            # active = quelli effettivamente avviati (lo aggiorniamo dopo start)
            self._status.sender_mode = getattr(self._sender, "prefer", "?")
            self._status.started_at = time.time()

        self._plugin_manager.start()

        # Active plugins = quelli che il manager è riuscito a istanziare
        with self._status_lock:
            self._status.plugins_active = [p.name for p in self._plugin_manager.plugins]

        logger.info("Companion engine avviato.")

        try:
            await self._sender_loop()
        finally:
            logger.info("Shutdown engine in corso...")
            self._plugin_manager.stop()
            await self._sender.close()
            with self._status_lock:
                self._status.running = False
            logger.info("Engine fermato.")

    async def _sender_loop(self) -> None:
        """Consuma la coda e invia ciascun pacchetto."""
        while not self._stop_event.is_set():
            try:
                pkt = await asyncio.wait_for(self._pending_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            send_ok = False
            try:
                send_ok = await self._sender.send(pkt)
            except Exception as e:
                logger.exception(f"Errore invio: {e}")

            # Aggiorna status
            with self._status_lock:
                if send_ok:
                    self._status.notifications_sent += 1
                else:
                    self._status.notifications_failed += 1
                self._status.last_notification_at = time.time()

            # Notifica event listeners
            for cb in list(self._event_listeners):
                try:
                    cb(pkt, send_ok)
                except Exception:
                    logger.exception("Errore in event listener")

    def _on_notification(self, pkt: NotifPacket) -> None:
        """Callback dai plugin (thread non-async)."""
        # Copia atomica del riferimento al loop prima del check: evita la race
        # condition TOCTOU in cui il thread engine azzera _loop tra il guard e
        # la call_soon_threadsafe.
        loop = self._loop
        queue = self._pending_queue
        if loop is not None and queue is not None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, pkt)
            except RuntimeError:
                # Loop già chiuso (shutdown in corso): notifica ignorata.
                logger.debug("_on_notification: loop chiuso, notifica scartata.")


def load_config(path: Path = Path("config.json")) -> dict:
    """Carica config.json dal path indicato."""
    if not path.exists():
        raise FileNotFoundError(f"config.json non trovato in {path.resolve()}")
    return json.loads(path.read_text(encoding="utf-8"))
