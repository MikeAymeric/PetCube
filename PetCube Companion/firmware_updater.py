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
import time
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

# Stati OTA letti dal device (caratteristica CTRL, OtaState lato firmware)
OTA_STATE_IDLE          = 0x00
OTA_STATE_RECEIVING     = 0x01
OTA_STATE_DONE          = 0x02
OTA_STATE_AWAIT_CONFIRM = 0x03
OTA_STATE_CANCELLED     = 0x04
OTA_STATE_ERROR         = 0xFF

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

# Una connessione BLE può chiudersi in qualsiasi momento con reason=0x213
# ("Remote User Terminated Connection", host-initiated). Il throughput reale
# misurato è ≈ 7 KB/s, quindi 1MB richiede ~155s, ma "write without response"
# su Windows ritorna non appena il dato è ACCODATO, non quando è trasmesso:
# la companion può accodare l'intero file in pochi secondi mentre il firmware
# lo riceve molto più lentamente. Per questo l'OTA è trasferita in più
# connessioni successive: ogni segmento accoda dati per al massimo
# SEGMENT_TIME_BUDGET secondi, poi la companion attende (DRAIN, fino a
# DRAIN_TIMEOUT secondi) che otaBytesReceived raggiunga quanto accodato,
# leggendolo periodicamente dalla CTRL. Se la connessione cade prima che il
# drain finisca, la companion riconnette e riprende: il firmware risponde a
# OTA START con otaBytesReceived, che coincide esattamente con i byte
# effettivamente scritti in flash (autocorregge eventuali dati persi).
SEGMENT_TIME_BUDGET = 60.0
DRAIN_TIMEOUT = 90.0


