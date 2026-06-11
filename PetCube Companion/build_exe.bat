@echo off
REM Costruisce PetCube Companion.exe con PyInstaller
REM Esegui una volta: pip install pyinstaller

cd /d "%~dp0"

python -m PyInstaller ^
  --onefile ^
  --windowed ^
  --name "PetCube Companion" ^
  --add-data "config.json;." ^
  --collect-all spacy ^
  --collect-all it_core_news_sm ^
  --hidden-import bleak ^
  --hidden-import bleak.backends.winrt ^
  --hidden-import customtkinter ^
  --hidden-import pystray ^
  --hidden-import PIL ^
  --hidden-import esptool ^
  --hidden-import serial ^
  --hidden-import serial.tools.list_ports ^
  --hidden-import plugins.base ^
  --hidden-import plugins.calendar_plugin ^
  --hidden-import plugins.gmail_plugin ^
  --hidden-import plugins.hacknplan_plugin ^
  --hidden-import plugins.discord_plugin ^
  --hidden-import plugins.telegram_plugin ^
  --hidden-import plugins.whatsapp_plugin ^
  gui.py

echo.
echo Build completata. L'eseguibile si trova in dist\PetCube Companion.exe
pause
