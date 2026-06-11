"""
firmware_updater.py
Verifica versione firmware via BLE, scarica da GitHub Releases,
e flasha via BLE OTA (o via esptool su USB come fallback).
"""
import asyncio
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import requests
from bleak import BleakClient, BleakScanner

log = logging.getLogger(__name__)

BLE_DEVICE_NAME         = "PetCube"
BLE_SERVICE_UUID        = "12345678-1234-5678-1234-56789abcdef0"
BLE_CHAR_VERSION_UUID   = "12345678-1234-5678-1234-56789abcdef2"
BLE_CHAR_OTA_CTRL_UUID  = "12345678-1234-5678-1234-56789abcdef3"
BLE_CHAR_OTA_DATA_UUID  = "12345678-1234-5678-1234-56789abcdef4"

# Comandi OTA CTRL
OTA_CMD_START  = 0x01
OTA_CMD_COMMIT = 0x02
OTA_CMD_ABORT  = 0x03

# Risposta OK dal dispositivo
OTA_ACK_OK  = 0x01
OTA_ACK_ERR = 0x00

# Chunk size conservativo per BLE (MTU negoziata - 3 byte ATT header)
# Viene sovrascritto a runtime con il valore reale dopo la connessione.
DEFAULT_CHUNK_SIZE = 500


@dataclass
class FirmwareInfo:
    version: int
    bin_path: Optional[Path] = None
    download_url: Optional[str] = None
    tag_name: Optional[str] = None

    def label(self) -> str:
        name = self.bin_path.name if self.bin_path else (self.tag_name or f"v{self.version}")
        return f"v{self.version}  ({name})"


# ── BLE device discovery ──────────────────────────────────────

async def scan_for_petcube(timeout: float = 10.0) -> Optional[str]:
    """Ritorna l'indirizzo BLE del primo dispositivo 'PetCube' trovato."""
    log.info("Scansione BLE in corso...")
    devices = await BleakScanner.discover(timeout=timeout)
    for d in devices:
        if d.name and BLE_DEVICE_NAME.lower() in d.name.lower():
            log.info("Trovato: %s @ %s", d.name, d.address)
            return d.address
    return None


async def read_fw_version_ble(address: str, timeout: float = 10.0) -> Optional[int]:
    """Legge la versione firmware dalla caratteristica VERSION (uint16 LE)."""
    async with BleakClient(address, timeout=timeout) as client:
        try:
            data = await client.read_gatt_char(BLE_CHAR_VERSION_UUID)
            return int.from_bytes(data[:2], "little")
        except Exception as e:
            log.warning("Lettura versione BLE fallita: %s", e)
            return None


# ── GitHub Releases ──────────────────────────────────────────

