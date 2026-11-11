"""Muestra los chats donde este bot de Telegram ha recibido actualizaciones."""

import json
import os
import sys
from urllib.request import urlopen

from dotenv import load_dotenv

load_dotenv()


def find_chats(token: str) -> dict[int, str]:
    with urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15) as response:
        data = json.load(response)
    if not data.get("ok"):
        raise SystemExit(f"Telegram rechazó el token: {data.get('description')}")
    chats: dict[int, str] = {}
    for update in data.get("result", []):
        for key in ("message", "channel_post", "my_chat_member", "edited_channel_post"):
            chat = (update.get(key) or {}).get("chat")
            if chat:
                chats[chat["id"]] = chat.get("title") or chat.get("username") or chat.get("first_name", "")
    return chats


if __name__ == "__main__":
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        sys.exit("Pon TELEGRAM_BOT_TOKEN en .env primero")
    chats = find_chats(token)
    if not chats:
        sys.exit("Sin resultados: escribe al bot o publica en el canal y repite.")
    for chat_id, name in chats.items():
        print(f"{chat_id}\t{name}")
