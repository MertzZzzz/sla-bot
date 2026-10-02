from __future__ import annotations

from aiogram import Dispatcher

from app.bot.middlewares.logging import UpdateLoggingMiddleware
from app.bot.routers import admin_chats, callbacks, common, messages, stats
from app.bot.services import BotServices
from app.core.config import Settings


def build_dispatcher(settings: Settings, services: BotServices) -> Dispatcher:
    """Settings and services are created once per process and injected as workflow data."""
    dp = Dispatcher(settings=settings, services=services)
    dp.update.outer_middleware(UpdateLoggingMiddleware())
    # Order matters: commands first, the catch-all message router last.
    dp.include_routers(
        common.router,
        admin_chats.router,
        stats.router,
        callbacks.router,
        messages.router,
    )
    return dp
