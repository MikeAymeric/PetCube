"""
main.py
Entry point della PetCube Companion App.

Carica config, istanzia plugin manager + sender, avvia il loop principale.
Ctrl+C per uscire ordinatamente.
"""
import asyncio
import json
import logging
import signal
import sys
from pathlib import Path

from playwright_env import setup_playwright_browsers_path
setup_playwright_browsers_path()

from plugin_manager import PluginManager
from ble_sender import Sender
from notification_packet import NotifPacket


def setup_logging(config: dict) -> None:
    log_cfg = config.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_file = log_cfg.get("file")

    # Su Windows la console default è cp1252 e non sa renderizzare le emoji.
    # Forziamo stdout a UTF-8 quando possibile (Python 3.7+ supporta reconfigure).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, Exception):
        pass

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        # Force UTF-8 anche per il file
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


def load_config(path: Path = Path("config.json")) -> dict:
    if not path.exists():
        print(f"⚠️  config.json non trovato in {path.resolve()}", file=sys.stderr)
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


class App:
    """Wrapping della logica async per gestione signal e shutdown."""

    def __init__(self, config: dict):
        self.config = config
        self.sender: Sender | None = None
        self.plugin_manager: PluginManager | None = None
        self.pending_queue: asyncio.Queue[NotifPacket] = asyncio.Queue()
        self._stop = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    def on_notification(self, pkt: NotifPacket) -> None:
        """
        Callback chiamato dai plugin (thread non-async).
        Usa call_soon_threadsafe per accodare nella event loop async.
        """
        if self._loop:
            self._loop.call_soon_threadsafe(self.pending_queue.put_nowait, pkt)

    async def _sender_loop(self) -> None:
        """Consuma la coda e invia ciascun pacchetto."""
        while not self._stop.is_set():
            try:
                pkt = await asyncio.wait_for(self.pending_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                ok = await self.sender.send(pkt)
                if not ok:
                    logging.warning("Invio fallito, scarto il pacchetto.")
            except Exception as e:
                logging.exception(f"Errore invio: {e}")

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.sender = Sender(self.config)
        self.plugin_manager = PluginManager(self.config, self.on_notification)
        self.plugin_manager.start()

        logging.info("PetCube Companion avviato. Ctrl+C per uscire.")
        try:
            await self._sender_loop()
        finally:
            logging.info("Shutdown in corso...")
            self.plugin_manager.stop()
            await self.sender.close()
            logging.info("Arresto completato.")

    def request_stop(self) -> None:
        self._stop.set()


async def main_async() -> None:
    config = load_config()
    setup_logging(config)

    app = App(config)

    # Signal handlers (cross-platform)
    loop = asyncio.get_running_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, app.request_stop)
    except NotImplementedError:
        # Windows non supporta add_signal_handler — usa fallback
        signal.signal(signal.SIGINT, lambda s, f: app.request_stop())

    await app.run()


if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\nInterrotto.")
