@echo off
REM Installa Chromium per Playwright nella cartella persistente usata
REM da PetCube Companion.exe (vedi playwright_env.py).
REM Da eseguire UNA TANTUM sulla macchina dove gira l'exe.

set "PLAYWRIGHT_BROWSERS_PATH=%LOCALAPPDATA%\PetCube Companion\playwright-browsers"

python -m playwright install chromium

echo.
echo Installazione completata in "%PLAYWRIGHT_BROWSERS_PATH%"
pause
