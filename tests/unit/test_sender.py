from __future__ import annotations

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.methods import SendMessage

from app.bot.keyboards.pending_reply import NOT_REQUIRED_TEXT, not_required_keyboard
from app.bot.sender import map_telegram_error
from app.services.telegram_sender import PermanentDeliveryError, TransientDeliveryError

METHOD = SendMessage(chat_id=1, text="x")


def test_transient_errors() -> None:
    retry = map_telegram_error(TelegramRetryAfter(METHOD, "flood", retry_after=12))
    assert isinstance(retry, TransientDeliveryError)
    assert retry.retry_after == 12
    assert isinstance(
        map_telegram_error(TelegramNetworkError(METHOD, "boom")), TransientDeliveryError
    )
    assert isinstance(
        map_telegram_error(TelegramServerError(METHOD, "502")), TransientDeliveryError
    )


def test_permanent_errors() -> None:
    forbidden = map_telegram_error(TelegramForbiddenError(METHOD, "bot was kicked"))
    assert isinstance(forbidden, PermanentDeliveryError)
    assert forbidden.error_type == "TelegramForbiddenError"
    assert isinstance(
        map_telegram_error(TelegramBadRequest(METHOD, "chat not found")), PermanentDeliveryError
    )


def test_keyboard() -> None:
    keyboard = not_required_keyboard(5)
    button = keyboard.inline_keyboard[0][0]
    assert button.text == NOT_REQUIRED_TEXT
    assert button.callback_data == "pr:nr:5"
