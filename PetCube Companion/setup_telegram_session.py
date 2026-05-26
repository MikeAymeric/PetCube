"""
setup_telegram_session.py
Utility interattiva per autenticare il client Telethon e salvare la sessione.
Eseguire una sola volta prima di abilitare il plugin Telegram.

Uso:
    cd "PetCube Companion"
    python setup_telegram_session.py

Prerequisiti:
    pip install telethon
    api_id e api_hash ottenuti da https://my.telegram.org
"""
import asyncio
import json
import os
from pathlib import Path


async def main() -> None:
    try:
        from telethon import TelegramClient
    except ImportError:
        print("❌ Telethon non installato. Eseguire: pip install telethon")
        return

    print("=== Setup sessione Telegram per PetCube Companion ===\n")

    # Leggi da config.json se esiste, altrimenti chiedi interattivamente
    config_path = Path("config.json")
    api_id = api_hash = phone = session_file = None

    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            tg = cfg.get("plugins", {}).get("telegram", {})
            api_id      = tg.get("api_id")
            api_hash    = tg.get("api_hash", "")
            phone       = tg.get("phone_number", "")
            session_file = tg.get("session_file", "telegram_session")
            if api_id and api_hash:
                print(f"Configurazione letta da config.json (api_id={api_id})\n")
        except Exception:
            pass

    if not api_id:
        api_id = int(input("API ID (da my.telegram.org): ").strip())
    if not api_hash:
        api_hash = input("API Hash: ").strip()
    if not phone:
        phone = input("Numero di telefono (es. +393331234567): ").strip()
    if not session_file:
        session_file = input("Nome file sessione [telegram_session]: ").strip() or "telegram_session"

    client = TelegramClient(session_file, api_id, api_hash)
    print("\nConnessione a Telegram...")
    await client.start(phone=phone)

    me = await client.get_me()
    print(f"\n✅ Autenticato come {me.first_name} (@{me.username})")
    print(f"   Sessione salvata in '{session_file}.session'\n")
    print("Puoi ora abilitare il plugin Telegram in config.json e avviare la companion.")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
