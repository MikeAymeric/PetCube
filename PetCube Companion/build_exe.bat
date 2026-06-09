@echo off
REM Costruisce PetCube Companion.exe con PyInstaller
REM Esegui una volta: pip install pyinstaller

cd /d "%~dp0"

pyinstaller ^
  --onefile ^
  --windowed ^
  --name "PetCube Companion" ^
  --add-data "config.json;." ^
  --hidden-import bleak ^
  --hidden-import bleak.backends.winrt ^
  --hidden-import customtkinter ^
  --hidden-import pystray ^
  --hidden-import PIL ^
  --hidden-import esptool ^
  --hidden-import serial ^
  --hidden-import serial.tools.list_ports ^
  gui.py

echo.
echo Build completata. L'eseguibile si trova in dist\PetCube Companion.exe
pause
