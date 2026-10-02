from __future__ import annotations

from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message

from app.bot.services import BotServices


class IsGlobalAdmin(Filter):
    """Sender is an admin from .env or one added through the settings menu."""

    async def __call__(self, event: Message | CallbackQuery, services: BotServices) -> bool:
        user = event.from_user
        return user is not None and await services.admins.is_admin(user.id)
