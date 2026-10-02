from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

logger = logging.getLogger("app.bot.updates")


def update_context(update: Update) -> dict[str, Any]:
    ctx: dict[str, Any] = {"update_id": update.update_id}
    message = update.message or update.edited_message
    if message is not None:
        ctx["telegram_chat_id"] = message.chat.id
        ctx["telegram_message_id"] = message.message_id
        ctx["telegram_user_id"] = message.from_user.id if message.from_user else None
    elif update.callback_query is not None:
        ctx["telegram_user_id"] = update.callback_query.from_user.id
        if update.callback_query.message is not None:
            ctx["telegram_chat_id"] = update.callback_query.message.chat.id
            ctx["telegram_message_id"] = update.callback_query.message.message_id
    return ctx


class UpdateLoggingMiddleware(BaseMiddleware):
    """Outer middleware: logs unhandled errors with update context, then re-raises."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception as exc:
            ctx = update_context(event) if isinstance(event, Update) else {}
            logger.exception(
                "unhandled error while processing update",
                extra={
                    "event": "update_failed",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    **ctx,
                },
            )
            raise
