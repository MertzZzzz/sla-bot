"""Identify a Telegram user from an admin's input in the settings menu."""

from __future__ import annotations

import re

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    KeyboardButton,
    KeyboardButtonRequestUsers,
    Message,
    MessageOriginHiddenUser,
    MessageOriginUser,
    ReplyKeyboardMarkup,
)

from app.bot.extractors import user_data
from app.bot.services import BotServices
from app.schemas.users import TelegramUserData, TelegramUserRead

PICK_USER_TEXT = "👤 Выбрать пользователя"
PICK_REQUEST_ID = 1
USERNAME_RE = re.compile(r"@?[A-Za-z0-9_]{4,32}")
USAGE = (
    "Укажите пользователя: <code>@username</code>, числовой Telegram ID "
    "или перешлите сюда любое его сообщение."
)


class UserInputError(ValueError):
    """User-facing explanation why the input could not be resolved."""


def pick_user_keyboard() -> ReplyKeyboardMarkup:
    """Native Telegram user picker; works only in a private chat with the bot."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=PICK_USER_TEXT,
                    request_users=KeyboardButtonRequestUsers(
                        request_id=PICK_REQUEST_ID,
                        user_is_bot=False,
                        max_quantity=1,
                        request_name=True,
                        request_username=True,
                    ),
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def from_read(user: TelegramUserRead) -> TelegramUserData:
    return TelegramUserData(
        telegram_user_id=user.telegram_user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        is_bot=user.is_bot,
    )


async def resolve_user(
    message: Message, bot: Bot, services: BotServices, chat_telegram_id: int | None
) -> TelegramUserData:
    """Shared user (picker) > forwarded message > @username > numeric ID.

    For a numeric ID the bot asks Telegram for the member of ``chat_telegram_id`` to
    learn the name; unknown IDs are still accepted (the name is filled in once the
    user writes).
    """
    if message.users_shared and message.users_shared.users:
        shared = message.users_shared.users[0]
        return TelegramUserData(
            telegram_user_id=shared.user_id,
            first_name=shared.first_name,
            last_name=shared.last_name,
            username=shared.username,
        )
    origin = message.forward_origin
    if isinstance(origin, MessageOriginUser):
        return _not_bot(user_data(origin.sender_user))
    if isinstance(origin, MessageOriginHiddenUser):
        msg = "Пользователь скрыл аккаунт в пересылаемых сообщениях — укажите @username или ID."
        raise UserInputError(msg)
    text = (message.text or "").strip()
    if text.isdigit():
        return await _by_id(int(text), bot, services, chat_telegram_id)
    if not USERNAME_RE.fullmatch(text):
        raise UserInputError(USAGE)
    known = await services.users.find(text)
    if known is None:
        msg = (
            f"Пользователь {text if text.startswith('@') else '@' + text} пока неизвестен боту "
            "(он ещё не писал в чаты с ботом). Укажите числовой ID или перешлите его сообщение."
        )
        raise UserInputError(msg)
    return _not_bot(from_read(known))


async def _by_id(
    telegram_id: int, bot: Bot, services: BotServices, chat_telegram_id: int | None
) -> TelegramUserData:
    if telegram_id <= 0:
        raise UserInputError(USAGE)
    if chat_telegram_id is not None:
        try:
            member = await bot.get_chat_member(chat_telegram_id, telegram_id)
            return _not_bot(user_data(member.user))
        except TelegramAPIError:
            pass  # not in the chat or not visible: fall back to what we know
    known = await services.users.find(str(telegram_id))
    return _not_bot(from_read(known)) if known else TelegramUserData(telegram_user_id=telegram_id)


def _not_bot(user: TelegramUserData) -> TelegramUserData:
    if user.is_bot:
        msg = "Нельзя выбрать бота."
        raise UserInputError(msg)
    return user
