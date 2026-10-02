"""aiogram adapter for the worker-side ``NotificationSender`` port."""

from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)

from app.bot.keyboards.pending_reply import notification_keyboard
from app.core.config import TelegramSettings
from app.services.telegram_sender import (
    DeliveryError,
    PermanentDeliveryError,
    TransientDeliveryError,
)


def build_bot(settings: TelegramSettings) -> Bot:
    return Bot(
        token=settings.bot_token.get_secret_value(),
        session=AiohttpSession(timeout=settings.request_timeout_seconds),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )


def map_telegram_error(exc: TelegramAPIError | TelegramNetworkError) -> DeliveryError:
    name = type(exc).__name__
    if isinstance(exc, TelegramRetryAfter):
        return TransientDeliveryError(str(exc), error_type=name, retry_after=float(exc.retry_after))
    if isinstance(exc, TelegramNetworkError | TelegramServerError):
        return TransientDeliveryError(str(exc), error_type=name)
    if isinstance(
        exc,
        TelegramForbiddenError
        | TelegramBadRequest
        | TelegramNotFound
        | TelegramUnauthorizedError
        | TelegramMigrateToChat,
    ):
        return PermanentDeliveryError(str(exc), error_type=name)
    return TransientDeliveryError(str(exc), error_type=name)


class AiogramNotificationSender:
    """Synchronous facade for Celery workers: one short-lived event loop per send."""

    def __init__(self, settings: TelegramSettings) -> None:
        self._settings = settings

    def send_notification(
        self, *, chat_id: int, thread_id: int | None, text: str, pending_reply_id: int
    ) -> int:
        return asyncio.run(self._send(chat_id, thread_id, text, pending_reply_id))

    async def _send(
        self, chat_id: int, thread_id: int | None, text: str, pending_reply_id: int
    ) -> int:
        bot = build_bot(self._settings)
        try:
            message = await bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text=text,
                reply_markup=notification_keyboard(pending_reply_id),
            )
        except (TelegramAPIError, TelegramNetworkError) as exc:
            raise map_telegram_error(exc) from exc
        except TimeoutError as exc:
            raise TransientDeliveryError("request timed out", error_type="TimeoutError") from exc
        finally:
            await bot.session.close()
        return message.message_id
