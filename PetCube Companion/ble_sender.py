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
from typing import Callable, Optional

from notification_packet import NotifPacket, PACKET_SIZE
from config_schema import device_tag


logger = logging.getLogger(__name__)

# Characteristic IDENTITY del firmware (vedi PetCube.ino BLE_CHAR_IDENTITY_UUID):
# riceve il tag multiplayer "username#12345" assegnato dalla Companion App.
DEFAULT_IDENTITY_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef5"

# Characteristic ACHV del firmware (vedi PetCube.ino BLE_CHAR_ACHV_UUID):
# bitmask uint64 little-endian di sblocco achievement, read-only.
DEFAULT_ACHV_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef7"


class BLESender:
    """
    Invia pacchetti via BLE.
    Mantiene la connessione aperta tra invii per ridurre overhead.
    Re-scan automatico se il device si disconnette.
    """

    def __init__(self, device_name: str, service_uuid: str, char_uuid: str,
                 scan_timeout_sec: int = 10,
                 identity_char_uuid: str = DEFAULT_IDENTITY_CHAR_UUID,
                 achv_char_uuid: str = DEFAULT_ACHV_CHAR_UUID,
                 on_achievements: Optional[Callable[[int], None]] = None):
        self.device_name = device_name
        self.service_uuid = service_uuid
        self.char_uuid = char_uuid
        self.identity_char_uuid = identity_char_uuid
        self.achv_char_uuid = achv_char_uuid
        self.on_achievements = on_achievements
        self.scan_timeout = scan_timeout_sec
        self._client = None
        self._lock = asyncio.Lock()
        self._identity_tag: str = ""
        self._identity_sent = False

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
            self._identity_sent = False
            await self._send_identity_if_needed()
            await self._read_achievements_if_needed()
            return True
        except Exception as e:
            logger.error(f"Connessione fallita: {e}")
            self._client = None
            return False

    def set_identity_tag(self, tag: str) -> None:
        """Imposta il tag multiplayer ('username#12345') da inviare al cubo."""
        if tag != self._identity_tag:
            self._identity_tag = tag
            self._identity_sent = False

    async def _send_identity_if_needed(self) -> None:
        """Scrive il tag identità sulla characteristic dedicata (best-effort)."""
        if not self._identity_tag or self._identity_sent:
            return
        if not (self._client and self._client.is_connected):
            return
        try:
            await self._client.write_gatt_char(
                self.identity_char_uuid, self._identity_tag.encode("utf-8")
            )
            self._identity_sent = True
            logger.info(f"📡 Tag identità inviato al cubo: {self._identity_tag!r}")
        except Exception as e:
            logger.warning(f"Invio tag identità fallito: {e}")

    async def _read_achievements_if_needed(self) -> None:
        """Legge la bitmask achievement e notifica la callback (best-effort)."""
        if not self.on_achievements:
            return
        if not (self._client and self._client.is_connected):
            return
        try:
            data = await self._client.read_gatt_char(self.achv_char_uuid)
            mask = int.from_bytes(data[:8], "little")
            self.on_achievements(mask)
            logger.info(f"🏆 Achievement bitmask letta via BLE: {mask:#x}")
        except Exception as e:
            logger.debug(f"Lettura achievement BLE fallita (FW < v25?): {e}")

    async def send(self, packet: NotifPacket) -> bool:
        """Invia un pacchetto. Ritorna True se inviato con successo."""
        data = packet.to_bytes()
        if len(data) != PACKET_SIZE:
            raise ValueError(
                f"BLESender.send: dimensione pacchetto errata "
                f"({len(data)} byte, attesi {PACKET_SIZE})"
            )

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

    def __init__(self, config: dict, on_achievements: Optional[Callable[[int], None]] = None):
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
                on_achievements=on_achievements,
            )
            # Invia il tag solo se un device_id è già stato assegnato (wizard/impostazioni):
            # evita di propagare al cubo un ID generato al volo e non persistito.
            if device_cfg.get("device_id"):
                self._ble.set_identity_tag(
                    device_tag(device_cfg.get("username", ""), device_cfg["device_id"])
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

        # 2. WiFi fallback (sync, lo wrapo in executor)
        # get_running_loop() è il modo corretto dentro una coroutine (Python 3.7+).
        # get_event_loop() è deprecato in contesto async da Python 3.10.
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._wifi.send_sync, packet)

    async def close(self) -> None:
        if self._ble:
            await self._ble.close()
