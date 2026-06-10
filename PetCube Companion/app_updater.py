"""
app_updater.py
Self-update della Companion App da GitHub Releases.

Convenzione release: tag "companion-vX.Y.Z" con un asset allegato:
  - PetCubeCompanion.exe   → usato quando l'app gira come eseguibile (PyInstaller)
  - *.zip (codice sorgente) → usato quando l'app gira da sorgente Python

L'asset .zip deve contenere i file della cartella "PetCube Companion"
alla radice (o in un'unica sottocartella di primo livello, che viene
automaticamente "appiattita").
"""
import logging
import os
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import requests

from version import APP_VERSION

log = logging.getLogger(__name__)

# File/cartelle da NON sovrascrivere mai durante un aggiornamento sorgente
_PRESERVE_NAMES = {"config.json", "firmware", "__pycache__", ".git"}
_PRESERVE_FILES = {"token.json", "credentials.json"}
_PRESERVE_SUFFIXES = (".session", ".session-journal")


@dataclass
class AppReleaseInfo:
    version: str           # es. "1.2.0"
    version_tuple: tuple
    tag_name: str
    download_url: str
    asset_name: str
    is_exe: bool

    def label(self) -> str:
        return f"v{self.version}  ({self.asset_name})"


def _parse_version(text: str) -> tuple:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if not m:
        return (0, 0, 0)
    return tuple(int(x) for x in m.groups())


def is_update_available(remote: AppReleaseInfo) -> bool:
    return remote.version_tuple > _parse_version(APP_VERSION)


def check_app_release(owner: str, repo: str) -> Optional[AppReleaseInfo]:
    """
    Cerca l'ultima release "companion-vX.Y.Z" su GitHub e l'asset adatto
    all'ambiente corrente (exe se frozen, altrimenti zip sorgente).
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/releases"
    try:
        r = requests.get(url, timeout=10, headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
    except requests.RequestException as e:
        log.warning("GitHub API non raggiungibile: %s", e)
        return None

    is_frozen = getattr(sys, "frozen", False)

    for release in r.json():
        tag = release.get("tag_name", "")
        if not tag.lower().startswith("companion-v"):
            continue

        ver_tuple = _parse_version(tag)
        ver_str = ".".join(str(v) for v in ver_tuple)

        for asset in release.get("assets", []):
            name = asset["name"]
            lname = name.lower()
            if is_frozen and lname.endswith(".exe"):
                return AppReleaseInfo(ver_str, ver_tuple, tag, asset["browser_download_url"], name, True)
            if not is_frozen and lname.endswith(".zip"):
                return AppReleaseInfo(ver_str, ver_tuple, tag, asset["browser_download_url"], name, False)

        # Release "companion-v*" trovata ma senza asset compatibile
        return None

    return None


def download_update(
    url: str,
    dest: Path,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> Path:
    """Scarica l'asset di aggiornamento in dest."""
    log.info("Download aggiornamento companion: %s", url)
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


def _extract_zip_flatten(zip_path: Path, dest_dir: Path) -> None:
    """Estrae zip_path in dest_dir, appiattendo un eventuale unico folder radice."""
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if not names:
            return

        # Se tutti i file condividono lo stesso primo segmento di path, lo rimuoviamo
        first_parts = {n.split("/", 1)[0] for n in names}
        strip_prefix = len(first_parts) == 1 and "/" in names[0]

        for name in names:
            rel = name.split("/", 1)[1] if strip_prefix else name
            if not rel:
                continue
            target = dest_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)


def _should_preserve(rel_path: Path) -> bool:
    if rel_path.parts and rel_path.parts[0] in _PRESERVE_NAMES:
        return True
    if rel_path.name in _PRESERVE_FILES:
        return True
    if rel_path.name.endswith(_PRESERVE_SUFFIXES):
        return True
    return False


def apply_source_update(zip_path: Path, target_dir: Path, log_cb: Optional[Callable[[str], None]] = None) -> None:
    """Estrae zip_path e copia i file su target_dir, preservando config/credenziali."""
    def _log(msg: str) -> None:
        log.info("[update] %s", msg)
        if log_cb:
            log_cb(msg)

    extract_dir = target_dir / "_update_tmp"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True)

    _log("Estrazione archivio...")
    _extract_zip_flatten(zip_path, extract_dir)

    copied = 0
    for item in extract_dir.rglob("*"):
        if item.is_dir():
            continue
        rel = item.relative_to(extract_dir)
        if _should_preserve(rel):
            continue
        dest = target_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, dest)
        copied += 1

    shutil.rmtree(extract_dir, ignore_errors=True)
    zip_path.unlink(missing_ok=True)
    _log(f"✓ {copied} file aggiornati.")


def restart_from_source() -> None:
    """Riavvia il processo Python corrente (gui.py)."""
    os.execv(sys.executable, [sys.executable] + sys.argv)


def apply_exe_update_and_restart(new_exe_path: Path, log_cb: Optional[Callable[[str], None]] = None) -> None:
    """
    Sostituisce l'eseguibile corrente con new_exe_path e lo rilancia.
    Usa uno script .bat di appoggio perché Windows non permette di
    sovrascrivere un .exe in esecuzione.
    """
    def _log(msg: str) -> None:
        log.info("[update] %s", msg)
        if log_cb:
            log_cb(msg)

    current_exe = Path(sys.executable).resolve()
    bat_path = current_exe.parent / "_petcube_update.bat"

    bat_content = (
        "@echo off\r\n"
        ":wait\r\n"
        f'tasklist /fi "imagename eq {current_exe.name}" | find /i "{current_exe.name}" >nul\r\n'
        "if not errorlevel 1 (\r\n"
        "  timeout /t 1 /nobreak >nul\r\n"
        "  goto wait\r\n"
        ")\r\n"
        f'move /y "{new_exe_path}" "{current_exe}"\r\n'
        f'start "" "{current_exe}"\r\n'
        'del "%~f0"\r\n'
    )
    bat_path.write_text(bat_content, encoding="utf-8")

    _log("Riavvio in corso...")
    subprocess.Popen(
        ["cmd", "/c", str(bat_path)],
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )
