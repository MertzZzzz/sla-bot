from __future__ import annotations

from aiogram.filters import Filter
from aiogram.types import Message

from app.bot.extractors import GROUP_TYPES
from app.bot.services import BotServices
from app.schemas.chats import MonitoredChatDetails


class IsGroupChat(Filter):
    async def __call__(self, message: Message) -> bool:
        return message.chat.type in GROUP_TYPES


class IsMonitoredChat(Filter):
    """Passes for groups registered via /chat_add; injects ``monitored`` into handlers."""

    async def __call__(
        self, message: Message, services: BotServices
    ) -> bool | dict[str, MonitoredChatDetails]:
        if message.chat.type not in GROUP_TYPES:
            return False
        details = await services.chats.get_details(message.chat.id)
        if details is None:
            return False
        return {"monitored": details}
