from __future__ import annotations

from datetime import datetime

from app.core.types import TelegramUserId
from app.schemas.common import Schema


class TelegramUserData(Schema):
    """User data extracted from an incoming Telegram update."""

    telegram_user_id: TelegramUserId
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    is_bot: bool = False

    @property
    def display_name(self) -> str:
        full = " ".join(p for p in (self.first_name, self.last_name) if p)
        if full:
            return full
        if self.username:
            return f"@{self.username}"
        return str(self.telegram_user_id)


class TelegramUserRead(Schema):
    id: int
    telegram_user_id: TelegramUserId
    username: str | None
    first_name: str | None
    last_name: str | None
    display_name: str | None
    is_bot: bool
    last_seen_at: datetime