def check_github_release(owner: str, repo: str) -> Optional[FirmwareInfo]:
    """
    Controlla la release più recente su GitHub.
    Ritorna FirmwareInfo con download_url se trova un asset .bin.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    try:
        r = requests.get(url, timeout=10, headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
    except requests.RequestException as e:
        log.warning("GitHub API non raggiungibile: %s", e)
        return None

    data = r.json()
    tag = data.get("tag_name", "")

    bin_assets = [a for a in data.get("assets", []) if a["name"].endswith(".bin")]
    if not bin_assets:
        return None

    # Per l'OTA via BLE serve solo l'immagine della partizione app
    # (bootloader/partition table non vengono aggiornati a runtime).
    asset = next((a for a in bin_assets if "_app" in a["name"].lower()), bin_assets[0])

    # Estrai numero versione dal nome dell'asset (es. "PetCube_FW_v15_app.bin" → 15)
    m = re.search(r"v(\d+)", asset["name"], re.IGNORECASE)
    ver = int(m.group(1)) if m else 0

    return FirmwareInfo(
        version=ver,
        download_url=asset["browser_download_url"],
        tag_name=tag,
    )


def download_firmware(
    url: str,
    dest: Path,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> Path:
    """Scarica il .bin da url e lo salva in dest."""
    log.info("Download firmware: %s", url)
    dest.parent.mkdir(parents=True, exist_ok=True)

    r = requests.get(url, stream=True, timeout=60)
    r.raise_for_status()
    total = int(r.headers.get("content-length", 0))
    downloaded = 0

    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)
            downloaded += len(chunk)
            if progress_cb:
                progress_cb(downloaded, total)

    log.info("Download completato: %s (%d bytes)", dest.name, downloaded)
    return dest


# ── BLE OTA ──────────────────────────────────────────────────

async def ota_update_ble(
    address: str,
    bin_path: Path,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    log_cb: Optional[Callable[[str], None]] = None,
) -> bool:
    """
    Trasferisce bin_path via BLE OTA al dispositivo e ne avvia il riavvio.
    Ritorna True se il flashing è andato a buon fine.
    """
    def _log(msg: str) -> None:
        log.info("[OTA] %s", msg)
        if log_cb:
            log_cb(msg)

    data = bin_path.read_bytes()
    total = len(data)
    _log(f"File: {bin_path.name}  ({total:,} bytes)")

    async with BleakClient(address, timeout=30.0) as client:
        # Negozia MTU più grande per throughput migliore
        try:
            await client.request_mtu(512)
        except Exception:
            pass
        chunk_size = max(20, client.mtu_size - 3)
        _log(f"MTU: {client.mtu_size}  →  chunk: {chunk_size} bytes")

        # ── START ──
        start_cmd = bytes([OTA_CMD_START]) + total.to_bytes(4, "little")
        await client.write_gatt_char(BLE_CHAR_OTA_CTRL_UUID, start_cmd, response=True)

        ack = await client.read_gatt_char(BLE_CHAR_OTA_CTRL_UUID)
        if not ack or ack[0] != OTA_ACK_OK:
            _log("✗ Il dispositivo ha rifiutato l'avvio OTA (memoria insufficiente?).")
            return False
        _log("▶ OTA avviata, trasferimento in corso...")

        # ── TRANSFER chunks ──
        offset = 0
        while offset < total:
            chunk = data[offset: offset + chunk_size]
            await client.write_gatt_char(BLE_CHAR_OTA_DATA_UUID, chunk, response=False)
            offset += len(chunk)
            if progress_cb:
                progress_cb(offset, total)
            # "write without response" su Windows viene solo accodato dallo
            # stack BLE, non trasmesso subito. Senza una pausa qui il loop
            # "finisce" molto prima che i dati siano davvero arrivati al cubo,
            # e il successivo COMMIT (write con risposta) resta bloccato dietro
            # un enorme backlog di pacchetti non ancora inviati, causando un
            # timeout silenzioso prima che l'ESP32 riceva il comando di commit.
            await asyncio.sleep(0.015)

        _log(f"✓ Trasferiti {total:,} bytes. Commit in corso...")

        # ── COMMIT ──
        # Update.end(true) sull'ESP32 verifica l'immagine (SHA256 sull'intera
        # partizione) prima di rispondere: può richiedere qualche secondo.
        try:
            await asyncio.wait_for(
                client.write_gatt_char(BLE_CHAR_OTA_CTRL_UUID, bytes([OTA_CMD_COMMIT]), response=True),
                timeout=15.0,
            )
        except Exception as e:
            _log(f"✗ Commit non confermato dal dispositivo ({e}). OTA non completata.")
            return False

        try:
            ack = await asyncio.wait_for(
                client.read_gatt_char(BLE_CHAR_OTA_CTRL_UUID), timeout=10.0
            )
            if ack and ack[0] == OTA_ACK_OK:
                _log("✓ Commit OK — il dispositivo si riavvierà con il nuovo firmware.")
                return True
            else:
                _log("✗ Commit rifiutato (verifica CRC fallita?).")
                return False
        except (asyncio.TimeoutError, Exception):
            # Il dispositivo potrebbe essersi già riavviato prima che leggessimo la risposta
            _log("ℹ  Il dispositivo si è riavviato (timeout atteso dopo commit).")
            return True


# ── USB fallback (esptool) ────────────────────────────────────

def find_local_firmware(firmware_dir: Path) -> Optional[FirmwareInfo]:
    """Cerca il .bin con versione più alta in firmware_dir."""
    best: Optional[FirmwareInfo] = None
    for b in firmware_dir.glob("*.bin"):
        m = re.search(r"v(\d+)", b.stem, re.IGNORECASE)
        if m:
            ver = int(m.group(1))
            if best is None or ver > best.version:
                best = FirmwareInfo(version=ver, bin_path=b)
    return best


def list_serial_ports() -> list[str]:
    """Elenca le porte seriali disponibili (richiede pyserial)."""
    try:
        import serial.tools.list_ports
        return [p.device for p in serial.tools.list_ports.comports()]
    except ImportError:
        return []


def flash_firmware_usb(
    bin_path: Path,
    port: str,
    baud: int = 921600,
    log_cb: Optional[Callable[[str], None]] = None,
) -> bool:
    """Flasha bin_path via esptool su porta USB seriale."""
    try:
        import esptool  # noqa: F401
        esptool_cmd = [sys.executable, "-m", "esptool"]
    except ImportError:
        esptool_cmd = ["esptool.py"]

    cmd = [
        *esptool_cmd,
        "--chip", "auto",
        "--port", port,
        "--baud", str(baud),
        "write_flash", "-z", "0x0",
        str(bin_path),
    ]

    def _emit(msg: str) -> None:
        log.info("[esptool] %s", msg)
        if log_cb:
            log_cb(msg)

    _emit("Comando: " + " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        for line in proc.stdout:
            _emit(line.rstrip())
        proc.wait()
        ok = proc.returncode == 0
        _emit("✓ Completato." if ok else f"✗ esptool codice {proc.returncode}.")
        return ok
    except FileNotFoundError:
        _emit("ERRORE: esptool non trovato. Installa con: pip install esptool")
        return False
