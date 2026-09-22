"""
Cek validitas BOT_TOKEN di `.env` sebelum menjalankan bot.

    python validate_token.py
"""

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("BOT_TOKEN", "").strip()
if not token:
    raise SystemExit("BOT_TOKEN kosong/tidak ada di .env")

try:
    resp = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15)
except requests.RequestException as exc:
    # Redact token dari pesan error agar tidak bocor ke log/terminal.
    safe = str(exc).replace(token, "***REDACTED***")
    raise SystemExit(f"Gagal menghubungi Telegram: {safe}")

if resp.ok:
    bot = resp.json()["result"]
    print(
        f"✅ TOKEN VALID — bot: {bot.get('first_name')} "
        f"(@{bot.get('username')}, id={bot.get('id')})"
    )
else:
    print(f" TOKEN INVALID — HTTP {resp.status_code}: {resp.text}")
    sys.exit(1)