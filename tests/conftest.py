from __future__ import annotations

import os

# Unit tests must never pick up a developer's real .env or secrets.
os.environ.setdefault("APP_TELEGRAM__BOT_TOKEN", "123456:TEST-TOKEN")
os.environ.setdefault("APP_TELEGRAM__ADMIN_TELEGRAM_IDS", "1000")
