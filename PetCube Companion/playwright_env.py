"""
playwright_env.py
Quando l'app gira come eseguibile PyInstaller (--onefile), Playwright cerca
i browser dentro la cartella temporanea _MEI, ricreata vuota ad ogni avvio:
i browser scaricati con 'playwright install' andrebbero persi.

Questo modulo punta PLAYWRIGHT_BROWSERS_PATH a una cartella persistente in
%LOCALAPPDATA%, cosi' l'installazione dei browser fatta una tantum (vedi
install_playwright.bat) resta valida tra un avvio e l'altro dell'exe.

Va importato (e setup_playwright_browsers_path() chiamato) prima di
qualsiasi uso di Playwright.
"""
import os
import sys


def setup_playwright_browsers_path() -> None:
    if getattr(sys, "frozen", False):
        browsers_dir = os.path.join(
            os.environ["LOCALAPPDATA"], "PetCube Companion", "playwright-browsers"
        )
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", browsers_dir)
