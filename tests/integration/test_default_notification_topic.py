"""Default notification forum: every new chat gets its own notification topic there."""

from __future__ import annotations

import pytest
from aiogram import Bot
from aiogram.methods import CreateForumTopic, SendMessage
from aiogram.types import Chat, ChatShared, ReplyKeyboardMarkup, User

from app.bot.services import BotServices
from app.core.config import Settings
from app.core.enums import ChatType
from app.services.bot_settings import DefaultNotificationTarget
from tests.factories import CHAT_ID
from tests.integration.bot_harness import (
    RecordingSession,
    feed,
    message,
    reset_fsm,
    visible_chat,
)
from tests.integration.helpers import ADMIN_CHAT_ID, ADMIN_ID
from tests.integration.test_settings_menu import ADMIN_CHAT, Nav

pytestmark = pytest.mark.integration

ADMIN_TG = User(id=ADMIN_ID, is_bot=False, first_name="Admin", username="boss")
GROUP = Chat(id=CHAT_ID, type="supergroup", title="ООО Заказчик")
NOTIFY_FORUM = -100900


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


@pytest.fixture
async def default_forum(tg: RecordingSession, services: BotServices) -> int:
    tg.chats[NOTIFY_FORUM] = visible_chat(NOTIFY_FORUM, "SLA-уведомления", forum=True)
    tg.topic_admin_chats.add(NOTIFY_FORUM)
    await services.bot_settings.set_default_notification(
        DefaultNotificationTarget(chat_id=NOTIFY_FORUM, title="SLA-уведомления"), ADMIN_ID
    )
    return NOTIFY_FORUM


async def chat_add(bot: Bot, settings: Settings, services: BotServices, chat: Chat = GROUP) -> None:
    await feed(bot, settings, services, message(chat, ADMIN_TG, "/chat_add"))


# --- new chats ----------------------------------------------------------------------


async def test_new_chat_gets_its_own_topic(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices, default_forum: int
) -> None:
    await chat_add(bot, settings, services)
    [create] = tg.of(CreateForumTopic)
    assert (int(create.chat_id), create.name) == (default_forum, "ООО Заказчик")
    [chat] = await services.chats.list_chats()
    assert (chat.notification_chat_id, chat.notification_thread_id) == (
        default_forum,
        tg.next_topic_id,
    )
    assert await services.chats.topic_name(default_forum, tg.next_topic_id) == "ООО Заказчик"
    card = tg.of(SendMessage)[-1]
    assert int(card.chat_id) == ADMIN_ID
    assert "новый топик «ООО Заказчик» в «SLA-уведомления»" in card.text


async def test_readding_a_chat_does_not_create_another_topic(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices, default_forum: int
) -> None:
    await chat_add(bot, settings, services)
    await chat_add(bot, settings, services)
    assert len(tg.of(CreateForumTopic)) == 1


async def test_topic_of_a_forum_chat_is_named_group_slash_topic(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices, default_forum: int
) -> None:
    await services.chats.add_chat(
        CHAT_ID, "Клиенты", ChatType.SUPERGROUP, ADMIN_ID, thread_id=42, topic_name="ООО Альфа"
    )  # pre-existing: no topic for it
    forum = Chat(id=-100333, type="supergroup", title="Клиенты 2", is_forum=True)
    await feed(
        bot,
        settings,
        services,
        message(forum, ADMIN_TG, "/chat_add", message_thread_id=7, is_topic_message=True),
    )
    [create] = tg.of(CreateForumTopic)
    assert create.name.startswith("Клиенты 2 / ")


async def test_without_topic_rights_notifications_go_to_general_chat(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices, default_forum: int
) -> None:
    tg.topic_admin_chats.clear()
    await chat_add(bot, settings, services)
    [chat] = await services.chats.list_chats()
    assert (chat.notification_chat_id, chat.notification_thread_id) == (default_forum, None)
    assert "Не удалось создать топик" in tg.of(SendMessage)[-1].text


