"""Fake Telegram API + dispatcher feeding for dispatcher-level tests."""

from __future__ import annotations

import itertools
from collections.abc import AsyncGenerator
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import (
    CreateChatInviteLink,
    EditMessageText,
    GetChatMember,
    GetMe,
    SendMessage,
    TelegramMethod,
)
from aiogram.types import (
    CallbackQuery,
    Chat,
    ChatInviteLink,
    ChatMemberMember,
    InlineKeyboardMarkup,
    Message,
    MessageEntity,
    Update,
    User,
)

from app.bot.dispatcher import build_dispatcher
from app.bot.services import BotServices
from app.core.config import Settings
from tests.factories import T0

BOT_USER = User(id=777, is_bot=True, first_name="SLA", username="sla_test_bot")


class RecordingSession(BaseSession):
    """Records every Bot API call; can simulate blocked users and chat members."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []
        self.forbidden_chats: set[int] = set()
        self.members: dict[int, User] = {}
        self.invites_forbidden = False

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,  # noqa: ASYNC109 - aiogram API
    ) -> Any:
        self.requests.append(method)
        if isinstance(method, GetMe):
            return BOT_USER
        if isinstance(method, GetChatMember):
            user = self.members.get(method.user_id)
            if user is None:
                raise TelegramBadRequest(method, "Bad Request: user not found")
            return ChatMemberMember(user=user)
        if isinstance(method, CreateChatInviteLink):
            if self.invites_forbidden:
                raise TelegramBadRequest(method, "Bad Request: not enough rights")
            return ChatInviteLink(
                invite_link=f"https://t.me/+invite{len(self.requests)}",
                creator=BOT_USER,
                creates_join_request=False,
                is_primary=False,
                is_revoked=False,
                name=method.name,
                member_limit=method.member_limit,
            )
        if isinstance(method, SendMessage | EditMessageText):
            chat_id = int(getattr(method, "chat_id", 0) or 0)
            if chat_id in self.forbidden_chats:
                raise TelegramForbiddenError(  # type: ignore[misc]  # union of methods
                    method, "Forbidden: bot can't initiate conversation"
                )
            return Message(
                message_id=900 + len(self.requests),
                date=T0,
                chat=Chat(id=chat_id, type="private" if chat_id > 0 else "supergroup"),
                text=method.text,
            )
        return True

    async def close(self) -> None:
        return None

    def stream_content(self, *args: Any, **kwargs: Any) -> AsyncGenerator[bytes, None]:
        raise NotImplementedError

    def of(self, kind: type[TelegramMethod[Any]]) -> list[Any]:
        return [r for r in self.requests if isinstance(r, kind)]

    def last_screen(self) -> Any:
        """The latest message text/markup the bot sent or edited."""
        return [r for r in self.requests if isinstance(r, SendMessage | EditMessageText)][-1]


_ids = itertools.count(1)
_dispatcher: Dispatcher | None = None
_storage = MemoryStorage()


def reset_fsm() -> None:
    _storage.storage.clear()


async def feed(bot: Bot, settings: Settings, services: BotServices, update: Update) -> None:
    # aiogram routers are module singletons and can be attached to one dispatcher only;
    # per-test dependencies are passed as feed_update kwargs (they override workflow data).
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = build_dispatcher(settings, services, _storage)
    await _dispatcher.feed_update(bot, update, settings=settings, services=services)


def message(chat: Chat, sender: User, text: str | None = None, **kwargs: Any) -> Update:
    entities = None
    if text and text.startswith("/"):
        entities = [MessageEntity(type="bot_command", offset=0, length=len(text.split()[0]))]
    msg = Message(
        message_id=next(_ids) + 100,
        date=T0,
        chat=chat,
        from_user=sender,
        text=text,
        entities=entities,
        **kwargs,
    )
    return Update(update_id=next(_ids), message=msg)


def callback(
    chat: Chat,
    sender: User,
    data: str,
    *,
    text: str = "menu",
    markup: InlineKeyboardMarkup | None = None,
    **message_kwargs: Any,
) -> Update:
    msg = Message(
        message_id=555,
        date=T0,
        chat=chat,
        from_user=BOT_USER,
        text=text,
        reply_markup=markup,
        **message_kwargs,
    )
    query = CallbackQuery(
        id=str(next(_ids)), from_user=sender, chat_instance="ci", message=msg, data=data
    )
    return Update(update_id=next(_ids), callback_query=query)


def buttons(markup: InlineKeyboardMarkup | None) -> dict[str, str]:
    """Button text -> callback data."""
    assert markup is not None
    return {b.text: b.callback_data or "" for row in markup.inline_keyboard for b in row}
