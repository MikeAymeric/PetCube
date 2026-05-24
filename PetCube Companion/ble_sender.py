"""
ble_sender.py
Invia NotifPacket al cubo via BLE GATT (primario) o WiFi locale (fallback).

BLE: usa bleak (cross-platform: Windows/macOS/Linux).
Cerca un dispositivo che pubblicizza il service UUID configurato,
si connette, scrive il NotifPacket sulla characteristic.

WiFi fallback: POST HTTP al cubo (richiede firmware con server HTTP attivo,
non ancora implementato — sarà v0.10).
"""
import asyncio
import logging
import time

from notification_packet import NotifPacket, PACKET_SIZE


logger = logging.getLogger(__name__)


class BLESender:
    """
    Invia pacchetti via BLE.
    Mantiene la connessione aperta tra invii per ridurre overhead.
    Re-scan automatico se il device si disconnette.
    """

    def __init__(self, device_name: str, service_uuid: str, char_uuid: str,
                 scan_timeout_sec: int = 10):
        self.device_name = device_name
        self.service_uuid = service_uuid
        self.char_uuid = char_uuid
        self.scan_timeout = scan_timeout_sec
        self._client = None
        self._lock = asyncio.Lock()

    async def _ensure_connected(self) -> bool:
        """Garantisce che il client BLE sia connesso. Re-scan se necessario."""
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError:
            logger.error("bleak non installato. Eseguire: pip install bleak")
            return False

        if self._client and self._client.is_connected:
            return True

        logger.info(f"Scan BLE per '{self.device_name}' (timeout {self.scan_timeout}s)...")
        device = await BleakScanner.find_device_by_name(
            self.device_name, timeout=self.scan_timeout
        )
        if device is None:
            logger.warning(f"Device '{self.device_name}' non trovato.")
            return False

        logger.info(f"Trovato {device.address}. Connessione...")
        self._client = BleakClient(device.address)
        try:
            await self._client.connect()
            logger.info("Connesso al PetCube via BLE.")
            return True
        except Exception as e:
            logger.error(f"Connessione fallita: {e}")
            self._client = None
            return False

    async def send(self, packet: NotifPacket) -> bool:
        """Invia un pacchetto. Ritorna True se inviato con successo."""
        data = packet.to_bytes()
        assert len(data) == PACKET_SIZE

        async with self._lock:
            ok = await self._ensure_connected()
            if not ok:
                return False
            try:
                await self._client.write_gatt_char(self.char_uuid, data)
                logger.debug(f"BLE write OK ({len(data)} byte).")
                return True
            except Exception as e:
                logger.warning(f"BLE write fallita: {e}. Tento reconnect al prossimo invio.")
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
                self._client = None
                return False

    async def close(self) -> None:
        if self._client and self._client.is_connected:
            await self._client.disconnect()
            logger.info("BLE disconnesso.")


class WiFiFallbackSender:
    """
    Fallback HTTP. Il firmware deve esporre un endpoint /notify che accetta
    POST con body = 64 byte raw.
    (Non implementato nel firmware v0.13 — sarà v0.10 del firmware.)
    """

    def __init__(self, url: str):
        self.url = url

    def send_sync(self, packet: NotifPacket) -> bool:
        try:
            import requests
            response = requests.post(
                self.url,
                data=packet.to_bytes(),
                headers={"Content-Type": "application/octet-stream"},
                timeout=5,
            )
            if response.ok:
                logger.debug(f"WiFi send OK (status {response.status_code}).")
                return True
            logger.warning(f"WiFi response non-OK: {response.status_code}")
            return False
        except ImportError:
            logger.error("requests non installato. Eseguire: pip install requests")
            return False
        except Exception as e:
            logger.warning(f"WiFi send fallita: {e}")
            return False


class Sender:
    """
    Facade unificato: prova BLE, se fallisce passa a WiFi.
    Ha anche una modalità 'mock' per testing senza hardware reale.
    """

    def __init__(self, config: dict):
        device_cfg = config.get("device", {})
        transport_cfg = config.get("transport", {})
        self.prefer = transport_cfg.get("prefer", "ble")

        if self.prefer == "mock":
            self._ble = None
            self._wifi = None
            logger.info("Sender in modalità MOCK (nessun invio reale).")
        else:
            self._ble = BLESender(
                device_name=device_cfg.get("ble_name", "PetCube"),
                service_uuid=device_cfg.get("ble_service_uuid", ""),
                char_uuid=device_cfg.get("ble_char_uuid", ""),
                scan_timeout_sec=transport_cfg.get("ble_scan_timeout_sec", 10),
            )
            self._wifi = WiFiFallbackSender(
                url=device_cfg.get("wifi_fallback_url", "http://petcube.local:8080/notify")
            )

    async def send(self, packet: NotifPacket) -> bool:
        if self.prefer == "mock":
            logger.info(
                f"[MOCK SEND] source={packet.source.name} cat={packet.category.name} "
                f"priority={packet.priority.name} preview={packet.seed_preview!r}"
            )
            return True

        # 1. Prova BLE
        if self.prefer in ("ble", "auto"):
            ok = await self._ble.send(packet)
            if ok:
                return True
            logger.info("BLE fallito, provo WiFi fallback...")

        # 2. WiFi fallback (sync, lo wrapo)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._wifi.send_sync, packet)

    async def close(self) -> None:
        if self._ble:
            await self._ble.close()
