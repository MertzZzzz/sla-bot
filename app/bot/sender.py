"""aiogram adapter for the worker-side ``NotificationSender`` port."""

from __future__ import annotations

import asyncio
from urllib.parse import quote, urlsplit

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import PRODUCTION, TelegramAPIServer
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
from aiohttp import ClientError
from aiohttp_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError

from app.bot.keyboards.pending_reply import notification_keyboard
from app.core.config import TelegramSettings
from app.services.telegram_sender import (
    DeliveryError,
    PermanentDeliveryError,
    TransientDeliveryError,
)


def build_proxy(settings: TelegramSettings) -> str | None:
    """Proxy URL for aiogram's AiohttpSession (HTTP, SOCKS4 or SOCKS5).

    Separately configured credentials are percent-encoded into the URL, so any
    characters (``@``, ``:``, ``/``) are safe in the password.
    """
    if settings.proxy_url is None:
        return None
    url = settings.proxy_url.get_secret_value()
    if settings.proxy_username is None or settings.proxy_password is None:
        return url
    parts = urlsplit(url)
    user = quote(settings.proxy_username, safe="")
    password = quote(settings.proxy_password.get_secret_value(), safe="")
    return parts._replace(netloc=f"{user}:{password}@{parts.hostname}:{parts.port}").geturl()


def build_session(
    settings: TelegramSettings, api: TelegramAPIServer = PRODUCTION
) -> AiohttpSession:
    return AiohttpSession(
        api=api, proxy=build_proxy(settings), timeout=settings.request_timeout_seconds
    )


def build_bot(settings: TelegramSettings, api: TelegramAPIServer = PRODUCTION) -> Bot:
    return Bot(
        token=settings.bot_token.get_secret_value(),
        session=build_session(settings, api),
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

    def __init__(self, settings: TelegramSettings, api: TelegramAPIServer = PRODUCTION) -> None:
        self._settings = settings
        self._api = api

    def send_notification(
        self, *, chat_id: int, thread_id: int | None, text: str, pending_reply_id: int
    ) -> int:
        return asyncio.run(self._send(chat_id, thread_id, text, pending_reply_id))

    async def _send(
        self, chat_id: int, thread_id: int | None, text: str, pending_reply_id: int
    ) -> int:
        bot = build_bot(self._settings, self._api)
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
        except (ProxyError, ProxyConnectionError, ProxyTimeoutError, ClientError, OSError) as exc:
            # Proxy unreachable/refused or a low-level network failure that aiogram does
            # not wrap: retry later instead of crashing the task.
            raise TransientDeliveryError(str(exc), error_type=type(exc).__name__) from exc
        finally:
            await bot.session.close()
        return message.message_id
