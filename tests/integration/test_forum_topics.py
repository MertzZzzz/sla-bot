"""A customer chat can be a single forum topic; chats are picked natively in the menu."""

from __future__ import annotations

from typing import Any

import pytest
from aiogram import Bot
from aiogram.methods import SendMessage
from aiogram.types import Chat, ChatShared, ForumTopicCreated, Message, ReplyKeyboardMarkup, User
from sqlalchemy.orm import Session, sessionmaker

from app.bot.services import BotServices
from app.core.config import Settings
from app.core.enums import ChatType
from app.schemas.chats import MonitoredChatUpdate
from tests.factories import CHAT_ID, T0, incoming
from tests.integration.bot_harness import (
    RecordingSession,
    feed,
    message,
    reset_fsm,
    visible_chat,
)
from tests.integration.helpers import ADMIN_ID, CLIENT, tickets
from tests.integration.test_settings_menu import Nav

pytestmark = pytest.mark.integration

ADMIN_TG = User(id=ADMIN_ID, is_bot=False, first_name="Admin", username="boss")
FORUM = Chat(id=CHAT_ID, type="supergroup", title="Клиенты", is_forum=True)
TOPIC_A, TOPIC_B = 100, 200


def in_topic(thread: int, name: str) -> dict[str, Any]:
    """Message kwargs of a plain message inside a forum topic."""
    root = Message(
        message_id=thread,
        date=T0,
        chat=FORUM,
        forum_topic_created=ForumTopicCreated(name=name, icon_color=0),
        message_thread_id=thread,
        is_topic_message=True,
    )
    return {"message_thread_id": thread, "is_topic_message": True, "reply_to_message": root}


@pytest.fixture
def tg() -> RecordingSession:
    reset_fsm()
    return RecordingSession()


@pytest.fixture
def bot(tg: RecordingSession) -> Bot:
    return Bot("42:TEST", session=tg)


@pytest.fixture
def nav(bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices) -> Nav:
    return Nav(bot, tg, settings, services)


def topic_msg(message_id: int, thread: int | None, text: str = "Вопрос") -> Any:
    return incoming(
        message_id,
        CLIENT,
        text=text,
        thread_id=thread,
        is_topic=thread is not None,
        reply_to=thread,
        title="Клиенты",
    )


async def test_messages_are_routed_to_topic_or_group(
    services: BotServices, sync_factory: sessionmaker[Session]
) -> None:
    _, topic = await services.chats.add_chat(
        CHAT_ID, "Клиенты", ChatType.SUPERGROUP, ADMIN_ID, thread_id=TOPIC_A, topic_name="ООО Альфа"
    )
    assert topic.title == "Клиенты / ООО Альфа"
    await services.messages.process(topic_msg(10, TOPIC_A))
    other = await services.messages.process(topic_msg(11, TOPIC_B))
    assert other.action == "ignored"  # only topic A is monitored

    _, group = await services.chats.add_chat(CHAT_ID, "Клиенты", ChatType.SUPERGROUP, ADMIN_ID)
    await services.messages.process(topic_msg(12, TOPIC_B))  # falls back to the whole group
    await services.messages.process(topic_msg(13, None))  # "General" topic
    by_chat = {t.source_message_id: t.chat_id for t in tickets(sync_factory)}
    assert by_chat == {10: topic.id, 12: group.id, 13: group.id}

    # Settings are per topic: changing the topic does not touch the group.
    await services.chats.update(
        CHAT_ID, MonitoredChatUpdate(sla_seconds=60), ADMIN_ID, thread_id=TOPIC_A
    )
    topic_details = await services.chats.get_details(CHAT_ID, thread_id=TOPIC_A)
    group_details = await services.chats.get_details(CHAT_ID)
    assert topic_details is not None and topic_details.chat.sla_seconds == 60
    assert group_details is not None and group_details.chat.sla_seconds == 900
    # The group title in messages must not overwrite the topic chat title.
    assert topic_details.chat.title == "Клиенты / ООО Альфа"


async def test_topic_rename_updates_title(services: BotServices) -> None:
    await services.chats.add_chat(
        CHAT_ID, "Клиенты", ChatType.SUPERGROUP, ADMIN_ID, thread_id=TOPIC_A, topic_name="Альфа"
    )
    await services.chats.remember_topic(CHAT_ID, TOPIC_A, "Альфа (VIP)")
    details = await services.chats.get_details(CHAT_ID, thread_id=TOPIC_A)
    assert details is not None
    assert (details.chat.title, details.chat.topic_name) == ("Клиенты / Альфа (VIP)", "Альфа (VIP)")


async def test_chat_add_inside_topic(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices
) -> None:
    tg.chats[CHAT_ID] = visible_chat(CHAT_ID, "Клиенты", forum=True)
    await feed(
        bot,
        settings,
        services,
        message(FORUM, ADMIN_TG, "/chat_add", **in_topic(TOPIC_A, "ООО Альфа")),
    )
    [chat] = await services.chats.list_chats()
    assert (chat.thread_id, chat.topic_name, chat.title) == (
        TOPIC_A,
        "ООО Альфа",
        "Клиенты / ООО Альфа",
    )
    assert chat.chat_link == f"https://t.me/c/1234567890/{TOPIC_A}"  # opens the topic
    card = next(m for m in tg.of(SendMessage) if m.chat_id == ADMIN_ID).text
    assert f"Тема форума: ООО Альфа (<code>{TOPIC_A}</code>)" in card
    # /chat_add in another topic adds a second, independent customer chat.
    await feed(
        bot,
        settings,
        services,
        message(FORUM, ADMIN_TG, "/chat_add", **in_topic(TOPIC_B, "ООО Бета")),
    )
    assert sorted(c.title for c in await services.chats.list_chats()) == [
        "Клиенты / ООО Альфа",
        "Клиенты / ООО Бета",
    ]


