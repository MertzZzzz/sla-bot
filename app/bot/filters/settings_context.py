from __future__ import annotations

from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message

from app.core.config import Settings


class IsSettingsChat(Filter):
    """Private chat with the bot or one of APP_TELEGRAM__ADMIN_CHAT_IDS."""

    async def __call__(self, event: Message | CallbackQuery, settings: Settings) -> bool:
        message = event.message if isinstance(event, CallbackQuery) else event
        if message is None:
            return False
        return settings.is_settings_chat(message.chat.id, message.chat.type)
