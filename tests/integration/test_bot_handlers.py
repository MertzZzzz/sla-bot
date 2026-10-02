"""Dispatcher-level tests of group messages and SLA notification buttons."""

from __future__ import annotations

import pytest
from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, EditMessageText
from aiogram.types import Chat, InlineKeyboardMarkup, Message, Update, User
from sqlalchemy.orm import Session, sessionmaker

from app.bot.extractors import user_data
from app.bot.keyboards.pending_reply import notification_keyboard
from app.bot.services import BotServices
from app.core.config import Settings
from app.core.enums import PendingReplyStatus
from tests.factories import CHAT_ID, T0, FakeClock, incoming
from tests.integration.bot_harness import RecordingSession, callback, feed
from tests.integration.helpers import ADMIN_ID, CLIENT, setup_chat, tickets

pytestmark = pytest.mark.integration

GROUP = Chat(id=CHAT_ID, type="supergroup", title="Поддержка VIP")
ADMIN_TG = User(id=ADMIN_ID, is_bot=False, first_name="Admin")
STRANGER_TG = User(id=9999, is_bot=False, first_name="Stranger")
CLIENT_TG = User(id=CLIENT.telegram_user_id, is_bot=False, first_name="Client")
NOTIFY = Chat(id=-100777, type="supergroup", title="Escalations")


@pytest.fixture
def tg() -> RecordingSession:
    return RecordingSession()


@pytest.fixture
def bot(tg: RecordingSession) -> Bot:
    return Bot("42:TEST", session=tg)


def press(
    sender: User,
    pending_reply_id: int,
    markup: InlineKeyboardMarkup,
    row: int = 0,
    text: str = "🔴 SLA нарушен\n\nТекст: <тест>",
) -> Update:
    data = markup.inline_keyboard[row][0].callback_data or ""
    return callback(NOTIFY, sender, data, text=text, markup=markup)


async def test_group_message_creates_ticket_via_dispatcher(
    bot: Bot,
    settings: Settings,
    services: BotServices,
    sync_factory: sessionmaker[Session],
) -> None:
    await setup_chat(services)
    msg = Message(message_id=10, date=T0, chat=GROUP, from_user=CLIENT_TG, text="Помогите")
    update = Update(update_id=424242, message=msg)
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
    markup = notification_keyboard(ticket.id)

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


async def test_reassign_ticket_buttons(
    bot: Bot,
    tg: RecordingSession,
    settings: Settings,
    services: BotServices,
    sync_factory: sessionmaker[Session],
    clock: FakeClock,
) -> None:
    await setup_chat(services)
    colleague = User(id=2001, is_bot=False, first_name="Colleague")
    await services.chats.add_responder(CHAT_ID, user_data(colleague), ADMIN_ID)
    await services.messages.process(incoming(10, CLIENT, date=clock.now()))
    [ticket] = tickets(sync_factory)
    main = notification_keyboard(ticket.id)
    text = "🔴 SLA нарушен\n\nОтветственный: Responder2000\nТекст: x"

    await feed(bot, settings, services, press(STRANGER_TG, ticket.id, main, row=1))
    assert "Недостаточно прав" in (tg.of(AnswerCallbackQuery)[-1].text or "")

    await feed(bot, settings, services, press(ADMIN_TG, ticket.id, main, row=1))
    [menu] = tg.of(EditMessageReplyMarkup)
    assert menu.reply_markup is not None
    buttons = [row[0] for row in menu.reply_markup.inline_keyboard]
    assert [b.callback_data for b in buttons] == [f"ra:st:{ticket.id}:2001", f"ra:bk:{ticket.id}:0"]

    await feed(bot, settings, services, press(ADMIN_TG, ticket.id, menu.reply_markup, text=text))
    [edit] = tg.of(EditMessageText)
    assert 'Ответственный: <a href="tg://user?id=2001">Colleague</a>' in edit.text
    assert "🔁 Ответственный по сообщению:" in edit.text
    assert edit.reply_markup == main  # main keyboard restored
    assert tickets(sync_factory)[0].responsible_telegram_id_snapshot == 2001


async def test_reassign_chat_button(
    bot: Bot,
    tg: RecordingSession,
    settings: Settings,
    services: BotServices,
    sync_factory: sessionmaker[Session],
    clock: FakeClock,
) -> None:
    await setup_chat(services)
    colleague = User(id=2001, is_bot=False, first_name="Colleague")
    await services.chats.add_responder(CHAT_ID, user_data(colleague), ADMIN_ID)
    await services.messages.process(incoming(10, CLIENT, date=clock.now()))
    [ticket] = tickets(sync_factory)
    main = notification_keyboard(ticket.id)

    await feed(bot, settings, services, press(ADMIN_TG, ticket.id, main, row=2))
    menu = tg.of(EditMessageReplyMarkup)[-1].reply_markup
    assert menu.inline_keyboard[0][0].callback_data == f"ra:sc:{ticket.id}:2001"
    await feed(bot, settings, services, press(ADMIN_TG, ticket.id, menu))
    [edit] = tg.of(EditMessageText)
    assert "👥 Ответственный чата:" in edit.text
    assert "для новых сообщений" in edit.text
    details = await services.chats.get_details(CHAT_ID)
    assert details is not None and details.responsible is not None
    assert details.responsible.telegram_user_id == 2001
    assert tickets(sync_factory)[0].responsible_telegram_id_snapshot == 2000

    # "Back" restores the main keyboard.
    await feed(bot, settings, services, press(ADMIN_TG, ticket.id, menu, row=1))
    assert tg.of(EditMessageReplyMarkup)[-1].reply_markup == main