async def test_topic_names_are_learned_from_messages(
    bot: Bot, settings: Settings, services: BotServices
) -> None:
    client = User(id=3000, is_bot=False, first_name="Client")
    await feed(
        bot, settings, services, message(FORUM, client, "привет", **in_topic(TOPIC_A, "Альфа"))
    )
    await feed(
        bot, settings, services, message(FORUM, client, "привет", **in_topic(TOPIC_B, "Бета"))
    )
    assert await services.chats.topics(CHAT_ID) == [(TOPIC_A, "Альфа"), (TOPIC_B, "Бета")]


async def test_native_pick_plain_group(
    nav: Nav, tg: RecordingSession, services: BotServices
) -> None:
    tg.chats[-100555] = visible_chat(-100555, "ООО Гамма")
    await nav.send("/menu")
    await nav.press("Чаты")
    await nav.press("Подключить чат")
    picker = [m for m in tg.of(SendMessage) if isinstance(m.reply_markup, ReplyKeyboardMarkup)]
    request = picker[-1].reply_markup.keyboard[0][0].request_chat
    assert request.bot_is_member and request.request_id == 3
    await nav.send(None, chat_shared=ChatShared(request_id=3, chat_id=-100555, title="ООО Гамма"))
    assert nav.text.startswith("✅ Чат подключён к мониторингу.")
    assert [c.title for c in await services.chats.list_chats()] == ["ООО Гамма"]


async def test_native_pick_forum_then_topic(
    nav: Nav, tg: RecordingSession, services: BotServices
) -> None:
    tg.chats[CHAT_ID] = visible_chat(CHAT_ID, "Клиенты", forum=True)
    await services.chats.remember_topic(CHAT_ID, TOPIC_A, "ООО Альфа")
    await services.chats.remember_topic(CHAT_ID, TOPIC_B, "ООО Бета")
    await nav.send("/menu")
    await nav.press("Чаты")
    await nav.press("Подключить чат")
    await nav.send(None, chat_shared=ChatShared(request_id=3, chat_id=CHAT_ID, title="Клиенты"))
    assert "форум" in nav.text
    assert {"Весь чат (все темы)", "# ООО Альфа", "# ООО Бета"} <= set(nav.buttons())
    await nav.press("# ООО Бета")
    assert nav.text.startswith("✅ Чат подключён к мониторингу.")
    [chat] = await services.chats.list_chats()
    assert (chat.thread_id, chat.title) == (TOPIC_B, "Клиенты / ООО Бета")

    # Picking the forum again shows what is already connected.
    await nav.press("« Чаты")
    await nav.press("Подключить чат")
    await nav.send(None, chat_shared=ChatShared(request_id=3, chat_id=CHAT_ID, title="Клиенты"))
    assert "✓ # ООО Бета" in nav.buttons()


async def test_native_pick_rejects_unreachable_chat(nav: Nav, services: BotServices) -> None:
    await nav.send("/menu")
    await nav.press("Чаты")
    await nav.press("Подключить чат")
    await nav.send(None, chat_shared=ChatShared(request_id=3, chat_id=-100777, title="X"))
    assert "Бот не видит чат" in nav.text
    assert await services.chats.list_chats() == []


async def test_staff_topic_of_customer_forum_can_receive_notifications(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices
) -> None:
    await services.chats.add_chat(
        CHAT_ID, "Клиенты", ChatType.SUPERGROUP, ADMIN_ID, thread_id=TOPIC_A, topic_name="ООО Альфа"
    )
    staff = Nav(bot, tg, settings, services, FORUM, **in_topic(300, "Эскалации"))
    await staff.send("/notify_here", **in_topic(300, "Эскалации"))
    await staff.press("ООО Альфа")
    details = await services.chats.get_details(CHAT_ID, thread_id=TOPIC_A)
    assert details is not None
    assert (details.chat.notification_chat_id, details.chat.notification_thread_id) == (
        CHAT_ID,
        300,
    )

    sent = len(tg.of(SendMessage))
    customer_topic = Nav(bot, tg, settings, services, FORUM, **in_topic(TOPIC_A, "ООО Альфа"))
    await customer_topic.send("/notify_here", **in_topic(TOPIC_A, "ООО Альфа"))
    assert len(tg.of(SendMessage)) == sent  # silent inside the customer's topic


async def test_group_migration_moves_topic_chats(services: BotServices) -> None:
    await services.chats.add_chat(-555, "Клиенты", ChatType.GROUP, ADMIN_ID)
    await services.chats.migrate_chat(-555, CHAT_ID)
    assert [c.telegram_chat_id for c in await services.chats.list_chats()] == [CHAT_ID]
