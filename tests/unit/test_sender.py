from __future__ import annotations

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.methods import SendMessage

from app.bot.keyboards.pending_reply import (
    BACK_TEXT,
    NOT_REQUIRED_TEXT,
    REASSIGN_CHAT_TEXT,
    REASSIGN_TICKET_TEXT,
    candidates_keyboard,
    notification_keyboard,
)
from app.bot.sender import map_telegram_error
from app.schemas.callbacks import ReassignAction
from app.schemas.users import TelegramUserRead
from app.services.telegram_sender import PermanentDeliveryError, TransientDeliveryError
from tests.factories import T0

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
    keyboard = notification_keyboard(5)
    rows = [[(b.text, b.callback_data) for b in row] for row in keyboard.inline_keyboard]
    assert rows == [
        [(NOT_REQUIRED_TEXT, "pr:nr:5")],
        [(REASSIGN_TICKET_TEXT, "ra:mt:5:0")],
        [(REASSIGN_CHAT_TEXT, "ra:mc:5:0")],
    ]


def test_candidates_keyboard() -> None:
    users = [
        TelegramUserRead(
            id=i,
            telegram_user_id=100 + i,
            username=None,
            first_name=None,
            last_name=None,
            display_name=f"User {i}",
            is_bot=False,
            last_seen_at=T0,
        )
        for i in range(2)
    ]
    keyboard = candidates_keyboard(7, ReassignAction.SET_TICKET, users)
    rows = [[(b.text, b.callback_data) for b in row] for row in keyboard.inline_keyboard]
    assert rows == [
        [("User 0", "ra:st:7:100")],
        [("User 1", "ra:st:7:101")],
        [(BACK_TEXT, "ra:bk:7:0")],
    ]
    for row in keyboard.inline_keyboard:
        assert len((row[0].callback_data or "").encode()) < 64