async def test_no_default_forum_leaves_notifications_unset(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices
) -> None:
    await chat_add(bot, settings, services)
    assert tg.of(CreateForumTopic) == []
    [chat] = await services.chats.list_chats()
    assert chat.notification_chat_id is None


async def test_chat_added_from_menu_gets_a_topic(
    nav: Nav, tg: RecordingSession, services: BotServices, default_forum: int
) -> None:
    tg.chats[-100555] = visible_chat(-100555, "ООО Гамма")
    await nav.send("/menu")
    await nav.press("Чаты")
    await nav.press("Подключить чат")
    await nav.send(None, chat_shared=ChatShared(request_id=3, chat_id=-100555, title="ООО Гамма"))
    assert nav.text.startswith("✅ Чат подключён к мониторингу.")
    assert "новый топик «ООО Гамма»" in nav.text
    [chat] = await services.chats.list_chats()
    assert chat.notification_chat_id == default_forum
    assert chat.notification_thread_id == tg.next_topic_id


# --- settings menu ------------------------------------------------------------------


async def test_pick_default_forum_in_private_menu(
    nav: Nav, tg: RecordingSession, services: BotServices
) -> None:
    tg.chats[NOTIFY_FORUM] = visible_chat(NOTIFY_FORUM, "SLA-уведомления", forum=True)
    await nav.send("/menu")
    await nav.press("Уведомления по умолчанию")
    assert "не задано" in nav.text
    await nav.press("Выбрать группу")
    picker = [m for m in tg.of(SendMessage) if isinstance(m.reply_markup, ReplyKeyboardMarkup)]
    request = picker[-1].reply_markup.keyboard[0][0].request_chat
    assert request.chat_is_forum and request.request_id == 4
    await nav.send(
        None, chat_shared=ChatShared(request_id=4, chat_id=NOTIFY_FORUM, title="SLA-уведомления")
    )
    assert "✅ Группа уведомлений по умолчанию: «SLA-уведомления»" in nav.text
    assert "Управление темами" in nav.text  # the bot is a plain member: warn
    assert await services.bot_settings.default_notification() == DefaultNotificationTarget(
        NOTIFY_FORUM, "SLA-уведомления"
    )

    await nav.press("Отключить")
    assert await services.bot_settings.default_notification() is None
    assert "не задано" in nav.text


async def test_manual_input_rejects_group_without_topics(
    nav: Nav, tg: RecordingSession, services: BotServices
) -> None:
    tg.chats[-100901] = visible_chat(-100901, "Обычная группа")
    await nav.send("/menu")
    await nav.press("Уведомления по умолчанию")
    await nav.press("Указать chat_id")
    await nav.send("-100901")
    assert "не включены темы" in nav.text
    assert await services.bot_settings.default_notification() is None


async def test_customer_chat_cannot_be_the_default_forum(
    nav: Nav, tg: RecordingSession, services: BotServices
) -> None:
    tg.chats[CHAT_ID] = visible_chat(CHAT_ID, "Клиенты", forum=True)
    await services.chats.add_chat(CHAT_ID, "Клиенты", ChatType.SUPERGROUP, ADMIN_ID)
    await nav.send("/menu")
    await nav.press("Уведомления по умолчанию")
    await nav.press("Указать chat_id")
    await nav.send(str(CHAT_ID))
    assert "Это чат заказчика" in nav.text
    assert await services.bot_settings.default_notification() is None


async def test_use_admin_chat_as_default_forum(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices
) -> None:
    tg.chats[ADMIN_CHAT_ID] = visible_chat(ADMIN_CHAT_ID, "Админы", forum=True)
    tg.topic_admin_chats.add(ADMIN_CHAT_ID)
    nav = Nav(bot, tg, settings, services, chat=ADMIN_CHAT)
    await nav.send("/menu")
    await nav.press("Уведомления по умолчанию")
    await nav.press("Использовать эту группу")
    assert "✅ Группа уведомлений по умолчанию: «Админы»" in nav.text
    assert "Управление темами" not in nav.text.split("Группа должна")[0]
    target = await services.bot_settings.default_notification()
    assert target is not None and target.chat_id == ADMIN_CHAT_ID
