"""
list_telegram_chats.py
Lista tutte le chat Telegram con il loro ID — utile per configurare
monitor_chat_ids in config.json.

Uso:
    python list_telegram_chats.py
    python list_telegram_chats.py --filter gruppo    # filtra per nome
"""
import asyncio
import json
import sys
from pathlib import Path


async def main() -> None:
    try:
        from telethon import TelegramClient
        from telethon.tl.types import (
            User, Chat, Channel,
            InputPeerEmpty,
        )
    except ImportError:
        print("❌ Telethon non installato. Eseguire: pip install telethon")
        return

    # Leggi credenziali da config.json
    config_path = Path("config.json")
    if not config_path.exists():
        print("❌ config.json non trovato.")
        return

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    tg = cfg.get("plugins", {}).get("telegram", {})
    api_id   = tg.get("api_id")
    api_hash = tg.get("api_hash", "")
    session  = tg.get("session_file", "telegram_session")

    if not api_id or not api_hash:
        print("❌ api_id / api_hash mancanti in config.json > plugins > telegram")
        return

    name_filter = sys.argv[2].lower() if len(sys.argv) >= 3 and sys.argv[1] == "--filter" else None

    async with TelegramClient(session, api_id, api_hash) as client:
        print(f"\n{'TIPO':<12} {'ID':>15}  NOME")
        print("─" * 55)

        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            chat_id = dialog.id

            if isinstance(entity, User):
                tipo = "👤 DM"
                name = f"{entity.first_name or ''} {entity.last_name or ''}".strip()
                if entity.username:
                    name += f" (@{entity.username})"
            elif isinstance(entity, Channel):
                tipo = "📢 Canale" if entity.broadcast else "👥 Supergruppo"
                name = entity.title or "?"
            elif isinstance(entity, Chat):
                tipo = "👥 Gruppo"
                name = entity.title or "?"
            else:
                continue

            if name_filter and name_filter not in name.lower():
                continue

            print(f"{tipo:<12} {chat_id:>15}  {name}")

        print("\n💡 Copia gli ID che vuoi in config.json > plugins > telegram > monitor_chat_ids")
        print('   Esempio: "monitor_chat_ids": [-1001234567890, 987654321]\n')


if __name__ == "__main__":
    asyncio.run(main())
