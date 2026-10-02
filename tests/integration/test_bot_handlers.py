"""Dispatcher-level acceptance tests: real routers/filters/services, fake Telegram API."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageText,
    GetMe,
    SendMessage,
    TelegramMethod,
)
from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardMarkup,
    Message,
    MessageEntity,
    Update,
    User,
)
from sqlalchemy.orm import Session, sessionmaker

from app.bot.dispatcher import build_dispatcher
from app.bot.keyboards.pending_reply import not_required_keyboard
from app.bot.services import BotServices
from app.core.config import Settings
from app.core.enums import PendingReplyStatus, Priority
from tests.factories import CHAT_ID, T0, FakeClock, incoming
from tests.integration.helpers import ADMIN_ID, CLIENT, setup_chat, tickets

pytestmark = pytest.mark.integration

BOT_USER = User(id=777, is_bot=True, first_name="SLA", username="sla_test_bot")
GROUP = Chat(id=CHAT_ID, type="supergroup", title="Поддержка VIP")
ADMIN_TG = User(id=ADMIN_ID, is_bot=False, first_name="Admin")
STRANGER_TG = User(id=9999, is_bot=False, first_name="Stranger")
CLIENT_TG = User(id=CLIENT.telegram_user_id, is_bot=False, first_name="Client")
NOTIFY = Chat(id=-100777, type="supergroup", title="Escalations")


class RecordingSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,  # noqa: ASYNC109 - aiogram API
    ) -> Any:
        self.requests.append(method)
        if isinstance(method, GetMe):
            return BOT_USER
        if isinstance(method, SendMessage | EditMessageText):
            chat_id = getattr(method, "chat_id", CHAT_ID)
            return Message(
                message_id=900 + len(self.requests),
                date=T0,
                chat=Chat(id=int(chat_id or CHAT_ID), type="supergroup"),
                text=method.text,
            )
        return True

    async def close(self) -> None:
        return None

    def stream_content(self, *args: Any, **kwargs: Any) -> AsyncGenerator[bytes, None]:
        raise NotImplementedError

    def of(self, kind: type[TelegramMethod[Any]]) -> list[Any]:
        return [r for r in self.requests if isinstance(r, kind)]


@pytest.fixture
def tg() -> RecordingSession:
    return RecordingSession()


@pytest.fixture
def bot(tg: RecordingSession) -> Bot:
    return Bot("42:TEST", session=tg)


_update_id = 0


def _next_id() -> int:
    global _update_id
    _update_id += 1
    return _update_id


def command(
    text: str, sender: User, reply_to: Message | None = None, message_id: int = 100
) -> Update:
    cmd_len = len(text.split(maxsplit=1)[0])
    message = Message(
        message_id=message_id,
        date=T0,
        chat=GROUP,
        from_user=sender,
        text=text,
        entities=[MessageEntity(type="bot_command", offset=0, length=cmd_len)],
        reply_to_message=reply_to,
    )
    return Update(update_id=_next_id(), message=message)


def press(sender: User, pending_reply_id: int, markup: InlineKeyboardMarkup) -> Update:
    notification = Message(
        message_id=555,
        date=T0,
        chat=NOTIFY,
        from_user=BOT_USER,
        text="🔴 SLA нарушен\n\nТекст: <тест>",
        reply_markup=markup,
    )
    callback = CallbackQuery(
        id="cb1",
        from_user=sender,
        chat_instance="ci",
        message=notification,
        data=markup.inline_keyboard[0][0].callback_data,
    )
    return Update(update_id=_next_id(), callback_query=callback)


_dispatcher: Dispatcher | None = None


async def feed(bot: Bot, settings: Settings, services: BotServices, update: Update) -> None:
    # aiogram routers are module singletons and can be attached to one dispatcher only;
    # per-test dependencies are passed as feed_update kwargs (they override workflow data).
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = build_dispatcher(settings, services)
    await _dispatcher.feed_update(bot, update, settings=settings, services=services)


async def test_admin_adds_chat_and_configures_sla(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices
) -> None:
    await feed(bot, settings, services, command("/chat_add", ADMIN_TG))
    assert "добавлен в мониторинг" in tg.of(SendMessage)[-1].text
    await feed(bot, settings, services, command("/chat_sla 120", ADMIN_TG))
    await feed(bot, settings, services, command("/chat_priority P1", ADMIN_TG))
    details = await services.chats.get_details(CHAT_ID)
    assert details is not None
    assert (details.chat.sla_seconds, details.chat.priority) == (120, Priority.P1)


async def test_invalid_arguments_get_clear_error(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices
) -> None:
    await feed(bot, settings, services, command("/chat_add", ADMIN_TG))
    await feed(bot, settings, services, command("/chat_sla soon", ADMIN_TG))
    assert "Использование: /chat_sla" in tg.of(SendMessage)[-1].text


async def test_non_admin_cannot_change_settings(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices
) -> None:
    await setup_chat(services, sla_seconds=900)
    await feed(bot, settings, services, command("/chat_sla 60", STRANGER_TG))
    assert "Недостаточно прав" in tg.of(SendMessage)[-1].text
    await feed(bot, settings, services, command("/stats", STRANGER_TG))
    assert "Недостаточно прав" in tg.of(SendMessage)[-1].text
    details = await services.chats.get_details(CHAT_ID)
    assert details is not None and details.chat.sla_seconds == 900


async def test_commands_require_monitored_chat(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices
) -> None:
    await feed(bot, settings, services, command("/chat_settings", ADMIN_TG))
    assert "не отслеживается" in tg.of(SendMessage)[-1].text


async def test_responsible_assigned_by_reply(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices
) -> None:
    await feed(bot, settings, services, command("/chat_add", ADMIN_TG))
    await feed(bot, settings, services, command("/chat_responsible", ADMIN_TG))
    assert "Reply" in tg.of(SendMessage)[-1].text
    target = Message(message_id=50, date=T0, chat=GROUP, from_user=CLIENT_TG, text="hi")
    await feed(bot, settings, services, command("/chat_responsible", ADMIN_TG, reply_to=target))
    details = await services.chats.get_details(CHAT_ID)
    assert details is not None and details.responsible is not None
    assert details.responsible.telegram_user_id == CLIENT_TG.id
    assert [u.telegram_user_id for u in details.responders] == [CLIENT_TG.id]


async def test_group_message_creates_ticket_via_dispatcher(
    bot: Bot,
    settings: Settings,
    services: BotServices,
    sync_factory: sessionmaker[Session],
) -> None:
    await setup_chat(services)
    msg = Message(message_id=10, date=T0, chat=GROUP, from_user=CLIENT_TG, text="Помогите")
    update = Update(update_id=_next_id(), message=msg)
    await feed(bot, settings, services, update)
    await feed(bot, settings, services, update)  # webhook redelivery
    assert [t.source_message_id for t in tickets(sync_factory)] == [10]


async def test_not_required_button(
    bot: Bot,
    tg: RecordingSession,
    settings: Settings,
    services: BotServices,
    sync_factory: sessionmaker[Session],
    clock: FakeClock,
) -> None:
    await setup_chat(services)
    await services.messages.process(incoming(10, CLIENT, date=clock.now()))
    [ticket] = tickets(sync_factory)
    markup = not_required_keyboard(ticket.id)

    await feed(bot, settings, services, press(STRANGER_TG, ticket.id, markup))
    denied = tg.of(AnswerCallbackQuery)[-1]
    assert "Недостаточно прав" in (denied.text or "") and denied.show_alert
    assert tg.of(EditMessageText) == []

    await feed(bot, settings, services, press(ADMIN_TG, ticket.id, markup))
    [edit] = tg.of(EditMessageText)
    assert edit.reply_markup is None
    assert edit.text.startswith("🔴 SLA нарушен")  # original text kept
    assert "&lt;тест&gt;" in edit.text
    assert "✅ Ответ не требуется. Отметил:" in edit.text
    assert f"tg://user?id={ADMIN_ID}" in edit.text
    assert tickets(sync_factory)[0].status is PendingReplyStatus.NOT_REQUIRED

    await feed(bot, settings, services, press(ADMIN_TG, ticket.id, markup))
    assert "Уже отмечено" in (tg.of(AnswerCallbackQuery)[-1].text or "")
    assert len(tg.of(EditMessageText)) == 1
