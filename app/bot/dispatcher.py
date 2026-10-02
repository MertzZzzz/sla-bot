from __future__ import annotations

from aiogram import Dispatcher
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.middlewares.logging import UpdateLoggingMiddleware
from app.bot.routers import callbacks, customer_chat, menu, messages, stats
from app.bot.services import BotServices
from app.core.config import Settings


def build_dispatcher(
    settings: Settings, services: BotServices, storage: BaseStorage | None = None
) -> Dispatcher:
    """Settings and services are created once per process and injected as workflow data."""
    dp = Dispatcher(storage=storage or MemoryStorage(), settings=settings, services=services)
    dp.update.outer_middleware(UpdateLoggingMiddleware())
    # Order matters: commands and the menu first, the catch-all message router last.
    dp.include_routers(
        customer_chat.router,
        menu.router,
        stats.router,
        callbacks.router,
        messages.router,
    )
    return dp
