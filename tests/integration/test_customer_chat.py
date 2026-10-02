"""Customer chat: only /chat_add by an admin; nothing else is visible to the customer."""

from __future__ import annotations

import pytest
from aiogram import Bot
from aiogram.methods import DeleteMessage, EditMessageText, SendMessage
from aiogram.types import Chat, User

from app.bot.extractors import user_data
from app.bot.routers.customer_chat import GROUP_HINT
from app.bot.services import BotServices
from app.core.config import Settings
from app.core.enums import ChatType
from tests.factories import CHAT_ID
from tests.integration.bot_harness import RecordingSession, feed, message, reset_fsm
from tests.integration.helpers import ADMIN_CHAT_ID, ADMIN_ID

pytestmark = pytest.mark.integration

GROUP = Chat(id=CHAT_ID, type="supergroup", title="ООО Заказчик")
ADMIN_TG = User(id=ADMIN_ID, is_bot=False, first_name="Admin")
SECOND_ADMIN = User(id=1001, is_bot=False, first_name="Second", username="second")
CUSTOMER = User(id=3000, is_bot=False, first_name="Customer")


@pytest.fixture
def tg() -> RecordingSession:
    reset_fsm()
    return RecordingSession()


@pytest.fixture
def bot(tg: RecordingSession) -> Bot:
    return Bot("42:TEST", session=tg)


async def test_chat_add_sends_card_privately_and_keeps_group_clean(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices
) -> None:
    await services.admins.add(user_data(SECOND_ADMIN), ADMIN_ID)
    await feed(bot, settings, services, message(GROUP, ADMIN_TG, "/chat_add"))

    [deleted] = tg.of(DeleteMessage)
    assert deleted.chat_id == CHAT_ID
    sent = tg.of(SendMessage)
    assert {m.chat_id for m in sent} == {ADMIN_ID, SECOND_ADMIN.id}  # nothing to the group
    card = sent[0].text
    assert "Новый чат подключён" in card
    assert "ООО Заказчик" in card
    assert "Ответственный: <i>не задано</i>" in card
    assert "Чат уведомлений: <i>не задано</i>" in card
    chats = await services.chats.list_chats()
    assert [c.title for c in chats] == ["ООО Заказчик"]


async def test_admin_without_private_chat_gets_hint_in_group(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices
) -> None:
    tg.forbidden_chats.add(ADMIN_ID)
    await feed(bot, settings, services, message(GROUP, ADMIN_TG, "/chat_add"))
    [hint] = tg.of(SendMessage)[-1:]
    assert (hint.chat_id, hint.text) == (CHAT_ID, GROUP_HINT)


async def test_repeated_chat_add_does_not_duplicate(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices
) -> None:
    await feed(bot, settings, services, message(GROUP, ADMIN_TG, "/chat_add"))
    await feed(bot, settings, services, message(GROUP, ADMIN_TG, "/chat_add"))
    assert "уже подключён" in tg.of(SendMessage)[-1].text
    assert len(await services.chats.list_chats()) == 1


async def test_non_admin_and_other_commands_are_silently_ignored(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices
) -> None:
    await feed(bot, settings, services, message(GROUP, CUSTOMER, "/chat_add"))
    assert await services.chats.list_chats() == []
    await services.chats.add_chat(CHAT_ID, "ООО Заказчик", ChatType.SUPERGROUP, ADMIN_ID)
    for text in ("/menu", "/start", "/help", "/stats", "/chat_sla 60", "/cancel", "/notify_here"):
        await feed(bot, settings, services, message(GROUP, ADMIN_TG, text))
    assert tg.of(SendMessage) == []
    assert tg.of(EditMessageText) == []


async def test_chat_add_refused_in_admin_chat(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices
) -> None:
    admin_chat = Chat(id=ADMIN_CHAT_ID, type="supergroup", title="Админы")
    await feed(bot, settings, services, message(admin_chat, ADMIN_TG, "/chat_add"))
    assert await services.chats.list_chats() == []
    assert "группе заказчика" in tg.of(SendMessage)[-1].text