async def ota_update_ble(
    address: str,
    bin_path: Path,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    log_cb: Optional[Callable[[str], None]] = None,
) -> bool:
    """
    Trasferisce bin_path via BLE OTA al dispositivo e ne avvia il riavvio.
    Il trasferimento è segmentato su più connessioni BLE (vedi
    SEGMENT_TIME_BUDGET) e riprende automaticamente da dove il firmware è
    arrivato in caso di disconnessione.
    Ritorna True se il flashing è andato a buon fine.
    """
    def _log(msg: str) -> None:
        log.info("[OTA] %s", msg)
        if log_cb:
            log_cb(msg)

    data = bin_path.read_bytes()
    total = len(data)
    _log(f"File: {bin_path.name}  ({total:,} bytes)")

    def _make_disconnect_logger(connect_ts: list) -> Callable[[object], None]:
        def _on_disconnect(_client) -> None:
            _log(f"⚠ BLE disconnesso lato host dopo {time.monotonic() - connect_ts[0]:.1f}s")
        return _on_disconnect

    # ── Fase 1: trasferimento a segmenti, con ripresa automatica ──
    offset = 0
    while offset < total:
        connect_ts = [time.monotonic()]
        async with BleakClient(address, timeout=30.0, disconnected_callback=_make_disconnect_logger(connect_ts)) as client:
            connect_ts[0] = time.monotonic()

            # Negozia MTU più grande per throughput migliore
            try:
                await client.request_mtu(512)
            except Exception:
                pass
            # Il firmware accoda i chunk in un buffer OTA_CHUNK_MAX=512 byte:
            # un chunk più grande viene scartato silenziosamente (write senza
            # risposta), quindi va sempre limitato a 512 anche se l'MTU
            # negoziato permetterebbe payload leggermente più grandi (es. 514
            # con MTU 517).
            chunk_size = min(max(20, client.mtu_size - 3), 512)

            # ── START (o ripresa) ──
            start_cmd = bytes([OTA_CMD_START]) + total.to_bytes(4, "little")
            await client.write_gatt_char(BLE_CHAR_OTA_CTRL_UUID, start_cmd, response=True)

            ack = await client.read_gatt_char(BLE_CHAR_OTA_CTRL_UUID)
            if not ack or ack[0] != OTA_ACK_OK:
                _log("✗ Il dispositivo ha rifiutato l'avvio OTA (memoria insufficiente?).")
                return False

            # Il firmware riporta sempre otaBytesReceived: è il punto da cui
            # riprendere, sia al primo avvio (0) sia dopo una riconnessione.
            if len(ack) >= 5:
                offset = int.from_bytes(ack[1:5], "little")

            if offset == 0:
                _log(f"▶ OTA avviata, trasferimento in corso... (MTU {client.mtu_size}, chunk {chunk_size})")
            else:
                _log(f"▶ Ripresa OTA da {offset:,}/{total:,} byte (MTU {client.mtu_size}, chunk {chunk_size})")
            if progress_cb:
                progress_cb(offset, total)

            # ── TRANSFER chunks (limitato a SEGMENT_TIME_BUDGET secondi) ──
            segment_start = time.monotonic()
            while offset < total and (time.monotonic() - segment_start) < SEGMENT_TIME_BUDGET:
                chunk = data[offset: offset + chunk_size]
                try:
                    await client.write_gatt_char(BLE_CHAR_OTA_DATA_UUID, chunk, response=False)
                except Exception as e:
                    # Se Windows/bleak rifiuta o fallisce una write "without
                    # response" (es. coda BLE piena), interrompiamo il
                    # segmento: la prossima connessione riprenderà da dove il
                    # firmware è realmente arrivato.
                    _log(f"✗ Errore scrittura OTA dopo {offset:,}/{total:,} byte "
                         f"e {time.monotonic() - connect_ts[0]:.1f}s: {e!r}")
                    break
                offset += len(chunk)
                if progress_cb:
                    progress_cb(offset, total)
                # "write without response" su Windows viene solo accodato
                # dallo stack BLE, non trasmesso subito: questa pausa evita
                # di accumulare un backlog enorme che lo stack non riesce a
                # smaltire prima della disconnessione.
                await asyncio.sleep(0.015)

            # ── DRAIN ──
            # I chunk appena accodati non sono ancora arrivati al firmware:
            # leggiamo otaBytesReceived dalla CTRL finché non raggiunge
            # quanto accodato in questo segmento (o smette di progredire /
            # supera DRAIN_TIMEOUT), così sappiamo davvero da dove riprendere
            # se serve una nuova connessione.
            queued_offset = offset
            drain_start = time.monotonic()
            last_confirmed = -1
            while True:
                try:
                    ack = await asyncio.wait_for(
                        client.read_gatt_char(BLE_CHAR_OTA_CTRL_UUID), timeout=5.0
                    )
                except (asyncio.TimeoutError, Exception):
                    break
                confirmed = int.from_bytes(ack[1:5], "little") if len(ack) >= 5 else 0
                if progress_cb:
                    progress_cb(confirmed, total)
                if confirmed >= queued_offset or confirmed == last_confirmed:
                    offset = confirmed
                    break
                last_confirmed = confirmed
                if time.monotonic() - drain_start > DRAIN_TIMEOUT:
                    offset = confirmed
                    break
                await asyncio.sleep(2.0)
        # async with: la connessione si chiude qui (volontariamente o perché
        # l'host la termina). Se offset < total, il while esterno riconnette
        # e riprende da otaBytesReceived.

    _log(f"✓ Trasferiti {total:,} bytes. Commit in corso...")

    # ── Fase 2: commit + attesa conferma, con riconnessione se necessario ──
    committed = False
    while True:
        connect_ts = [time.monotonic()]
        try:
            async with BleakClient(address, timeout=30.0, disconnected_callback=_make_disconnect_logger(connect_ts)) as client:
                connect_ts[0] = time.monotonic()

                if not committed:
                    # ── COMMIT ──
                    # Update.end(true) sull'ESP32 verifica l'immagine (SHA256
                    # sull'intera partizione) prima di rispondere: può
                    # richiedere qualche secondo.
                    try:
                        await asyncio.wait_for(
                            client.write_gatt_char(BLE_CHAR_OTA_CTRL_UUID, bytes([OTA_CMD_COMMIT]), response=True),
                            timeout=15.0,
                        )
                        committed = True
                    except Exception as e:
                        _log(f"✗ Commit non confermato dal dispositivo ({e}), riconnetto e riprovo...")
                        continue

                    ack = await asyncio.wait_for(
                        client.read_gatt_char(BLE_CHAR_OTA_CTRL_UUID), timeout=10.0
                    )
                    if not ack or ack[0] != OTA_STATE_AWAIT_CONFIRM:
                        _log("✗ Commit rifiutato (verifica CRC fallita?).")
                        return False

                    # ── Attesa conferma sul dispositivo ──
                    # Il PetCube mostra "Aggiornare il firmware?" e attende B
                    # (installa) o C (annulla) prima di finalizzare l'OTA.
                    _log("⏳ In attesa di conferma sul PetCube (B = installa, C = annulla)...")

                # ── Polling stato (con ripresa se la connessione cade) ──
                while True:
                    await asyncio.sleep(1.0)
                    try:
                        st = await asyncio.wait_for(
                            client.read_gatt_char(BLE_CHAR_OTA_CTRL_UUID), timeout=5.0
                        )
                    except (asyncio.TimeoutError, Exception):
                        break  # riconnetti e continua il polling

                    if not st:
                        continue
                    state = st[0]
                    if state == OTA_STATE_AWAIT_CONFIRM:
                        continue
                    elif state == OTA_STATE_DONE:
                        _log("✓ Aggiornamento confermato — il dispositivo si riavvierà con il nuovo firmware.")
                        return True
                    elif state == OTA_STATE_CANCELLED:
                        _log("✗ Aggiornamento annullato dall'utente sul PetCube.")
                        return False
                    else:
                        _log("✗ Aggiornamento fallito sul dispositivo.")
                        return False
        except Exception as e:
            if committed:
                # Il dispositivo probabilmente si è riavviato col nuovo
                # firmware prima che riuscissimo a riconnetterci.
                _log(f"ℹ  Il dispositivo si è riavviato ({e!r}).")
                return True
            raise


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
