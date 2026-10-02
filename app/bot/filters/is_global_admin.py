from __future__ import annotations

from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message

from app.core.config import Settings


class IsGlobalAdmin(Filter):
    """Passes when the sender's Telegram ID is listed in APP_TELEGRAM__ADMIN_TELEGRAM_IDS."""

    async def __call__(self, event: Message | CallbackQuery, settings: Settings) -> bool:
        user = event.from_user
        return user is not None and settings.is_global_admin(user.id)
